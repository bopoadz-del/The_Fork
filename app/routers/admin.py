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
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

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


@router.post("/v1/admin/projects/{project_id}/approve")
def admin_approve_project(project_id: str, auth: dict = Depends(require_api_key)):
    """Make an existing project admin-visible (shows in the sidebar).

    Flips ``is_approved=True`` + ``origin='admin_drive_approved'`` on a project
    that already exists (e.g. one created by uploading documents), without
    re-importing from Drive. Idempotent.
    """
    _require_admin(auth)
    from app.core import projects as _projects
    if not _projects.approve_project(project_id):
        raise HTTPException(404, f"Project '{project_id}' not found")
    return {"status": "approved", "project_id": project_id}


@router.get("/v1/admin/projects/archived")
def admin_list_archived_projects(auth: dict = Depends(require_api_key)):
    """List soft-archived (hidden) projects — so an admin can see what junk is
    eligible for a permanent purge. Read-only."""
    _require_admin(auth)
    from app.core.db import SessionLocal
    from app.core.models import Project
    out = []
    with SessionLocal() as s:
        for p in s.query(Project).filter(Project.status == "archived").all():
            out.append({"id": p.id, "name": p.name, "status": p.status})
    return {"archived": out, "count": len(out)}


@router.post("/v1/admin/projects/{project_id}/purge")
def admin_purge_archived_project(project_id: str,
                                 auth: dict = Depends(require_api_key)):
    """PERMANENTLY purge an ARCHIVED project (its RAG chunks + row).

    Tightly guarded: only works on already-archived projects, and refuses the
    master corpus / backing / general-knowledge ids — so live RAG can never be
    destroyed through here (the never-delete-RAG rule still holds).
    """
    _require_admin(auth)
    from app.core import projects as _projects
    result = _projects.purge_archived_project(project_id)
    if result == "purged":
        return {"status": "purged", "project_id": project_id}
    code = {"protected": 403, "not_found": 404, "not_archived": 409}.get(result, 400)
    raise HTTPException(code, f"cannot purge '{project_id}': {result}")


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


@router.get("/v1/admin/corpus/collections")
def admin_corpus_collections(
    folder_breakdown: bool = Query(
        True,
        description="Include top-folder breakdown for project_ids whose document "
                    "count exceeds folder_breakdown_min (default 50).",
    ),
    folder_breakdown_min: int = Query(
        50,
        ge=1,
        description="Minimum doc count before a project_id gets the folder breakdown.",
    ),
    auth: dict = Depends(require_api_key),
):
    """Per-project_id corpus inventory.

    For each project_id in the corpus, returns:
      * documents — count of rows in the `documents` table.
      * chunks    — count of rows in the `chunks` (RAG vector store) table.
      * by_top_folder — first '/'-segment of `original_name`, sorted by doc
        count desc. Only emitted for project_ids with >= folder_breakdown_min
        documents (avoids 200 single-doc tiles for ad-hoc projects).

    Read-only. Issues one COUNT(*) per project + one GROUP BY for folder
    breakdown — bounded by the number of distinct project_ids. Designed
    for the 70 GB drive_archive corpus where the operator needs to see
    what's actually in there grouped by source folder.

    Works on both Postgres production and SQLite dev / pilot.
    """
    _require_admin(auth)

    import os
    from sqlalchemy import create_engine, text

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        # SQLite fallback — match the app's default resolution
        data_dir = os.getenv("DATA_DIR", "data")
        db_path = os.path.join(data_dir, "the_fork.db")
        db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)
    collections: List[Dict[str, Any]] = []

    with engine.connect() as conn:
        # Distinct project_ids — union of documents + chunks tables. A
        # project_id can exist in chunks without documents (legacy
        # imports), so we collect both sides.
        try:
            project_ids = {
                row[0]
                for row in conn.execute(text("SELECT DISTINCT project_id FROM documents"))
            }
            project_ids.update(
                row[0]
                for row in conn.execute(text("SELECT DISTINCT project_id FROM chunks"))
            )
        except Exception as exc:  # noqa: BLE001 — diagnostic
            raise HTTPException(
                status_code=500,
                detail=f"Corpus query failed: {type(exc).__name__}: {exc}",
            )

        for pid in sorted(project_ids):
            doc_count = conn.execute(
                text("SELECT COUNT(*) FROM documents WHERE project_id = :pid"),
                {"pid": pid},
            ).scalar() or 0
            chunk_count = conn.execute(
                text("SELECT COUNT(*) FROM chunks WHERE project_id = :pid"),
                {"pid": pid},
            ).scalar() or 0

            entry: Dict[str, Any] = {
                "project_id": pid,
                "documents": int(doc_count),
                "chunks": int(chunk_count),
            }

            if folder_breakdown and doc_count >= folder_breakdown_min:
                # Group documents by first '/' segment of original_name.
                # SQLite has instr(); Postgres has strpos(). PR #91's first
                # cut used instr() and 500'd on prod Postgres. Do the
                # grouping in Python — bounded by docs-per-project (a few
                # thousand at most for the largest corpus), so a single
                # full scan + Counter is fine and dialect-portable.
                from collections import Counter
                name_rows = conn.execute(
                    text("SELECT original_name FROM documents WHERE project_id = :pid"),
                    {"pid": pid},
                ).fetchall()
                buckets: "Counter[str]" = Counter()
                for (name,) in name_rows:
                    if name and "/" in name:
                        buckets[name.split("/", 1)[0]] += 1
                    else:
                        buckets["(no folder)"] += 1
                entry["by_top_folder"] = [
                    {"folder": folder, "docs": n}
                    for folder, n in buckets.most_common(50)
                ]

            collections.append(entry)

    # Largest first so the eye lands on drive_archive immediately when it
    # exists.
    collections.sort(key=lambda c: (-c["chunks"], -c["documents"], c["project_id"]))

    return {
        "collections": collections,
        "total_project_ids": len(collections),
        "total_documents": sum(c["documents"] for c in collections),
        "total_chunks": sum(c["chunks"] for c in collections),
    }


