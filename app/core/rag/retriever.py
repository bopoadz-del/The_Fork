"""High-level retrieval — the unit the chat block and the HTTP route call.

Composes the embedder + the vector store into a single ``retrieve()``
call. All public callers should go through this module rather than
talking to ``Embedder`` / ``VectorStore`` directly; the composition is
where caching, dimension matching, and graceful-degradation policy live.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Set, Tuple

from app.core.rag.embeddings import Embedder, get_embedder
from app.core.rag.vector_store import Chunk, get_store

import os
import re


_NOISE_DEFAULT = r"^(~\$|nambae-menu|SandsChina_Application)"

# Construction reference labels used to anchor identifier extraction.
# These are generic categories, not project-specific values.
_REFERENCE_LABELS = (
    "BOQ", "Clause", "Contract", "Doc", "Document", "Drawing",
    "Item", "NCR", "Package", "PRC", "Ref", "Reference", "RFI",
    "Rev", "Revision", "Schedule", "Spec", "Specification", "VO",
    "Variation Order",
)

# Regex components for extract_query_identifiers.
_QUOTED_RE = re.compile(r'["“]([^"”]{4,})["”]|\'([^\']{4,})\'')
_CODE_TOKEN_RE = re.compile(r"\b[A-Z]{2,}(?:[-./][A-Z0-9]+)+\b")
# Named capture ``label`` keeps the category word (VO, RFI, PRC, ...)
# separate from the captured ``code``.
_LABELED_REF_FULL_RE = re.compile(
    r"\b(?P<label>" + "|".join(re.escape(l) for l in _REFERENCE_LABELS) + r")"
    r"\s*(?:No|Ref|Number|#)?\s*[:\-]?\s*"
    r"(?P<code>[A-Za-z0-9][A-Za-z0-9\-./]*)",
    re.IGNORECASE,
)
# Mixed/lowercase code-shaped tokens that clearly contain a digit, e.g.
# D999.46, 12-A, revision-3.  The token may contain dots/dashes/slashes.
_ALPHANUMERIC_RE = re.compile(
    r"\b(?=[A-Za-z0-9./\-]*\d)[A-Za-z0-9]{2,}(?:[./\-][A-Za-z0-9]{1,})+\b"
)

_STOPWORDS: Set[str] = {
    "this", "that", "with", "from", "have", "what", "when", "where",
    "which", "about", "please", "thank", "thanks", "hello", "help",
}


def extract_query_identifiers(query: str) -> List[str]:
    """Pull construction reference identifiers out of a user query.

    Detects, without hardcoding any specific value:
      * quoted phrases (preserved as exact-match candidates)
      * code-shaped tokens such as PRC-501, IP-INF-054-0000-...
      * labeled references such as "VO Ref 31", "RFI 42", "Clause 13.1"
      * alphanumeric tokens that clearly contain a digit (e.g. D999.46)

    Returns a deduplicated list of lowercase identifier strings. The list
    is empty for queries that contain no identifier-like tokens.
    """
    if not query:
        return []

    found: Set[str] = set()

    # 1. Quoted phrases (preserve exact content).
    for m in _QUOTED_RE.finditer(query):
        phrase = (m.group(1) or m.group(2) or "").strip()
        if phrase and len(phrase) >= 3:
            found.add(phrase.lower())

    # 2. Code-shaped tokens (hyphen/dotted/dashed uppercase codes).
    for m in _CODE_TOKEN_RE.finditer(query):
        token = m.group(0).strip("-.:/")
        if len(token) >= 4:
            found.add(token.lower())

    # 3. Labeled references: "VO Ref 31", "PRC-501", "RFI 12-A", etc.
    for m in _LABELED_REF_FULL_RE.finditer(query):
        label = m.group("label")
        # The captured code may have trailing punctuation; strip it.
        code = m.group("code").strip("-.:,;")
        # A genuine reference code carries a digit (VO 99, Clause 13.1,
        # PRC-501). Several labels ("Contract", "Spec", "Package", ...) are
        # also ordinary English words, so a label followed by a digit-less
        # word ("contract cover", "specification") is prose — NOT a reference.
        # Without this guard those false identifiers earned the +2.0 retrieval
        # bonus and flooded the top-K with boilerplate, so grounded chat
        # answered "I cannot find" for broad questions (2026-06-30 pilot).
        if code and any(ch.isdigit() for ch in code):
            found.add(f"{label.lower()} {code.lower()}")
            found.add(code.lower())

    # 4. Standalone alphanumeric codes containing digits.
    for m in _ALPHANUMERIC_RE.finditer(query):
        token = m.group(0).strip("-.:,;")
        if len(token) >= 5:
            found.add(token.lower())

    # Filter out trivial stopwords and very short tokens.
    result = [
        t for t in found
        if len(t) >= 2 and t not in _STOPWORDS
    ]
    # Prefer longer, more specific identifiers first.
    result.sort(key=lambda t: (-len(t), t))
    return result


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


def _project_has_any_chunks(store, project_id: str) -> bool:
    """True iff the project (or any configured GK project) has indexed chunks."""
    if store.count(project_id) > 0:
        return True
    for pid in _general_knowledge_project_ids():
        if pid != project_id and store.count(pid) > 0:
            return True
    return False


def project_is_rag_ready(project_id: str) -> bool:
    """Public guard: is there any corpus to retrieve from for this project?"""
    if not project_id:
        return False
    if not available():
        return False
    try:
        store = get_store(dim=get_embedder().dim)
        return _project_has_any_chunks(store, project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("project_is_rag_ready check failed: %s", exc)
        return False


# General-knowledge relevance boost — lift a curated reference chunk (units /
# CESMM / POMI / FIDIC in the GK project) that LEXICALLY overlaps the query, so
# everyday phrasings surface it even when pure cosine ranks it just below the
# active project's own chunks. Capped well under IDENTIFIER_BONUS_MAX (2.0) so
# exact-code lookups still win, and relevance-gated (only overlapping GK chunks
# are boosted) so it never displaces a strongly-matched project chunk.
_GK_TERM_BONUS = 0.25
_GK_BONUS_CAP = 1.2
_GK_STOPWORDS = frozenset({
    "what", "which", "when", "where", "whom", "whose", "does", "did", "how",
    "the", "and", "for", "are", "was", "were", "this", "that", "these", "those",
    "from", "with", "into", "your", "our", "their", "please", "tell", "give",
    "answer", "question", "about", "standard", "project", "document", "documents",
    "knowledge", "base", "using", "used", "there", "here", "have", "has", "will",
})


def _significant_terms(query: str) -> frozenset:
    """Content words (>=4 chars, minus stopwords) used for lexical overlap."""
    import re as _re
    return frozenset(
        w for w in _re.findall(r"[a-z0-9]{4,}", (query or "").lower())
        if w not in _GK_STOPWORDS
    )


def _gk_lexical_bonus(query_terms: frozenset, chunk_text: str) -> float:
    """Bonus for a GK chunk = capped count of distinct query terms it contains."""
    if not query_terms or not chunk_text:
        return 0.0
    text = chunk_text.lower()
    overlap = sum(1 for t in query_terms if t in text)
    return min(overlap * _GK_TERM_BONUS, _GK_BONUS_CAP)


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

    **Identifier-aware precision:** if the query contains construction
    reference identifiers (VO/RFI/NCR/PRC/drawing codes/etc.), the
    retriever also performs a case-insensitive substring search over
    chunk text and boosts matching chunks above pure semantic hits.
    This prevents a high-cosine generic boilerplate chunk from
    outranking the exact document that contains the requested code.

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
    # The GK corpus is small and curated (units / CESMM / FIDIC / procedures), so
    # over-fetch it generously: a lexically-relevant reference chunk must enter
    # the candidate pool even when its semantic score for a broad query is low
    # -- the lexical boost below can only re-rank chunks that made the fetch.
    gk_over_fetch = max(k * 12, 80)

    # Active project (operator's own corpus — first so it wins ties).
    raw_active = store.search(project_id, query_vec, k=over_fetch, query_text=query)

    # General-knowledge projects (cross-project background context).
    gk_ids = [pid for pid in _general_knowledge_project_ids() if pid != project_id]
    raw_gk: List[Chunk] = []
    for gk_pid in gk_ids:
        try:
            raw_gk.extend(store.search(gk_pid, query_vec, k=gk_over_fetch, query_text=query))
        except Exception as exc:  # noqa: BLE001 — never let GK break primary path
            logger.warning(
                "general-knowledge retrieval for %s failed: %s; primary results stand",
                gk_pid, exc,
            )

    # Identifier-aware lexical rescue for exact reference lookups.
    identifiers = extract_query_identifiers(query)
    id_candidates: Dict[str, Tuple[Chunk, float]] = {}
    if identifiers:
        try:
            id_active = store.identifier_search(project_id, identifiers, k=over_fetch)
            for c in id_active:
                id_candidates[c.chunk_id] = (c, c.score or 0.0)
            for gk_pid in gk_ids:
                try:
                    id_gk = store.identifier_search(gk_pid, identifiers, k=over_fetch)
                    for c in id_gk:
                        # Active-project identifier hits win ties over GK.
                        if c.chunk_id not in id_candidates:
                            id_candidates[c.chunk_id] = (c, c.score or 0.0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "identifier search for GK %s failed: %s", gk_pid, exc
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("identifier search failed: %s; falling back to semantic", exc)

    # Fuse semantic and identifier signals.
    # Semantic chunks carry their cosine score; identifier hits add a
    # bonus proportional to how many identifiers they match. A chunk that
    # matches all requested identifiers receives a +2.0 bonus, which is
    # larger than any pure semantic score, guaranteeing it outranks
    # semantically-similar boilerplate that lacks the exact reference.
    fused: Dict[str, Tuple[Chunk, float]] = {}
    for c in list(raw_active) + raw_gk:
        fused[c.chunk_id] = (c, c.score or 0.0, 0.0)

    IDENTIFIER_BONUS_MAX = 2.0
    for chunk_id, (id_chunk, id_score) in id_candidates.items():
        if chunk_id in fused:
            sem_chunk, sem_score, _ = fused[chunk_id]
            fused[chunk_id] = (sem_chunk, sem_score, id_score * IDENTIFIER_BONUS_MAX)
        else:
            # Identifier-only hit: keep its text but start from zero semantic.
            fused[chunk_id] = (id_chunk, 0.0, id_score * IDENTIFIER_BONUS_MAX)

    # General-knowledge lexical boost: lift GK reference chunks that overlap the
    # query so everyday phrasings surface curated references (units/CESMM/FIDIC).
    q_terms = _significant_terms(query)
    for gk_chunk_id in {c.chunk_id for c in raw_gk}:
        entry = fused.get(gk_chunk_id)
        if entry is None:
            continue
        gk_chunk, sem_score, bonus = entry
        add = _gk_lexical_bonus(q_terms, gk_chunk.text)
        if add:
            fused[gk_chunk_id] = (gk_chunk, sem_score, bonus + add)

    scored: List[Tuple[float, Chunk]] = []
    for chunk, sem_score, id_bonus in fused.values():
        final_score = (sem_score or 0.0) + (id_bonus or 0.0)
        chunk.score = round(final_score, 6)
        scored.append((final_score, chunk))

    # Sort by fused score descending; active-project chunks naturally come
    # first when scores are equal because they were inserted first.
    scored.sort(key=lambda x: -x[0])

    # Photo chunks RAG leg was removed in migration 0008 along with the
    # photo_chunks table. Chat-attached photos are now question-context
    # (see POST /v1/chat/analyze-photo), not corpus material.
    kept: List[Chunk] = []
    noise_dropped = 0
    for _, c in scored:
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
