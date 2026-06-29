"""Document index — text extraction, chunking, and per-project index persistence.

Roadmap V2 · Stream C — Phase C1.

This module builds a searchable text index for each project's documents.
It reuses file_crypto for transparent decryption and app.core.projects for
document metadata. No TF-IDF or search logic lives here — that is Phase C2.

Public API
----------
* ``extract_document_text(file_path, filename) -> str``
    Extract plaintext from a single document. Returns "" on any error.
* ``chunk_text(text, words_per_chunk=500) -> list[str]``
    Split text into word-count-bounded chunks.
* ``index_project(project_id) -> dict``
    Build (or rebuild) the full index for a project and persist it.
* ``index_document(project_id, document_id) -> dict``
    Incrementally add / replace one document in an existing index.
* ``invalidate_project(project_id) -> None``
    Delete the on-disk index so the next call rebuilds from scratch.
* ``_load_index(project_id) -> dict | None``
    Read a project's stored index; None if absent.
* ``init_db() -> None``
    Ensure the index DB schema exists and import any legacy JSON indexes.
* ``_data_dir() -> str``
    DATA_DIR env at call time, with tempfile fallback.

The index is persisted in the unified The Fork DB (one JSON row per project),
so the read-modify-write in ``index_document`` runs in a real transaction —
concurrent updates, including across worker processes, serialise instead of
overwriting each other.

SQLAlchemy-backed via app.core.db — unified The Fork schema.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import logging as _logging
import os
import re
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import zlib

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import file_crypto
from app.core import projects as _projects
from app.core.db import SessionLocal, engine, get_database_url, get_engine
from app.core.models import DocIndex, Project

# Image extensions — Stream F runs OCR on these to make scanned drawings /
# photos searchable. They are SUPPORTED (not "unsupported_type") even when OCR
# yields no text: a blank photo simply indexes with empty chunks.
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

# Extensions we know how to extract text from.
_SUPPORTED_EXTS = (
    {".txt", ".md", ".csv", ".json", ".xml", ".pdf", ".docx", ".xlsx"}
    | _IMAGE_EXTS
)

# A PDF whose recovered text-layer is shorter than this is treated as a
# scanned / image-only PDF and re-extracted via OCR.
_PDF_OCR_THRESHOLD = 30

# In-process guard around index writes. Cross-process safety comes from the
# SQLite BEGIN IMMEDIATE transaction in _update_index; this lock just avoids
# threads in one process contending on the DB lock unnecessarily.
_INDEX_LOCK = threading.RLock()
_initialized = False
_initialized_for_url: str | None = None


# ── sync → async bridge ────────────────────────────────────────────────────────

def _run_sync(coro):
    """Run an async coroutine to completion from synchronous code.

    ``extract_document_text`` is sync but the OCR block is async. This bridge
    handles both call contexts:

    * No event loop running (plain sync indexing) → ``asyncio.run``.
    * A loop IS already running (``search_project_documents`` lazily triggers
      ``index_project`` → ``extract_document_text`` from inside the running
      loop) → run the coroutine on a FRESH loop inside a worker thread, so we
      never hit "asyncio.run() cannot be called from a running event loop".
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running in this thread — safe to drive one directly.
        return asyncio.run(coro)

    # A loop is already running here; offload to a worker thread with its own.
    def _worker():
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_worker).result()