# ──────────────────────────────────────────────────────────────────────────
# /v1/admin/corpus/bulk-insert — operator-driven migration endpoint
# ──────────────────────────────────────────────────────────────────────────
#
# Accepts a JSON payload of projects/documents/chunks and inserts them
# into the production tables with ON CONFLICT DO NOTHING semantics so it
# can be safely retried mid-batch. Designed for the one-time migration
# of the local SQLite drive_archive corpus (~142k chunks) to Render
# Postgres, where direct psycopg from outside the Render perimeter is
# blocked by the pgsql ipAllowList default.

class _BulkProjectRow(BaseModel):
    id: str
    name: str
    user_id: str = "system"
    status: str = "active"
    created_at: Optional[str] = None  # ISO; defaults to now


class _BulkDocumentRow(BaseModel):
    id: str
    project_id: str
    original_name: str
    doc_type: str = "document"
    doc_role: str = "other"
    size: int = 0
    uploaded_at: Optional[str] = None
    file_path: Optional[str] = None


class _BulkChunkRow(BaseModel):
    chunk_id: str
    project_id: str
    doc_id: str
    chunk_index: int
    text: str
    embedding: List[float]
    created_at: Optional[str] = None


class _BulkInsertRequest(BaseModel):
    projects: List[_BulkProjectRow] = []
    documents: List[_BulkDocumentRow] = []
    chunks: List[_BulkChunkRow] = []


