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


@router.get("/v1/admin/training/list")
def admin_training_list(auth: dict = Depends(require_api_key)):
    """List all training-scenario JSONL files on the server."""
    _require_admin(auth)
    data_dir = os.getenv("DATA_DIR", "data")
    learn_dir = os.path.join(data_dir, "learning")
    if not os.path.isdir(learn_dir):
        return {"files": []}
    out = []
    for name in sorted(os.listdir(learn_dir)):
        path = os.path.join(learn_dir, name)
        if name.endswith(".jsonl") and os.path.isfile(path):
            line_count = 0
            try:
                with open(path, "r", encoding="utf-8") as f:
                    line_count = sum(1 for _ in f)
            except Exception:
                pass
            out.append({
                "name": name,
                "size_bytes": os.path.getsize(path),
                "line_count": line_count,
            })
    return {"files": out}


@router.get("/v1/admin/training/download")
def admin_training_download(
    filename: str = Query(..., description="Filename inside DATA_DIR/learning/"),
    auth: dict = Depends(require_api_key),
):
    """Stream a training-scenario JSONL back to the caller. Sandboxed to
    DATA_DIR/learning so an attacker can't traverse to other paths."""
    _require_admin(auth)
    import os as _os
    # Path-traversal guard: filename must be a basename, no slashes.
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "filename must be a plain basename")
    data_dir = _os.getenv("DATA_DIR", "data")
    full = _os.path.join(data_dir, "learning", filename)
    if not _os.path.isfile(full):
        raise HTTPException(404, "file not found")
    with open(full, "r", encoding="utf-8") as f:
        body = f.read()
    return {"filename": filename, "line_count": body.count("\n"),
            "size_bytes": len(body), "content": body}


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
#
# Long-running generation jobs are now ASYNC: POST returns a job_id
# immediately, the actual work runs in an asyncio background task, and the
# client polls GET /v1/admin/training/job/{job_id}. This decouples the
# 10-20 minute generator from any client-side connection (bridge, curl
# pipe) that might drop mid-flight.

# In-memory job registry. Process-local; if uvicorn restarts mid-job, the
# job is lost and the client will see a 404 on next poll — that's the
# correct signal for "kick a new one off". Persistent job state would
# need its own table; not worth it for an admin-only diagnostic tool.
_TRAINING_JOBS: Dict[str, Dict[str, Any]] = {}


async def _training_job_runner(
    job_id: str,
    project_id: str,
    questions_per_chunk: int,
    min_chunk_chars: int,
    max_chunks: int,
    provider_hint: str,
) -> None:
    """Background task: runs the full generator, updates job state."""
    import asyncio
    import json
    import os
    import time as _time

    from scripts.generate_training_scenarios import (
        iter_chunks_for_project,
        _generate_for_chunk,
        _validate_scenarios,
        _DEFAULT_PROMPT,
    )
    from app.blocks import BLOCK_REGISTRY

    job = _TRAINING_JOBS[job_id]
    try:
        chunks = list(iter_chunks_for_project(
            project_id, min_chars=min_chunk_chars, max_chunks=max_chunks
        ))
        if not chunks:
            job["status"] = "failed"
            job["error"] = f"no chunks in doc_index for project {project_id}"
            job["finished_at"] = _time.time()
            return
        job["total_chunks"] = len(chunks)

        rows: list = []
        skipped_chunks = 0
        skip_reasons: dict = {}
        per_chunk_timeout = 200.0

        for i, chunk in enumerate(chunks):
            reason = None
            try:
                pairs = await asyncio.wait_for(
                    _generate_for_chunk(chunk, questions_per_chunk, provider_hint),
                    timeout=per_chunk_timeout,
                )
            except asyncio.TimeoutError:
                pairs = []
                reason = "timeout"
            except Exception as exc:  # noqa: BLE001
                pairs = []
                reason = f"exc:{type(exc).__name__}"
            if not pairs:
                skipped_chunks += 1
                skip_reasons[reason or "no_pairs"] = skip_reasons.get(reason or "no_pairs", 0) + 1
            else:
                rows.extend(pairs)
            job["chunks_done"] = i + 1
            job["rows_generated"] = len(rows)
            job["chunks_skipped"] = skipped_chunks

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

        job["status"] = "done"
        job["output_path"] = out_path
        job["rows_kept"] = len(kept_rows)
        job["validation"] = validation_report
        job["skip_reasons"] = skip_reasons
        job["finished_at"] = _time.time()
    except Exception as exc:  # noqa: BLE001 — last-line safety
        job["status"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
        job["finished_at"] = _time.time()


@router.post("/v1/admin/training/generate-scenarios")
async def admin_generate_training_scenarios(
    project_id: str = Query(...),
    questions_per_chunk: int = Query(3, ge=1, le=20),
    min_chunk_chars: int = Query(150, ge=50, le=2000),
    max_chunks: int = Query(200, ge=1, le=2000),
    provider_hint: str = Query("any"),
    auth: dict = Depends(require_api_key),
):
    """Kick off a Q&A generation job in the background. Returns a job_id
    for polling via GET /v1/admin/training/job/{job_id}.

    The job runs server-side and survives client disconnects — this is the
    reliability fix for the 10-20 minute generator (which used to time out
    bridge curl pipes mid-flight).
    """
    _require_admin(auth)

    import asyncio
    import time as _time
    import uuid as _uuid

    job_id = _uuid.uuid4().hex[:12]
    _TRAINING_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "project_id": project_id,
        "questions_per_chunk": questions_per_chunk,
        "min_chunk_chars": min_chunk_chars,
        "max_chunks": max_chunks,
        "provider_hint": provider_hint,
        "started_at": _time.time(),
        "finished_at": None,
        "chunks_done": 0,
        "total_chunks": None,
        "rows_generated": 0,
        "chunks_skipped": 0,
        "output_path": None,
        "rows_kept": None,
        "error": None,
    }
    asyncio.create_task(_training_job_runner(
        job_id, project_id, questions_per_chunk, min_chunk_chars,
        max_chunks, provider_hint,
    ))
    return {
        "job_id": job_id,
        "status_url": f"/v1/admin/training/job/{job_id}",
        "status": "running",
    }