def _ocr_extract(file_path: str) -> Tuple[str, bool]:
    """Run the OCR block on ``file_path``; return ``(text, low_quality)``.

    Never raises — any OCR error / missing text yields ``("", False)``. The
    OCR block does its own ``open_plaintext`` decryption, so the raw stored
    path is passed straight through.
    """
    try:
        from app.blocks.ocr import OCRBlock

        result = _run_sync(OCRBlock().process(file_path))
        if not isinstance(result, dict) or result.get("status") == "error":
            return "", False
        text = result.get("text") or ""
        quality = result.get("quality") or {}
        low_quality = bool(quality.get("low_quality"))
        return text, low_quality
    except Exception:
        return "", False


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_dir() -> str:
    """Resolve DATA_DIR at call time (so tests can relocate it via setenv)."""
    data_dir = os.getenv("DATA_DIR", "./data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        data_dir = tempfile.gettempdir()
    return data_dir


def _ensure_sqlite_parent_dir() -> None:
    url = get_database_url()
    if url.startswith("sqlite:///"):
        parent = os.path.dirname(url[len("sqlite:///") :])
        if parent:
            os.makedirs(parent, exist_ok=True)


def _legacy_db_path() -> str:
    """Pre-unified layout: dedicated SQLite file under DATA_DIR."""
    return os.path.join(_data_dir(), "doc_index.db")


def _legacy_index_dir() -> str:
    """Pre-SQLite layout: one JSON file per project under data/doc_index/."""
    return os.path.join(_data_dir(), "doc_index")


# ── text extraction ───────────────────────────────────────────────────────────

def _extract_with_meta(file_path: str, filename: str) -> Tuple[str, Dict[str, Any]]:
    """Extract plaintext from a document, plus a small metadata dict.

    Returns ``(text, meta)`` where ``meta`` carries ``{"ocr_low_quality": True}``
    when the document was OCR'd and the OCR quality verdict flagged it as a poor
    scan (omitted otherwise). Never raises — returns ``("", {})`` on any error.

    Supports:
    * .txt/.md/.csv/.json/.xml — via file_crypto.read_document
    * .pdf — fitz / PyMuPDF text layer; if that text is effectively empty
      (a scanned / image-only PDF) it falls back to OCR on the same file
    * .docx — python-docx; .xlsx — openpyxl
    * image extensions (.jpg/.png/.webp/...) — OCR via OCRBlock
    """
    try:
        _, ext = os.path.splitext((filename or "").lower())
        if ext not in _SUPPORTED_EXTS:
            return "", {}

        # ── plain-text-like formats ──────────────────────────────────────────
        if ext in {".txt", ".md", ".csv", ".json", ".xml"}:
            raw = file_crypto.read_document(file_path)
            return raw.decode("utf-8", errors="replace"), {}

        # ── images → OCR ─────────────────────────────────────────────────────
        if ext in _IMAGE_EXTS:
            text, low_quality = _ocr_extract(file_path)
            meta: Dict[str, Any] = {}
            if low_quality:
                meta["ocr_low_quality"] = True
            return text, meta

        # ── PDF ──────────────────────────────────────────────────────────────
        if ext == ".pdf":
            import fitz  # PyMuPDF
            text = ""
            try:
                with file_crypto.open_plaintext(file_path) as readable_path:
                    doc = fitz.open(readable_path)
                    for page in doc:
                        text += page.get_text()
                    doc.close()
            except Exception:
                text = ""
            # Scanned / image-only PDF — no usable text layer → OCR fallback.
            if len(text.strip()) < _PDF_OCR_THRESHOLD:
                ocr_text, low_quality = _ocr_extract(file_path)
                if ocr_text.strip():
                    meta = {"ocr_low_quality": True} if low_quality else {}
                    return ocr_text, meta
            return text, {}

        # ── DOCX ─────────────────────────────────────────────────────────────
        if ext == ".docx":
            import docx
            with file_crypto.open_plaintext(file_path) as readable_path:
                document = docx.Document(readable_path)
                return "\n".join(p.text for p in document.paragraphs), {}

        # ── XLSX ─────────────────────────────────────────────────────────────
        if ext == ".xlsx":
            import openpyxl
            with file_crypto.open_plaintext(file_path) as readable_path:
                wb = openpyxl.load_workbook(readable_path, data_only=True)
                parts: List[str] = []
                for sheet_name in wb.sheetnames:
                    ws = wb[sheet_name]
                    for row in ws:
                        for cell in row:
                            if cell.value is not None and str(cell.value).strip():
                                parts.append(str(cell.value))
                return " ".join(parts), {}

    except Exception:
        return "", {}

    return "", {}


def extract_document_text(file_path: str, filename: str) -> str:
    """Extract plaintext from a document.

    Supports .txt/.md/.csv/.json/.xml (via file_crypto.read_document),
    .pdf (via fitz / PyMuPDF + open_plaintext, with OCR fallback for scanned
    image-only PDFs), .docx (via python-docx + open_plaintext), .xlsx (via
    openpyxl + open_plaintext), and image formats (.jpg/.jpeg/.png/.webp/.gif/
    .bmp/.tif/.tiff) via OCR.

    Returns "" for unsupported extensions and on any extraction error —
    callers treat the empty string as "skipped". Never raises.
    """
    text, _ = _extract_with_meta(file_path, filename)
    return text


# ── chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, words_per_chunk: int = 500) -> List[str]:
    """Split ``text`` into chunks of at most ``words_per_chunk`` words.

    Splits on whitespace; drops empty/whitespace-only chunks. Returns [] for
    empty or whitespace-only input.
    """
    words = text.split()
    if not words:
        return []
    chunks: List[str] = []
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i : i + words_per_chunk])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