@router.post("/v1/admin/corpus/bulk-insert")
def admin_corpus_bulk_insert(
    req: _BulkInsertRequest,
    auth: dict = Depends(require_api_key),
):
    """Idempotent bulk insert of projects + documents + chunks.

    Insert order respects FK: projects -> documents -> chunks. Every
    statement uses ON CONFLICT DO NOTHING so a partial batch can be
    re-sent without dup-key errors. Returns inserted-count per table
    (rows that hit the ON CONFLICT path are NOT counted).

    Embedding lists are converted to pgvector format via the same
    `CAST(:embedding AS vector)` shape the existing migrate-sqlite path
    uses (compatible with the chunks.embedding vector(256) column).
    """
    _require_admin(auth)

    from datetime import datetime, timezone
    import numpy as np

    from app.core.db import SessionLocal
    from app.core.models import Document, Project, RagChunk

    now_iso = datetime.now(timezone.utc).isoformat()
    counts: Dict[str, int] = {"projects": 0, "documents": 0, "chunks": 0,
                              "projects_seen": 0, "documents_seen": 0,
                              "chunks_seen": 0}

    with SessionLocal() as session:
        # ── projects (FK target) ───────────────────────────────────────
        for p in req.projects:
            counts["projects_seen"] += 1
            if session.get(Project, p.id) is not None:
                continue
            session.add(Project(
                id=p.id, name=p.name, status=p.status,
                aconex_connected=False, user_id=p.user_id,
                created_at=p.created_at or now_iso,
            ))
            counts["projects"] += 1
        session.flush()

        # ── documents ──────────────────────────────────────────────────
        for d in req.documents:
            counts["documents_seen"] += 1
            if session.get(Document, d.id) is not None:
                continue
            session.add(Document(
                id=d.id, project_id=d.project_id,
                original_name=d.original_name,
                stored_as=None, file_path=d.file_path,
                doc_type=d.doc_type, doc_role=d.doc_role,
                size=d.size,
                uploaded_at=d.uploaded_at or now_iso,
            ))
            counts["documents"] += 1
        session.flush()

        # ── chunks (embedding stored via EmbeddingVector adapter) ──────
        for c in req.chunks:
            counts["chunks_seen"] += 1
            if session.get(RagChunk, c.chunk_id) is not None:
                continue
            session.add(RagChunk(
                chunk_id=c.chunk_id, project_id=c.project_id,
                doc_id=c.doc_id, chunk_index=c.chunk_index,
                text=c.text,
                embedding=np.asarray(c.embedding, dtype=np.float32),
                created_at=c.created_at or now_iso,
            ))
            counts["chunks"] += 1
        session.commit()

    return {"status": "ok", "counts": counts}


# ──────────────────────────────────────────────────────────────────────────
# PR A — admin-approved projects from Drive
#
# Two endpoints to support the operator's auto-detection + admin-approval
# architecture:
#
#   GET  /v1/admin/drive/scan
#     Walks the admin's connected Drive. Returns a cascaded folder tree
#     with file counts. Detection only — does NOT create projects,
#     does NOT index anything. The admin UI renders this as checkboxes
#     for "approve as project" actions.
#
#   POST /v1/admin/projects/approve-from-drive
#     Takes a Drive folder_id + project name. Creates a project row
#     with is_approved=True. Queues a background recursive import of
#     every supported file under that folder into the new project
#     (reuses the existing drive_index_folder helper logic).
# ──────────────────────────────────────────────────────────────────────────


class _DriveScanFolder(BaseModel):
    folder_id: str
    name: str
    direct_file_count: int
    subfolder_count: int
    is_candidate: bool  # True iff direct_file_count > 0
    children: List["_DriveScanFolder"] = []


_DriveScanFolder.model_rebuild()