@router.get("/v1/admin/training/job/{job_id}")
def admin_training_job_status(job_id: str, auth: dict = Depends(require_api_key)):
    """Poll status of a generate-scenarios job."""
    _require_admin(auth)
    job = _TRAINING_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, f"job '{job_id}' not found (worker may have restarted)")
    return job


# ── Synchronous (legacy) path — kept for tests / quick small jobs ────────

def _legacy_sync_generate_unused():
    """Stub kept to preserve the import surface for the deleted sync path —
    intentionally never called. Real entrypoint is the async job above."""
    return None


@router.post("/v1/admin/debug/migrate-sqlite")
def admin_migrate_sqlite(
    dry_run: bool = Query(True, description="Count rows only; no Postgres writes"),
    execute: bool = Query(False, description="Run idempotent migration (overrides dry_run)"),
    auth: dict = Depends(require_api_key),
):
    """Run SQLite→Postgres migration against the live DATA_DIR volume.

  One-off Render jobs cannot mount the service disk; this endpoint runs inside
  the web process so cutover dry-run / execute work on production.
    """
    _require_admin(auth)

    if execute:
        dry_run = False

    from pathlib import Path
    from sqlalchemy import create_engine

    from scripts.migrate_sqlite_to_pg import migrate

    data_dir = Path(os.getenv("DATA_DIR", "data")).resolve()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    if not data_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"DATA_DIR not found: {data_dir}")

    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        counts = migrate(engine, data_dir, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — operator diagnostic
        raise HTTPException(status_code=500, detail=f"migration failed: {exc}") from exc

    # Persist dry-run output for pilot-preflight when operators poll without re-running.
    if dry_run:
        log_path = data_dir / "pilot_dry_run.log"
        try:
            lines = [f"Migration summary (would migrate):"]
            for table, n in counts.items():
                lines.append(f"  {table}: {n}")
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    return {
        "dry_run": dry_run,
        "data_dir": str(data_dir),
        "counts": counts,
    }


@router.get("/v1/admin/debug/pilot-preflight")
def admin_pilot_preflight(auth: dict = Depends(require_api_key)):
    """Postgres schema + row-count snapshot for pilot cutover / re-index gates."""
    _require_admin(auth)

    import os
    from sqlalchemy import create_engine, text

    out: Dict[str, Any] = {
        "sentry_enabled": bool(os.getenv("SENTRY_DSN", "").strip()),
        "database_url_set": bool(os.getenv("DATABASE_URL", "").strip()),
        "data_dir": os.getenv("DATA_DIR", "data"),
    }

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        out["postgres"] = {"error": "DATABASE_URL unset"}
        return out

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            emb = conn.execute(
                text(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                    FROM pg_attribute a
                    JOIN pg_class t ON a.attrelid = t.oid
                    WHERE t.relname = 'chunks'
                      AND a.attname = 'embedding'
                      AND NOT a.attisdropped
                    """
                )
            ).scalar()
            counts = {}
            for table in (
                "users",
                "projects",
                "documents",
                "chunks",
                "conversations",
                "messages",
            ):
                counts[table] = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table}")
                ).scalar()
            out["postgres"] = {
                "chunks_embedding_type": emb,
                "embedding_dim_ok": emb == "vector(256)",
                "row_counts": counts,
            }
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        out["postgres"] = {"error": f"{type(exc).__name__}: {exc}"}

    data_dir = out["data_dir"]
    dry_log = os.path.join(data_dir, "pilot_dry_run.log")
    if os.path.isfile(dry_log):
        try:
            with open(dry_log, encoding="utf-8") as f:
                out["sqlite_dry_run_log"] = f.read()[-8000:]
        except Exception as exc:  # noqa: BLE001
            out["sqlite_dry_run_log_error"] = str(exc)

    return out


@router.post("/v1/admin/debug/sentry-smoke")
def admin_sentry_smoke(auth: dict = Depends(require_api_key)):
    """Raise a tagged test exception so Sentry capture can be verified pre-cutover."""
    _require_admin(auth)

    import os

    if not os.getenv("SENTRY_DSN", "").strip():
        raise HTTPException(
            status_code=503,
            detail="SENTRY_DSN not configured — set env and redeploy before smoke test",
        )

    import sentry_sdk

    event_id = sentry_sdk.capture_exception(
        RuntimeError("pilot sentry-smoke: intentional test exception (safe to ignore)")
    )
    return {
        "status": "captured",
        "event_id": event_id,
        "message": "Check Sentry Issues for pilot sentry-smoke event",
    }
