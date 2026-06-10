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
    auth: dict = Depends(require_api_key),
):
    """Re-run extraction + chunking + RAG indexing for a single document."""
    _require_admin(auth)
    from app.core import doc_index as _doc_index
    return _doc_index.index_document(project_id, document_id)
