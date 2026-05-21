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
    Read the JSON index file; None if absent.
* ``_index_path(project_id) -> str``
    Canonical filesystem path for a project's index file.
* ``_data_dir() -> str``
    DATA_DIR env at call time, with tempfile fallback.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core import file_crypto
from app.core import projects as _projects

# Extensions we know how to extract text from.
_SUPPORTED_EXTS = {".txt", ".md", ".csv", ".json", ".xml", ".pdf", ".docx", ".xlsx"}

# Module-level lock guarding all index-file writes.
_INDEX_LOCK = threading.Lock()


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


def _index_path(project_id: str) -> str:
    """Return the canonical path for ``project_id``'s index JSON file."""
    idx_dir = os.path.join(_data_dir(), "doc_index")
    os.makedirs(idx_dir, exist_ok=True)
    return os.path.join(idx_dir, f"{project_id}.json")


# ── text extraction ───────────────────────────────────────────────────────────

def extract_document_text(file_path: str, filename: str) -> str:
    """Extract plaintext from a document.

    Supports .txt/.md/.csv/.json/.xml (via file_crypto.read_document),
    .pdf (via fitz / PyMuPDF + open_plaintext), .docx (via python-docx +
    open_plaintext), and .xlsx (via openpyxl + open_plaintext).

    Returns "" for unsupported extensions and on any extraction error —
    callers treat the empty string as "skipped". Never raises.
    """
    try:
        _, ext = os.path.splitext((filename or "").lower())
        if ext not in _SUPPORTED_EXTS:
            return ""

        # ── plain-text-like formats ──────────────────────────────────────────
        if ext in {".txt", ".md", ".csv", ".json", ".xml"}:
            raw = file_crypto.read_document(file_path)
            return raw.decode("utf-8", errors="replace")

        # ── PDF ──────────────────────────────────────────────────────────────
        if ext == ".pdf":
            import fitz  # PyMuPDF
            with file_crypto.open_plaintext(file_path) as readable_path:
                doc = fitz.open(readable_path)
                text = ""
                for page in doc:
                    text += page.get_text()
                doc.close()
            return text

        # ── DOCX ─────────────────────────────────────────────────────────────
        if ext == ".docx":
            import docx
            with file_crypto.open_plaintext(file_path) as readable_path:
                document = docx.Document(readable_path)
                return "\n".join(p.text for p in document.paragraphs)

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
                return " ".join(parts)

    except Exception:
        return ""

    return ""


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


# ── index persistence ─────────────────────────────────────────────────────────

