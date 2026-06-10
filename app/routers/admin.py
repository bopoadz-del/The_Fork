"""Admin-gated diagnostic endpoints — mounted in ALL environments.

These were originally in app/routers/debug.py but that router is only
mounted in development. The doc-extract / doc-reindex diagnostics are
the only way to inspect the production index without shell access, so
they need to live somewhere that the production app actually loads.

Admin gating via ``_require_admin`` is the only security boundary; the
endpoints never run unauthenticated and never run for non-admin users.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import require_api_key

router = APIRouter()


def _require_admin(auth: Dict[str, Any]) -> None:
    if auth.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/v1/admin/debug/doc-extract")
def admin_doc_extract(
    project_id: str = Query(...),
    document_id: str = Query(...),
    re_extract: bool = Query(False, description="Run fresh extraction (don't read index)"),
    auth: dict = Depends(require_api_key),
):
    """Diagnostic report on what extraction produced for ``document_id``.

    Returns metadata, page count, text-layer character counts, indexed chunk
    counts + previews, and (when ``re_extract=true``) a fresh extraction so the
    caller can compare against what the index stored.
    """
    _require_admin(auth)

    from app.core import projects as _projects
    from app.core import doc_index as _doc_index

    doc = _projects.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    if doc.get("project_id") != project_id:
        raise HTTPException(status_code=400, detail="document does not belong to project")

    filename = doc.get("original_name", "")
    file_path = doc.get("file_path") or ""
    size = doc.get("size")
    ext = os.path.splitext(filename.lower())[1] if filename else ""

    response: Dict[str, Any] = {
        "document_id": document_id,
        "project_id": project_id,
        "filename": filename,
        "ext": ext,
        "size_bytes": size,
        "file_exists": bool(file_path and os.path.exists(file_path)),
    }

    if ext == ".pdf" and response["file_exists"]:
        try:
            import fitz  # PyMuPDF
            from app.core import file_crypto
            with file_crypto.open_plaintext(file_path) as readable_path:
                pdf = fitz.open(readable_path)
                response["pdf_page_count"] = pdf.page_count
                page_chars: List[int] = []
                for i, page in enumerate(pdf):
                    if i >= 10:
                        break
                    page_chars.append(len(page.get_text()))
                response["pdf_first_pages_chars"] = page_chars
                pdf.close()
        except Exception as exc:
            response["pdf_error"] = str(exc)

    # Indexed chunks (what RAG sees today).
    index = _doc_index._load_index(project_id)  # noqa: SLF001 — diagnostic only
    chunks: List[str] = []
    if index and isinstance(index.get("documents"), list):
        for entry in index["documents"]:
            if entry.get("document_id") == document_id:
                chunks = list(entry.get("chunks", []))
                if entry.get("ocr_low_quality"):
                    response["ocr_low_quality"] = True
                break

    response["indexed_chunk_count"] = len(chunks)
    response["indexed_chunks_avg_chars"] = (
        sum(len(c) for c in chunks) // len(chunks) if chunks else 0
    )
    response["indexed_chunks_preview"] = [
        {"i": i, "chars": len(c), "snippet": c[:200]}
        for i, c in enumerate(chunks[:3])
    ]

    if re_extract and response["file_exists"]:
        try:
            text, meta = _doc_index._extract_with_meta(file_path, filename)  # noqa: SLF001
            fresh_chunks = _doc_index.chunk_text(text)
            response["fresh_extraction"] = {
                "total_chars": len(text),
                "chunk_count": len(fresh_chunks),
                "avg_chars": sum(len(c) for c in fresh_chunks) // len(fresh_chunks) if fresh_chunks else 0,
                "meta": meta,
                "first_chunk_snippet": fresh_chunks[0][:200] if fresh_chunks else "",
            }
        except Exception as exc:
            response["fresh_extraction_error"] = str(exc)

    return response


@router.post("/v1/admin/debug/doc-reindex")
def admin_doc_reindex(
    project_id: str = Query(...),
    document_id: str = Query(...),
    chunker: str = Query("default", pattern="^(default|finer)$"),
    auth: dict = Depends(require_api_key),
):
    """Re-run extraction + chunking + RAG indexing for a single document.

    ``chunker=finer`` uses the BOQ-aware char-level chunker (500-char target,
    50-char overlap, BOQ row boundaries preferred). Use it for BOQ / tender
    PDFs where the default 500-word chunker produces too-coarse chunks.
    """
    _require_admin(auth)
    from app.core import doc_index as _doc_index
    return _doc_index.index_document(project_id, document_id, chunker=chunker)


@router.post("/v1/admin/debug/project-reindex")
def admin_project_reindex(
    project_id: str = Query(...),
    auth: dict = Depends(require_api_key),
):
    """Full rebuild of a project's doc_index from its current document list.

    Use this to clean up orphans left behind by deletes that pre-dated the
    auto-prune-on-delete fix, or to apply a chunker change to all docs at
    once. Slow — extracts text from every document.
    """
    _require_admin(auth)
    from app.core import doc_index as _doc_index
    return _doc_index.index_project(project_id)


# ── Training scenario generation (Task 1.4 / MEGA-2) ───────────────────────

@router.post("/v1/admin/training/generate-scenarios")
async def admin_generate_training_scenarios(
    project_id: str = Query(...),
    questions_per_chunk: int = Query(3, ge=1, le=20),
    min_chunk_chars: int = Query(150, ge=50, le=2000),
    max_chunks: int = Query(200, ge=1, le=2000),
    provider_hint: str = Query("any"),
    auth: dict = Depends(require_api_key),
):
    """Run the synthetic Q&A generator against a project's indexed chunks.

    Iterates the project's doc_index, sends each chunk to the chat block,
    parses + filters the JSONL output, dedupes/validates, and writes the
    result to ``${DATA_DIR}/learning/training_scenarios_<ts>.jsonl``.

    Slow — up to ~5 seconds per chunk through the LLM. The frontend caller
    should set a long fetch timeout (15-30 minutes for a full project).
    """
    _require_admin(auth)

    import asyncio
    import json
    import os
    import time as _time

    from scripts.generate_training_scenarios import (
        iter_chunks_for_project,
        _generate_for_chunk,
        _validate_scenarios,
    )

    chunks = list(iter_chunks_for_project(
        project_id, min_chars=min_chunk_chars, max_chunks=max_chunks
    ))
    if not chunks:
        raise HTTPException(
            status_code=404,
            detail=f"no chunks in doc_index for project {project_id}",
        )

    rows: list = []
    skipped_chunks = 0
    per_chunk_timeout = 60.0  # never wait more than 60s on a single chunk

    for chunk in chunks:
        try:
            pairs = await asyncio.wait_for(
                _generate_for_chunk(chunk, questions_per_chunk, provider_hint),
                timeout=per_chunk_timeout,
            )
        except asyncio.TimeoutError:
            skipped_chunks += 1
            continue
        except Exception:  # noqa: BLE001 — never crash on one bad chunk
            skipped_chunks += 1
            continue
        if not pairs:
            skipped_chunks += 1
            continue
        rows.extend(pairs)

    kept_rows, validation_report = _validate_scenarios(rows)

    data_dir = os.getenv("DATA_DIR", "data")
    out_dir = os.path.join(data_dir, "learning")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(
        out_dir,
        f"training_scenarios_{project_id}_{int(_time.time())}.jsonl",
    )
    with open(out_path, "w", encoding="utf-8") as f:
        for r in kept_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_doc: dict = {}
    for r in kept_rows:
        src = r.get("source") or "?"
        by_doc[src] = by_doc.get(src, 0) + 1
    top_sources = sorted(by_doc.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return {
        "project_id": project_id,
        "chunks_processed": len(chunks),
        "chunks_skipped": skipped_chunks,
        "rows_generated": len(rows),
        "rows_kept": len(kept_rows),
        "validation": validation_report,
        "top_sources": top_sources,
        "output_path": out_path,
        "sample": kept_rows[:3],
    }