# ── BOQ-aware finer chunker (FOLLOW-UP #91) ────────────────────────────────
#
# Used for documents where word-count chunking produces too-coarse chunks
# (the Diriyah BOQ symptom — 8 chunks at 2798 chars per chunk, so finding
# a single rate requires the LLM to scan 700 tokens). Re-chunks at character
# level with overlap and respects BOQ row boundaries (lines that start with
# an item code like ``D 999.14``) so a rate stays adjacent to its item code.

_BOQ_ITEM_PATTERN = re.compile(r"^\s*[A-Z]\s*\d+(?:\.\d+)+\b")

# ``re`` is used here; the module-level ``import re`` was added with the
# noise-filter helper earlier.


def chunk_text_with_overlap(
    text: str,
    target_chars: int = 500,
    overlap: int = 50,
    max_chars: int = 800,
) -> List[str]:
    """Split ``text`` into chunks targeting ``target_chars`` per chunk with
    ``overlap`` characters carried into the next chunk.

    Prefer breakpoints at, in order of preference:
    1. A line that looks like the start of a BOQ row (matches an item-code
       pattern such as ``D 999.14``) — keeps a row contiguous in one chunk.
    2. A paragraph break (``\\n\\n``).
    3. A line break (``\\n``).
    4. A sentence boundary (``. ``).
    5. Hard cut at ``max_chars`` if nothing better was found.

    ``overlap`` chars of trailing context from the previous chunk are
    prepended to the next so a row split mid-sentence still has context.

    Empty / whitespace-only input returns ``[]``.
    """
    import re as _re

    if not text or not text.strip():
        return []
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    if overlap < 0 or overlap >= target_chars:
        raise ValueError("overlap must be in [0, target_chars)")
    if max_chars < target_chars:
        max_chars = target_chars

    chunks: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        # Hard window — never go past max_chars from the start of this chunk.
        hard_end = min(i + max_chars, n)
        if hard_end == n:
            tail = text[i:].strip()
            if tail:
                chunks.append(tail)
            break

        # Soft target — prefer a break point near i+target_chars.
        soft_end = min(i + target_chars, n)
        search_start = max(soft_end - 120, i + target_chars // 2)

        # Look for BOQ row start within [soft_end, hard_end] — break ABOVE the
        # next row so the new chunk starts with that row's code.
        next_row = None
        for match in _BOQ_ITEM_PATTERN.finditer(text, soft_end, hard_end):
            # Walk back to the line start.
            line_start = text.rfind("\n", i, match.start()) + 1
            if line_start > i and line_start <= hard_end:
                next_row = line_start
                break
        if next_row is not None:
            end = next_row
        else:
            # Fall back to whitespace boundaries.
            end = -1
            for marker in ("\n\n", "\n", ". "):
                idx = text.rfind(marker, search_start, soft_end)
                if idx > 0:
                    end = idx + len(marker)
                    break
            if end <= i:
                # No boundary found in the soft window — extend to hard_end.
                end = hard_end

        chunk = text[i:end].strip()
        if chunk:
            chunks.append(chunk)
        # Carry overlap from the tail of this chunk.
        next_i = end - overlap if end - overlap > i else end
        if next_i <= i:
            next_i = end
        i = next_i
    return chunks


# ── index persistence ─────────────────────────────────────────────────────────

def _index_from_row(row: DocIndex | None) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = row.index_json
    return data if isinstance(data, dict) else None


def _ensure_project_row(project_id: str, session: Session) -> None:
    """Satisfy doc_index.project_id FK when tests or legacy imports use bare ids."""
    if session.get(Project, project_id) is not None:
        return
    session.add(
        Project(
            id=project_id,
            name=project_id,
            client=None,
            status="active",
            aconex_connected=False,
            user_id="system",
            created_at=_now(),
        )
    )
    session.flush()


def _ensure_project_row_on_conn(conn: Connection, project_id: str) -> None:
    """FK helper for the SQLite BEGIN IMMEDIATE connection path."""
    exists = conn.execute(
        select(Project.id).where(Project.id == project_id)
    ).scalar_one_or_none()
    if exists is not None:
        return
    conn.execute(
        insert(Project).values(
            id=project_id,
            name=project_id,
            client=None,
            status="active",
            aconex_connected=False,
            user_id="system",
            created_at=_now(),
        )
    )


def _import_legacy_json_indexes(session: Session) -> None:
    """Import pre-SQLite data/doc_index/<pid>.json files once."""
    legacy = _legacy_index_dir()
    if not os.path.isdir(legacy):
        return
    for fn in os.listdir(legacy):
        if not fn.endswith(".json"):
            continue
        pid = fn[:-5]
        if _is_master_corpus_alias(pid):
            continue  # virtual alias — never materialise a real row for it
        if session.get(DocIndex, pid) is not None:
            continue
        try:
            with open(os.path.join(legacy, fn), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        _ensure_project_row(pid, session)
        session.add(
            DocIndex(project_id=pid, index_json=data, updated_at=_now())
        )


def _import_legacy_sqlite_db(session: Session) -> None:
    """Import rows from the legacy dedicated doc_index.db file once."""
    path = _legacy_db_path()
    if not os.path.isfile(path):
        return
    with sqlite3.connect(path, timeout=30.0) as leg:
        for pid, index_json, updated_at in leg.execute(
            "SELECT project_id, index_json, updated_at FROM doc_index"
        ):
            if _is_master_corpus_alias(pid):
                continue  # virtual alias — never materialise a real row for it
            if session.get(DocIndex, pid) is not None:
                continue
            try:
                data = json.loads(index_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            _ensure_project_row(pid, session)
            session.add(
                DocIndex(
                    project_id=pid,
                    index_json=data,
                    updated_at=updated_at or _now(),
                )
            )


def init_db() -> None:
    """Ensure the schema exists and import any legacy on-disk indexes.

    Pre-SQLite deployments stored each index as data/doc_index/<pid>.json.
    Pre-unified deployments used data/doc_index.db. Both are imported once
    per database URL (rows already present are left untouched); source files
    are left in place.
    """
    global _initialized, _initialized_for_url
    url = get_database_url()
    with _INDEX_LOCK:
        from app.core.projects import init_db as init_projects_db

        init_projects_db()
        _ensure_sqlite_parent_dir()
        DocIndex.__table__.create(bind=engine, checkfirst=True)
        if _initialized_for_url != url:
            with SessionLocal() as session:
                _import_legacy_json_indexes(session)
                # Flush pending DocIndex rows so the SQLite-migration's
                # ``session.get(DocIndex, pid)`` dedupe check can see them.
                # Without this, both migrations independently add a row for
                # any project_id present in both legacy sources, and the
                # final commit fails with the UNIQUE constraint.
                session.flush()
                _import_legacy_sqlite_db(session)
                session.commit()
            # Self-heal: drop any real row carrying the virtual master-corpus
            # alias id (a stale _ensure_project_row artifact that duplicates the
            # injected alias in listings).
            try:
                _purge_spurious_master_corpus_row()
            except Exception:  # noqa: BLE001 — never let startup die on cleanup
                _logging.getLogger(__name__).warning(
                    "init_db: spurious master-corpus row cleanup failed", exc_info=True
                )
            _initialized_for_url = url
        _initialized = True


def _ensure_db() -> None:
    url = get_database_url()
    if not _initialized or _initialized_for_url != url:
        init_db()


def _load_index(project_id: str) -> Optional[Dict[str, Any]]:
    """Read the stored index for ``project_id``. Returns None if absent."""
    _ensure_db()
    with SessionLocal() as session:
        return _index_from_row(session.get(DocIndex, project_id))


def _write_index(project_id: str, data: Dict[str, Any]) -> None:
    """Replace the stored index for ``project_id`` (a full-rebuild write).

    Reuses ``_update_index`` so the full-rebuild path gets the same
    cross-process serialization as incremental updates:
    - SQLite: BEGIN IMMEDIATE
    - Postgres: pg_advisory_xact_lock + SELECT FOR UPDATE
    """
    _update_index(project_id, lambda _current: data)


def _apply_index_mutation(
    session: Session,
    project_id: str,
    mutate: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    *,
    lock_row: bool,
) -> Dict[str, Any]:
    if lock_row:
        row = session.scalar(
            select(DocIndex)
            .where(DocIndex.project_id == project_id)
            .with_for_update()
        )
    else:
        row = session.get(DocIndex, project_id)
    current_raw = _index_from_row(row)
    current = copy.deepcopy(current_raw) if current_raw is not None else None
    updated = mutate(current)
    now = _now()
    _ensure_project_row(project_id, session)
    if row is None:
        session.add(
            DocIndex(project_id=project_id, index_json=updated, updated_at=now)
        )
    else:
        row.index_json = updated
        row.updated_at = now
        flag_modified(row, "index_json")
    return updated


def _load_index_json_from_conn(
    conn: Connection, project_id: str
) -> Optional[Dict[str, Any]]:
    data = conn.execute(
        select(DocIndex.index_json).where(DocIndex.project_id == project_id)
    ).scalar_one_or_none()
    return data if isinstance(data, dict) else None


def _update_index_on_sqlite_conn(
    conn: Connection,
    project_id: str,
    mutate: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
) -> Dict[str, Any]:
    current = _load_index_json_from_conn(conn, project_id)
    updated = mutate(current)
    now = _now()
    _ensure_project_row_on_conn(conn, project_id)
    row_exists = conn.execute(
        select(DocIndex.project_id).where(DocIndex.project_id == project_id)
    ).scalar_one_or_none()
    if row_exists is None:
        conn.execute(
            insert(DocIndex).values(
                project_id=project_id,
                index_json=updated,
                updated_at=now,
            )
        )
    else:
        conn.execute(
            update(DocIndex)
            .where(DocIndex.project_id == project_id)
            .values(index_json=updated, updated_at=now)
        )
    return updated


def _update_index(
    project_id: str,
    mutate: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
) -> Dict[str, Any]:
    """Atomic read-modify-write of a project's index.

    Runs ``mutate(current_or_None) -> new_index`` inside a single write
    transaction (BEGIN IMMEDIATE on SQLite), so two concurrent updates —
    even from separate worker processes — serialise rather than overwrite
    each other.
    """
    with _INDEX_LOCK:
        _ensure_db()
        eng = get_engine()
        dialect = eng.dialect.name
        if dialect == "sqlite":
            with eng.connect() as conn:
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    updated = _update_index_on_sqlite_conn(conn, project_id, mutate)
                    conn.exec_driver_sql("COMMIT")
                except BaseException:
                    conn.exec_driver_sql("ROLLBACK")
                    raise
            return updated

        with SessionLocal() as session:
            with session.begin():
                # FOR UPDATE does not lock missing rows; advisory lock serialises
                # concurrent first-time inserts for the same project_id.
                lock_key = zlib.crc32(project_id.encode("utf-8")) & 0x7FFFFFFF
                session.execute(
                    text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key}
                )
                return _apply_index_mutation(
                    session, project_id, mutate, lock_row=True
                )


def _ext_of(filename: str) -> str:
    _, ext = os.path.splitext((filename or "").lower())
    return ext


def index_project(project_id: str) -> Dict[str, Any]:
    """Build (or rebuild) the full text index for ``project_id``.

    For each document:
    - unsupported extension → record under ``skipped`` with reason
      ``"unsupported_type"``
    - supported extension → extract text → chunk → store entry

    Persists the result as JSON and returns a summary dict.
    """
    docs = _projects.list_documents(project_id)

    documents: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    total_chunks = 0

    for doc in docs:
        filename = doc.get("original_name", "")
        ext = _ext_of(filename)

        if ext not in _SUPPORTED_EXTS:
            fingerprint = f"{doc['uploaded_at']}:{doc['size']}"
            skipped.append({
                "document_id": doc["id"],
                "filename": filename,
                "reason": "unsupported_type",
                "fingerprint": fingerprint,
            })
            continue

        file_path = doc.get("file_path") or ""
        text, meta = _extract_with_meta(file_path, filename)
        chunks = chunk_text(text)

        fingerprint = f"{doc['uploaded_at']}:{doc['size']}"
        entry: Dict[str, Any] = {
            "document_id": doc["id"],
            "filename": filename,
            "fingerprint": fingerprint,
            "chunks": chunks,
        }
        if meta.get("ocr_low_quality"):
            entry["ocr_low_quality"] = True
        documents.append(entry)
        total_chunks += len(chunks)

        # PR #94: mirror the RAG hook from ``index_document`` so the
        # chunks table stays in sync with the JSON index. Without this,
        # ``index_project`` left the chunks table empty and the new
        # hybrid-retriever-backed ``search_project_documents`` returned
        # nothing — even though the JSON index was fresh.
        try:
            from app.core.rag import retriever as _rag
            if _rag.available() and chunks:
                _rag.index_chunks(project_id, doc["id"], chunks)
        except Exception as exc:  # noqa: BLE001 — RAG must never break primary indexing
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "RAG indexing skipped for %s during index_project: %s",
                doc["id"], exc,
            )

    index_data: Dict[str, Any] = {
        "project_id": project_id,
        "built_at": _now(),
        "documents": documents,
        "skipped": skipped,
    }
    _write_index(project_id, index_data)

    return {
        "project_id": project_id,
        "indexed": len(documents),
        "skipped_unsupported": len(skipped),
        "total_chunks": total_chunks,
    }


def index_document(
    project_id: str,
    document_id: str,
    chunker: str = "default",
) -> Dict[str, Any]:
    """Incrementally index a single document into the project's index.

    Loads the existing index (or starts an empty one), extracts + chunks the
    given document, replaces any existing entry for that document_id, and
    writes back.

    Returns a summary dict with ``indexed`` count (always 1 on success).

    ``chunker`` selects the chunking strategy:

    * ``"default"`` — 500-word windows (legacy, what every doc uses today).
    * ``"finer"``   — char-level windows with overlap (FOLLOW-UP #91 fix
      for the Diriyah BOQ symptom: coarse word-windows produced 8 chunks
      averaging 2800 chars each, so single line items were buried inside
      a wall of unrelated text).
    """
    doc = _projects.get_document(document_id)
    if doc is None:
        return {"project_id": project_id, "indexed": 0, "error": "document not found"}

    filename = doc.get("original_name", "")
    ext = _ext_of(filename)
    fingerprint = f"{doc.get('uploaded_at', '')}:{doc.get('size', 0)}"

    # Slow work (text extraction, OCR, chunking) runs OUTSIDE the lock so it
    # does not serialise all indexing — only the load-modify-write below does.
    entry: Optional[Dict[str, Any]] = None
    skipped_entry: Optional[Dict[str, Any]] = None
    chunks: List[str] = []
    if ext not in _SUPPORTED_EXTS:
        skipped_entry = {
            "document_id": document_id,
            "filename": filename,
            "reason": "unsupported_type",
            "fingerprint": fingerprint,
        }
    else:
        file_path = doc.get("file_path") or ""
        text, meta = _extract_with_meta(file_path, filename)
        if chunker == "finer":
            chunks = chunk_text_with_overlap(text, target_chars=500, overlap=50)
        else:
            chunks = chunk_text(text)
        entry = {
            "document_id": document_id,
            "filename": filename,
            "fingerprint": fingerprint,
            "chunks": chunks,
        }
        if meta.get("ocr_low_quality"):
            entry["ocr_low_quality"] = True

        # ── RAG hook (PR 2) — best-effort embedding into the vector store.
        # Lazy import + try/except keep doc_index importable even when
        # sentence-transformers isn't installed. Idempotent via
        # upsert_chunks: re-indexing the same doc replaces its chunks.
        try:
            from app.core.rag import retriever as _rag
            if _rag.available() and chunks:
                indexed = _rag.index_chunks(project_id, document_id, chunks)
                if indexed:
                    entry["rag_indexed"] = indexed
        except Exception as exc:  # noqa: BLE001
            # Never let a RAG failure abort the primary doc-index path
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "RAG indexing skipped for %s: %s", document_id, exc
            )

    # Load-modify-write inside one SQLite transaction — a concurrent
    # index_document call for the same project (another BackgroundTask, or
    # another worker process) cannot interleave and drop this entry.
    def _mutate(current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        current = current or {
            "project_id": project_id,
            "built_at": _now(),
            "documents": [],
            "skipped": [],
        }
        current["documents"] = [
            d for d in current.get("documents", [])
            if d["document_id"] != document_id
        ]
        current["skipped"] = [
            s for s in current.get("skipped", [])
            if s["document_id"] != document_id
        ]
        if entry is not None:
            current["documents"].append(entry)
        else:
            current["skipped"].append(skipped_entry)
        current["built_at"] = _now()
        return current

    _update_index(project_id, _mutate)

    if entry is None:
        return {
            "project_id": project_id,
            "indexed": 0,
            "skipped_unsupported": 1,
            "total_chunks": 0,
        }
    return {
        "project_id": project_id,
        "indexed": 1,
        "skipped_unsupported": 0,
        "total_chunks": len(chunks),
    }


def invalidate_project(project_id: str) -> None:
    """Delete the stored index for ``project_id`` (next access rebuilds it)."""
    with _INDEX_LOCK:
        _ensure_db()
        with SessionLocal() as session:
            session.execute(
                delete(DocIndex).where(DocIndex.project_id == project_id)
            )
            session.commit()


def _is_master_corpus_alias(project_id: str) -> bool:
    """True when ``project_id`` is the VIRTUAL master-corpus alias (backed by a
    different source project). Such an id must never become a real Project row —
    the alias is injected into listings on the fly."""
    from app.core.projects import (
        MASTER_CORPUS_PROJECT_ID,
        MASTER_CORPUS_SOURCE_PROJECT_ID,
    )
    return (
        project_id == MASTER_CORPUS_PROJECT_ID
        and MASTER_CORPUS_PROJECT_ID != MASTER_CORPUS_SOURCE_PROJECT_ID
    )


def purge_project_index(project_id: str) -> None:
    """Remove ALL index traces of a project so a delete sticks across restarts.

    ``delete_project`` only removes the Project row (+ cascaded docs). The
    DocIndex row and the legacy on-disk sources (``data/doc_index/<pid>.json``
    and the legacy sqlite db) survive — and ``init_db`` re-imports them on the
    next restart, where ``_ensure_project_row`` resurrects the deleted project.
    Purging those sources is what makes a delete permanent.
    """
    log = _logging.getLogger(__name__)
    with _INDEX_LOCK:
        _ensure_db()
        with SessionLocal() as session:
            session.execute(
                delete(DocIndex).where(DocIndex.project_id == project_id)
            )
            session.commit()
    # Legacy per-project JSON file.
    try:
        path = os.path.join(_legacy_index_dir(), f"{project_id}.json")
        if os.path.isfile(path):
            os.remove(path)
    except OSError as exc:
        log.warning("purge_project_index: legacy json remove failed for %s: %s", project_id, exc)
    # Legacy dedicated sqlite db row.
    try:
        db = _legacy_db_path()
        if os.path.isfile(db):
            with sqlite3.connect(db, timeout=30.0) as leg:
                leg.execute("DELETE FROM doc_index WHERE project_id = ?", (project_id,))
                leg.commit()
    except sqlite3.Error as exc:
        log.warning("purge_project_index: legacy db purge failed for %s: %s", project_id, exc)


def _purge_spurious_master_corpus_row() -> None:
    """Remove a real Project row that carries the virtual master-corpus alias id.

    The master corpus is injected into listings from its backing source project;
    a real row with the alias id is always a stale ``_ensure_project_row``
    artifact that duplicates the alias in the UI. Self-healing: runs on startup.
    """
    from app.core.projects import (
        MASTER_CORPUS_PROJECT_ID,
        MASTER_CORPUS_SOURCE_PROJECT_ID,
    )
    if MASTER_CORPUS_PROJECT_ID == MASTER_CORPUS_SOURCE_PROJECT_ID:
        return  # aliasing disabled — the id IS a real project
    with SessionLocal() as session:
        row = session.get(Project, MASTER_CORPUS_PROJECT_ID)
        if row is not None:
            session.delete(row)
            session.commit()
    purge_project_index(MASTER_CORPUS_PROJECT_ID)


# ── search ────────────────────────────────────────────────────────────────────

async def search_project_documents(
    project_id: str,
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Search indexed documents for ``query``, returning up to ``top_k`` results.

    Each result is a dict: ``{document_id, filename, snippet, score}``.

    PR #94: routes through the production hybrid retriever
    (``app.core.rag.retriever.retrieve_with_filter`` — BM25 + vector RRF
    over the ``chunks`` Postgres table) so the agent's tool sees the
    SAME chunks the RAG injection layer sees. Pre-PR-94 this used a
    separate TF-IDF-over-JSON-blobs path that did not query the migrated
    drive_archive corpus, causing tool results to be empty while the
    injected RAG context contained the right answer (PR #93 migration
    surfaced the gap).

    Best chunk per document is kept (so a ``top_k=5`` request returns up
    to 5 distinct files, not 5 chunks from one file). Filenames are
    resolved via ``_doc_name_for_id`` (the same helper the retriever
    itself uses).
    """
    if not query or not query.strip():
        return []

    # Hybrid retriever lives in app.core.rag.retriever; local import to
    # keep app.core.doc_index importable when the RAG stack isn't fully
    # wired (tests, minimal install).
    from app.core.rag.retriever import retrieve_with_filter, _doc_name_for_id

    # Over-fetch chunks so we can still return ``top_k`` DISTINCT documents
    # after the "best chunk per document" collapse below. 4x is enough
    # for typical corpora where each doc has 2-10 chunks.
    over_fetch = max(top_k * 4, 20)

    def _query() -> List[Any]:
        try:
            chunks, _noise = retrieve_with_filter(query, project_id, k=over_fetch)
            return chunks
        except Exception:
            return []

    chunks = _query()
    # Lazy bootstrap: pre-PR-94 callers relied on the legacy code path
    # to build the project index on first search. Preserve that contract
    # for newly-uploaded projects (or anything pre-RAG-migration) by
    # iterating documents through ``index_document`` once. That call (and
    # not ``index_project``) is the one that fires ``_rag.index_chunks``,
    # which populates the ``chunks`` table the hybrid retriever queries.
    if not chunks and _load_index(project_id) is None:
        try:
            for doc in _projects.list_documents(project_id):
                try:
                    index_document(project_id, doc["id"])
                except Exception:
                    # Best-effort per-doc indexing; one failure shouldn't
                    # abort the bootstrap for the rest of the project.
                    continue
        except Exception:
            pass
        chunks = _query()
    if not chunks:
        return []

    best: Dict[str, Dict[str, Any]] = {}
    for c in chunks:
        score = float(c.score or 0.0)
        prev = best.get(c.doc_id)
        if prev is None or score > prev["score"]:
            best[c.doc_id] = {
                "document_id": c.doc_id,
                "filename": _doc_name_for_id(c.doc_id),
                "chunk": c.text,
                "score": score,
            }

    ranked = sorted(best.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    # Pull the OCR-low-quality flag from the JSON index (if present). The
    # hybrid retriever's Chunk doesn't carry it; tests + UI need it for
    # the "low-confidence OCR" badge on citations.
    legacy_index = _load_index(project_id)
    ocr_flag_by_doc: Dict[str, bool] = {}
    if legacy_index is not None:
        for entry in legacy_index.get("documents", []):
            if entry.get("ocr_low_quality"):
                ocr_flag_by_doc[entry["document_id"]] = True

    results: List[Dict[str, Any]] = []
    for item in ranked:
        snippet = " ".join(item["chunk"].split()[:50])
        result_row = {
            "document_id": item["document_id"],
            "filename": item["filename"],
            "snippet": snippet,
            "score": round(item["score"], 4),
        }
        if ocr_flag_by_doc.get(item["document_id"]):
            result_row["ocr_low_quality"] = True
        results.append(result_row)
    return results


# ── eager (background) indexing ───────────────────────────────────────────────

def maybe_eager_index(project_id: str, document_id: str) -> None:
    """Index a single document if eager indexing is enabled (default: on).

    Checks INDEX_ON_UPLOAD env var at call time — "1", "true", or "yes"
    (case-insensitive) enables; anything else disables. Intended to be
    scheduled via FastAPI BackgroundTasks so it runs after the response
    is sent without blocking the upload.
    """
    if os.getenv("INDEX_ON_UPLOAD", "true").strip().lower() in ("1", "true", "yes"):
        index_document(project_id, document_id)