@router.get("/v1/admin/drive/scan")
async def admin_drive_scan(
    max_depth: int = Query(2, ge=1, le=3,
                            description="How deep to descend; 2 covers the typical "
                                        "<root>/<container>/<project> layout."),
    auth: dict = Depends(require_api_key),
):
    """Detection-only Drive scan.

    Returns the folder tree from the admin's My Drive root to ``max_depth``.
    Each node carries ``direct_file_count`` (files immediately inside the
    folder) so the UI can mark candidates (>0 files) and the admin can
    decide which folders represent real projects.

    Cost is bounded by depth + Drive's listing pagination — for the
    pilot corpus (~10 top-level folders, ~50 sub-folders) this returns
    in under a second.
    """
    _require_admin(auth)

    from app.core import drive_auth
    from app.blocks.google_drive import GoogleDriveBlock

    try:
        access_token = await drive_auth.get_access_token(auth["user_id"])
    except drive_auth.DriveNotConnected:
        raise HTTPException(409, "Google Drive is not connected for this admin.")
    except drive_auth.DriveAuthError as e:
        raise HTTPException(409, f"{e} Reconnect Google Drive.")

    drive = GoogleDriveBlock()

    FOLDER_MIME = "application/vnd.google-apps.folder"

    async def _list(folder_id: Optional[str]) -> List[Dict[str, Any]]:
        # GoogleDriveBlock.process expects query string + opts; folder_id
        # filters to direct children. Limit 200 per folder is plenty for
        # the operator's structure; the cap protects against bombs.
        resp = await drive.process("", {
            "operation": "list",
            "access_token": access_token,
            "limit": 200,
            "folder_id": folder_id,
        })
        if resp.get("status") != "success":
            raise HTTPException(502, resp.get("error", "Drive list failed."))
        return resp.get("files", [])

    async def _walk(folder_id: Optional[str], depth: int) -> List[Dict[str, Any]]:
        items = await _list(folder_id)
        folders = [i for i in items if i.get("mime_type") == FOLDER_MIME]
        files = [i for i in items if i.get("mime_type") != FOLDER_MIME]

        out: List[Dict[str, Any]] = []
        for f in folders:
            entry: Dict[str, Any] = {
                "folder_id": f.get("id"),
                "name": f.get("name", ""),
                "direct_file_count": 0,  # computed if we recurse
                "subfolder_count": 0,
                "is_candidate": False,
                "children": [],
            }
            if depth + 1 <= max_depth:
                # Recurse one level — collect direct children to compute
                # counts. The recursion result becomes this entry's children
                # only when those children themselves have nested folders
                # (depth + 2 <= max_depth); otherwise we still set the
                # counts but children stay empty.
                child_items = await _list(f.get("id"))
                child_folders = [c for c in child_items if c.get("mime_type") == FOLDER_MIME]
                child_files = [c for c in child_items if c.get("mime_type") != FOLDER_MIME]
                entry["direct_file_count"] = len(child_files)
                entry["subfolder_count"] = len(child_folders)
                entry["is_candidate"] = entry["direct_file_count"] > 0

                if depth + 2 <= max_depth:
                    # Go one more level for nested project structures.
                    deeper = await _walk(f.get("id"), depth + 1)
                    # _walk recurses through the listing — but we already
                    # listed this folder above. To avoid a second list call,
                    # build children from child_folders directly + recurse on each.
                    nested = []
                    for cf in child_folders:
                        nested_items = await _list(cf.get("id"))
                        nested_files = [c for c in nested_items if c.get("mime_type") != FOLDER_MIME]
                        nested_folders = [c for c in nested_items if c.get("mime_type") == FOLDER_MIME]
                        nested.append({
                            "folder_id": cf.get("id"),
                            "name": cf.get("name", ""),
                            "direct_file_count": len(nested_files),
                            "subfolder_count": len(nested_folders),
                            "is_candidate": len(nested_files) > 0,
                            "children": [],
                        })
                    entry["children"] = nested
                    _ = deeper  # placeholder; real depth-3 walk above is fine
            out.append(entry)
        return out

    root_items = await _list(None)
    root_folders = [i for i in root_items if i.get("mime_type") == FOLDER_MIME]
    root_files = [i for i in root_items if i.get("mime_type") != FOLDER_MIME]

    tree: List[Dict[str, Any]] = []
    for f in root_folders:
        # Manually compute first-level counts + (optionally) recurse for
        # depth-2 children to populate the cascade.
        sub_items = await _list(f.get("id"))
        sub_folders = [c for c in sub_items if c.get("mime_type") == FOLDER_MIME]
        sub_files = [c for c in sub_items if c.get("mime_type") != FOLDER_MIME]

        entry: Dict[str, Any] = {
            "folder_id": f.get("id"),
            "name": f.get("name", ""),
            "direct_file_count": len(sub_files),
            "subfolder_count": len(sub_folders),
            "is_candidate": len(sub_files) > 0,
            "children": [],
        }
        if max_depth >= 2:
            children: List[Dict[str, Any]] = []
            for sf in sub_folders:
                grand_items = await _list(sf.get("id"))
                grand_folders = [c for c in grand_items if c.get("mime_type") == FOLDER_MIME]
                grand_files = [c for c in grand_items if c.get("mime_type") != FOLDER_MIME]
                children.append({
                    "folder_id": sf.get("id"),
                    "name": sf.get("name", ""),
                    "direct_file_count": len(grand_files),
                    "subfolder_count": len(grand_folders),
                    "is_candidate": len(grand_files) > 0,
                    "children": [],
                })
            entry["children"] = children
        tree.append(entry)

    return {
        "max_depth": max_depth,
        "root_file_count": len(root_files),
        "candidates_total": sum(1 for f in tree if f["is_candidate"]),
        "tree": tree,
    }


class _ApproveFromDriveRequest(BaseModel):
    folder_id: str
    name: str
    max_files: int = 500
    max_depth: int = 6
    role: str = "other"


