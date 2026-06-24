"""High-level retrieval — the unit the chat block and the HTTP route call.

Composes the embedder + the vector store into a single ``retrieve()``
call. All public callers should go through this module rather than
talking to ``Embedder`` / ``VectorStore`` directly; the composition is
where caching, dimension matching, and graceful-degradation policy live.
"""

from __future__ import annotations

import logging
from typing import List

from app.core.rag.embeddings import Embedder, get_embedder
from app.core.rag.vector_store import Chunk, get_store

import os
import re


_NOISE_DEFAULT = r"^(~\$|nambae-menu|SandsChina_Application)"


def _noise_regex():
    """Compile the active noise regex. Re-reads env every call so
    tests / operators can flip RAG_NOISE_FILENAME_REGEX live."""
    return re.compile(os.getenv("RAG_NOISE_FILENAME_REGEX", _NOISE_DEFAULT))


def _is_noise_filename(name: str) -> bool:
    """True iff the document filename matches the noise regex.

    Used to drop accumulated garbage docs (lockfiles, unrelated pptx
    menus, etc.) from the retrieval candidate pool BEFORE top-K
    selection, so they cannot displace a relevant chunk.
    """
    if not name:
        return False
    return bool(_noise_regex().match(name))


logger = logging.getLogger(__name__)


def available() -> bool:
    """True when retrieval is functional in this process.

    Reports True when either the real embedding stack is importable OR
    the configured model is the test-mode "fake" embedder — that way
    test suites that swap ``RAG_EMBEDDING_MODEL=fake`` go through the
    same code path as production rather than short-circuiting to "unavailable."

    False is the signal callers (chat, route) treat as "skip retrieval"
    rather than treating empty results as "no matches."
    """
    import os as _os
    if _os.getenv("RAG_EMBEDDING_MODEL") == "fake":
        return True
    return Embedder.available()


def chunk_text(text: str, max_chars: int = 512, overlap: int = 50) -> List[str]:
    """Sliding-window chunker. Plain and deterministic — no spaCy, no
    LangChain, no semantic segmenter. Good enough for keyword-flavored
    retrieval over construction docs; the doc indexer's own
    ``chunk_text`` covers fancier cases when needed.

    Empty / whitespace-only input → empty list.
    """
    if not text or not text.strip():
        return []
    if max_chars <= overlap:
        raise ValueError(f"max_chars ({max_chars}) must exceed overlap ({overlap})")
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    step = max_chars - overlap
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return chunks


def _doc_name_for_id(doc_id: str) -> str:
    """Resolve a doc_id to its original filename. Returns '' if not
    found - the noise filter treats unknown names as non-noise so a
    schema mismatch never silently drops a real document."""
    try:
        from app.core import projects as _projects
        doc = _projects.get_document(doc_id)
        return (doc or {}).get("original_name") or ""
    except Exception:
        return ""


def retrieve(
    query: str,
    project_id: str,
    k: int = 5,
) -> List[Chunk]:
    """Backwards-compatible: returns top-K AFTER the noise filter."""
    chunks, _ = retrieve_with_filter(query, project_id, k=k)
    return chunks


def _general_knowledge_project_ids() -> List[str]:
    """Project ids whose chunks count as cross-project general knowledge —
    queried alongside the active project on every retrieval.

    Configured via ``RAG_GENERAL_KNOWLEDGE_PROJECTS`` (comma-separated).
    Defaults to ``training_material`` which holds the 8 procedure +
    scanned-reference folders migrated in PR #93. Set to the empty
    string to disable the merge (the retriever then queries the active
    project only — the pre-PR-107 behavior).
    """
    raw = os.getenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "training_material")
    return [p.strip() for p in raw.split(",") if p.strip()]


def retrieve_with_filter(
    query: str,
    project_id: str,
    k: int = 5,
) -> tuple:
    """Returns ``(chunks, noise_filtered_count)``.

    Pulls ``max(k*4, 20)`` raw candidates from the active project's
    vector store, then ALSO pulls the same over-fetch from each
    general-knowledge project (``training_material`` by default — see
    ``_general_knowledge_project_ids``). The two candidate sets are
    merged, re-ranked by vector score descending, noise-filtered, and
    the top K returned.

    Behaviour notes:
      * The active project is queried first so its chunks appear
        before GK chunks on equal scores (stable Python sort).
      * GK projects equal to ``project_id`` are skipped (no
        double-counting).
      * A GK lookup failure NEVER breaks the primary query — it is
        logged + the active-only results stand.
      * When ``RAG_GENERAL_KNOWLEDGE_PROJECTS=""``, no GK lookup runs
        and the retriever behaves as it did pre-PR-107.

    The audit log records ``noise_filtered_count`` so the regex can be
    tuned from data.
    """
    if not available():
        logger.debug("retrieve called but embedding stack not available; returning []")
        return [], 0
    if not query or not query.strip():
        return [], 0
    if not project_id:
        raise ValueError("project_id is required")

    embedder = get_embedder()
    query_vec = embedder.encode([query])[0]
    store = get_store(dim=embedder.dim)
    over_fetch = max(k * 4, 20)

    # Active project (operator's own corpus — first so it wins ties).
    raw_active = store.search(project_id, query_vec, k=over_fetch, query_text=query)

    # General-knowledge projects (cross-project background context).
    gk_ids = [pid for pid in _general_knowledge_project_ids() if pid != project_id]
    raw_gk: List[Chunk] = []
    for gk_pid in gk_ids:
        try:
            raw_gk.extend(store.search(gk_pid, query_vec, k=over_fetch, query_text=query))
        except Exception as exc:  # noqa: BLE001 — never let GK break primary path
            logger.warning(
                "general-knowledge retrieval for %s failed: %s; primary results stand",
                gk_pid, exc,
            )

    # Photo chunks (V2 — kind='photo'). BM25 over caption + class labels.
    # V1 photos have project_id NULL, so they surface in every project's
    # retrieval results until Phase 3 backfills project scope.
    raw_photos: List[Chunk] = []
    try:
        raw_photos = store.bm25_search_photos(query, k=over_fetch, project_id=project_id)
    except Exception as exc:  # noqa: BLE001 — never let photo leg break text retrieval
        logger.warning("photo bm25 leg failed: %s; text-only results stand", exc)

    # Merge then sort by score desc. Python sort is stable so active-project
    # chunks appear before GK chunks at identical scores.
    combined: List[Chunk] = list(raw_active) + raw_gk + raw_photos
    combined.sort(key=lambda c: -(c.score or 0))

    kept: List[Chunk] = []
    noise_dropped = 0
    for c in combined:
        name = _doc_name_for_id(c.doc_id)
        if _is_noise_filename(name):
            noise_dropped += 1
            continue
        kept.append(c)
        if len(kept) == k:
            break
    return kept, noise_dropped


def index_chunks(
    project_id: str,
    doc_id: str,
    chunks: List[str],
) -> int:
    """Embed ``chunks`` and write them to the store for retrieval.

    Returns the number of chunks indexed. Returns 0 silently when the
    embedding stack isn't installed — the doc indexer treats this as
    "RAG is off, nothing to do."
    """
    if not available():
        return 0
    if not chunks:
        return 0
    embedder = get_embedder()
    embeddings = embedder.encode(chunks)
    store = get_store(dim=embedder.dim)
    return store.upsert_chunks(project_id, doc_id, chunks, embeddings)