def _load_index(project_id: str) -> Optional[Dict[str, Any]]:
    """Read the JSON index for ``project_id``. Returns None if absent."""
    path = _index_path(project_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_index(project_id: str, data: Dict[str, Any]) -> None:
    """Write ``data`` to the index file, holding the module-level lock."""
    path = _index_path(project_id)
    with _INDEX_LOCK:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


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
            skipped.append({
                "document_id": doc["id"],
                "filename": filename,
                "reason": "unsupported_type",
            })
            continue

        file_path = doc.get("file_path") or ""
        text = extract_document_text(file_path, filename)
        chunks = chunk_text(text)

        fingerprint = f"{doc['uploaded_at']}:{doc['size']}"
        documents.append({
            "document_id": doc["id"],
            "filename": filename,
            "fingerprint": fingerprint,
            "chunks": chunks,
        })
        total_chunks += len(chunks)

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


def index_document(project_id: str, document_id: str) -> Dict[str, Any]:
    """Incrementally index a single document into the project's index.

    Loads the existing index (or starts an empty one), extracts + chunks the
    given document, replaces any existing entry for that document_id, and
    writes back.

    Returns a summary dict with ``indexed`` count (always 1 on success).
    """
    existing = _load_index(project_id) or {
        "project_id": project_id,
        "built_at": _now(),
        "documents": [],
        "skipped": [],
    }

    doc = _projects.get_document(document_id)
    if doc is None:
        return {"project_id": project_id, "indexed": 0, "error": "document not found"}

    filename = doc.get("original_name", "")
    ext = _ext_of(filename)

    # Remove any existing entry for this document_id from either list
    existing["documents"] = [
        d for d in existing["documents"] if d["document_id"] != document_id
    ]
    existing["skipped"] = [
        s for s in existing["skipped"] if s["document_id"] != document_id
    ]

    if ext not in _SUPPORTED_EXTS:
        existing["skipped"].append({
            "document_id": document_id,
            "filename": filename,
            "reason": "unsupported_type",
        })
        existing["built_at"] = _now()
        _write_index(project_id, existing)
        return {
            "project_id": project_id,
            "indexed": 0,
            "skipped_unsupported": 1,
            "total_chunks": 0,
        }

    file_path = doc.get("file_path") or ""
    text = extract_document_text(file_path, filename)
    chunks = chunk_text(text)

    fingerprint = f"{doc['uploaded_at']}:{doc['size']}"
    existing["documents"].append({
        "document_id": document_id,
        "filename": filename,
        "fingerprint": fingerprint,
        "chunks": chunks,
    })
    existing["built_at"] = _now()
    _write_index(project_id, existing)

    return {
        "project_id": project_id,
        "indexed": 1,
        "skipped_unsupported": 0,
        "total_chunks": len(chunks),
    }


def invalidate_project(project_id: str) -> None:
    """Delete the on-disk index for ``project_id`` if it exists."""
    path = _index_path(project_id)
    with _INDEX_LOCK:
        try:
            os.remove(path)
        except OSError:
            pass


# ── search ────────────────────────────────────────────────────────────────────

async def search_project_documents(
    project_id: str,
    query: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Search indexed documents for ``query``, returning up to ``top_k`` results.

    Each result is a dict: ``{document_id, filename, snippet, score}``.

    Logic
    -----
    1. Lazy build — if no index on disk, build it first.
    2. Self-heal staleness — compare DB documents against the index; re-index
       any missing or fingerprint-mismatched docs; filter out docs that have
       been deleted from the DB.
    3. Flatten all chunks, rank by TF-IDF cosine similarity via ZvecBlock, keep
       the best chunk per document, return ``top_k`` documents sorted by score.
    """
    from app.blocks.zvec import ZvecBlock  # local import avoids circular refs

    # ── 1. Lazy build ─────────────────────────────────────────────────────────
    index = _load_index(project_id)
    if index is None:
        index_project(project_id)
        index = _load_index(project_id)
    if index is None:
        return []

    # ── 2. Self-heal staleness ────────────────────────────────────────────────
    db_docs = _projects.list_documents(project_id)
    db_by_id: Dict[str, Any] = {d["id"]: d for d in db_docs}

    # Index map: document_id → index entry
    idx_by_id: Dict[str, Any] = {
        d["document_id"]: d for d in index.get("documents", [])
    }

    needs_reload = False

    # Docs in DB that are missing from index or have stale fingerprint
    for doc in db_docs:
        did = doc["id"]
        expected_fp = f"{doc['uploaded_at']}:{doc['size']}"
        entry = idx_by_id.get(did)
        if entry is None or entry.get("fingerprint") != expected_fp:
            index_document(project_id, did)
            needs_reload = True

    if needs_reload:
        fresh = _load_index(project_id)
        if fresh is not None:
            index = fresh

    # ── 3. Gather chunks, filtering deleted docs ──────────────────────────────
    all_chunks: List[str] = []
    chunk_meta: List[tuple] = []  # (document_id, filename)

    for entry in index.get("documents", []):
        did = entry["document_id"]
        # Skip documents that no longer exist in the DB
        if did not in db_by_id:
            continue
        for chunk in entry.get("chunks", []):
            if chunk:
                all_chunks.append(chunk)
                chunk_meta.append((did, entry["filename"]))

    # ── 4. No chunks → empty results ─────────────────────────────────────────
    if not all_chunks:
        return []

    # ── 5. Rank via ZvecBlock ─────────────────────────────────────────────────
    texts = [query] + all_chunks
    try:
        zvec_result = await ZvecBlock().process("", {
            "operation": "similarity",
            "texts": texts,
        })
    except Exception:
        return []

    if zvec_result.get("status") != "success":
        return []
    matrix = zvec_result.get("similarity_matrix")
    if not matrix:
        return []

    # Row 0 = query row; columns 1..N are similarities to chunks
    row0 = matrix[0]
    chunk_scores = row0[1:]  # one score per chunk

    # ── 6. Best chunk per document, sort, top_k ───────────────────────────────
    best: Dict[str, dict] = {}  # document_id → {filename, chunk, score}
    for i, score in enumerate(chunk_scores):
        did, fname = chunk_meta[i]
        if did not in best or score > best[did]["score"]:
            best[did] = {
                "document_id": did,
                "filename": fname,
                "chunk": all_chunks[i],
                "score": score,
            }

    ranked = sorted(best.values(), key=lambda x: x["score"], reverse=True)
    ranked = ranked[:top_k]

    # ── 7. Build output ───────────────────────────────────────────────────────
    results: List[Dict[str, Any]] = []
    for item in ranked:
        snippet = " ".join(item["chunk"].split()[:50])
        results.append({
            "document_id": item["document_id"],
            "filename": item["filename"],
            "snippet": snippet,
            "score": round(float(item["score"]), 4),
        })

    return results