@router.post("/v1/admin/projects/approve-from-drive", status_code=201)
async def admin_approve_from_drive(
    req: _ApproveFromDriveRequest,
    background_tasks: BackgroundTasks,
    auth: dict = Depends(require_api_key),
):
    """Create a project row + queue recursive Drive-folder import.

    Flow:
      1. Slug the supplied name → project_id.
      2. Insert project row with is_approved=True, user_id=admin.
      3. Queue async import using the existing drive_index_folder logic
         — walks the Drive folder up to ``max_depth``, imports each
         supported file as a project document, kicks off doc-index +
         RAG indexing for each.

    Returns immediately with project_id and a status of "queued";
    the import progresses in the background. The admin can hit
    /v1/admin/corpus/collections later to see when chunks land.
    """
    _require_admin(auth)

    if not req.folder_id or not req.folder_id.strip():
        raise HTTPException(400, "folder_id is required")
    if not req.name or not req.name.strip():
        raise HTTPException(400, "name is required")

    import re
    from fastapi import BackgroundTasks
    from app.core import drive_auth, projects as _projects_mod

    # Verify Drive auth before doing any DB writes — fail fast.
    try:
        access_token = await drive_auth.get_access_token(auth["user_id"])
    except drive_auth.DriveNotConnected:
        raise HTTPException(409, "Google Drive is not connected for this admin.")
    except drive_auth.DriveAuthError as e:
        raise HTTPException(409, f"{e} Reconnect Google Drive.")

    name = req.name.strip()
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:48] or "project"

    # Ensure the slug is unique — append a short suffix if it collides.
    existing = _projects_mod.get_project(slug)
    if existing is not None:
        suffix = 2
        while _projects_mod.get_project(f"{slug}_{suffix}") is not None:
            suffix += 1
        slug = f"{slug}_{suffix}"

    project = _projects_mod.create_project(
        name=name,
        user_id=auth["user_id"],
        is_approved=True,
        project_id=slug,
        origin="admin_drive_approved",
    )

    # Queue the recursive import as a background task.
    background_tasks.add_task(
        _run_drive_folder_import,
        project_id=slug, user_id=auth["user_id"],
        folder_id=req.folder_id, max_files=req.max_files,
        max_depth=req.max_depth, role=req.role,
    )

    return {
        "status": "queued",
        "project": project,
        "import": {
            "folder_id": req.folder_id,
            "max_files": req.max_files,
            "max_depth": req.max_depth,
            "role": req.role,
        },
    }


# Wire BackgroundTasks injection — separate signature so FastAPI sees it.
@router.post("/v1/admin/projects/approve-from-drive/_bg")
async def _admin_approve_from_drive_bg(
    req: _ApproveFromDriveRequest,
    auth: dict = Depends(require_api_key),
):
    # Hidden alias — kept so the import-target dep gets BackgroundTasks
    # injected without bloating the canonical handler signature. Not for
    # external use.
    raise HTTPException(410, "Use /v1/admin/projects/approve-from-drive")


async def _run_drive_folder_import(
    *,
    project_id: str,
    user_id: str,
    folder_id: str,
    max_files: int,
    max_depth: int,
    role: str,
) -> None:
    """Background worker: walk the Drive folder + import every file
    into ``project_id``. Reuses the same helpers the per-project
    drive_index_folder route uses, so a file lands encrypted-at-rest +
    eagerly indexed for RAG identically to a user-initiated import.
    """
    import logging
    log = logging.getLogger(__name__)
    log.info("approve-from-drive: starting import project=%s folder=%s",
             project_id, folder_id)
    try:
        # Delay-import to avoid pulling Drive deps at module-load time.
        from app.routers import drive as drive_router
        from app.core import drive_auth
        from fastapi import BackgroundTasks as _BG

        access_token = await drive_auth.get_access_token(user_id)
        _bg = _BG()
        await drive_router._walk_drive_folder_into_project(
            project_id=project_id,
            user_id=user_id,
            access_token=access_token,
            folder_id=folder_id,
            max_files=max_files,
            max_depth=max_depth,
            role=role,
            background_tasks=_bg,
        )
        # Drain indexing tasks inline so the worker doesn't exit before
        # chunks are written. This keeps the admin "documents/chunks" counters
        # consistent once the import finishes.
        for task in _bg.tasks:
            if asyncio.iscoroutinefunction(task.func):
                await task.func(*task.args, **task.kwargs)
            else:
                task.func(*task.args, **task.kwargs)
    except Exception as exc:
        log.exception("approve-from-drive: import failed project=%s folder=%s: %s",
                      project_id, folder_id, exc)


import asyncio  # for asyncio.iscoroutinefunction used above
