"""High-level retrieval — the unit the chat block and the HTTP route call.

Composes the embedder + the vector store into a single ``retrieve()``
call. All public callers should go through this module rather than
talking to ``Embedder`` / ``VectorStore`` directly; the composition is
where caching, dimension matching, and graceful-degradation policy live.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple

from app.core.rag.embeddings import Embedder, get_embedder
from app.core.rag.vector_store import Chunk, get_store
from app.core.rag import layers
from app.core.rag import revision as _revision
from app.core.rag import reranker as _reranker

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


def _is_prose_compound(token: str) -> bool:
    """True if a hyphen/dot/slash token contains a full English-word segment
    (all-alpha, >4 chars) — e.g. '30-storey', '12-month', 'revision-3'. These
    are descriptive compounds, NOT reference codes (whose alpha parts are short
    abbreviations: TL, PRC, IP). Without this guard a generative request like
    "risk register for a 30-storey tower" extracted '30-storey' as a reference,
    which then (on a retrieval miss) wrongly fired the missing-reference
    short-circuit — answering "provide the exact filename" to a generate request.
    """
    for seg in re.split(r"[-./]", token):
        if seg.isalpha() and len(seg) > 4:
            return True
    return _is_misspelled_word(token)


# A separator-free token is only a reference code when its letters are a SHORT
# abbreviation prefix: M145, A615, D999, PRC501, IP054. A long alphabetic run
# with a digit buried inside it is a TYPO, not a code.
#
# Live incident 2026-08-02: the operator typed "You should find it in the
# project specif8cation not drawings" — a conversational correction. The '8'
# in the misspelling made "specif8cation" match rule 4, retrieval missed on
# it, and the missing-reference short-circuit answered "I could not confirm
# this reference in the indexed project sources ... provide the exact
# filename." A typo silently converted a correction into a failed document
# lookup.
#
# _is_prose_compound could not catch it: that guard splits on [-./] and checks
# for all-alpha segments, but a separator-free token yields ONE segment which
# isn't .isalpha() precisely BECAUSE of the stray digit.
# The discriminator is the ALPHABETIC RUN. Reference codes are built from
# short abbreviations (M145, A615, D999, PRC501, IP054 — runs of 1-3 letters).
# An English word carries runs of 5+ letters, and a typo'd digit does not
# change that: "specif8cation" still contains "specif" and "cation".
#
# This also catches the same typo arriving via the LABELED-reference rule,
# which happily split "specif8cation" into label "spec" + code "if8cation" —
# and "if8cation" satisfies any letters-then-digits shape test, so only the
# run-length check rejects it.
_MAX_CODE_ALPHA_RUN = 4


def _is_misspelled_word(token: str) -> bool:
    """True for a separator-free word with a digit typo'd into it."""
    if re.search(r"[-./]", token):
        return False  # separator tokens are handled by the segment rule above
    if not any(ch.isdigit() for ch in token):
        return False
    return any(
        len(run) > _MAX_CODE_ALPHA_RUN
        for run in re.findall(r"[A-Za-z]+", token)
    )


# Measurement units / unit-ratios are NOT reference codes. A spec unit in the
# query (e.g. "concrete 250 kg/cm2") otherwise slips through _ALPHANUMERIC_RE
# (letters + a digit + a slash) and earns the +2.0 identifier bonus, which then
# matches every drawing dimension-table chunk that happens to contain the same
# number — burying the real answer and inducing a fabricated figure lifted from
# the number-soup (2026-07-14 live cost-query incident). Unit atoms are short
# symbols; a trailing exponent digit (m2, m3, cm2, mm2) is stripped before the
# vocabulary check. Reference codes (IP-INF-054, PRC-501, D999.46) are NOT unit
# atoms, so they survive untouched.
_UNIT_ATOMS = frozenset({
    "kg", "g", "mg", "t", "ton", "tonne", "lb", "kn", "mn", "n",
    "pa", "kpa", "mpa", "gpa", "bar", "psi",
    "mm", "cm", "m", "km", "in", "ft", "yd", "mil",
    "sqm", "cum", "rm", "lm", "ha",
    "l", "ml", "kl", "cc",
    "s", "sec", "min", "hr", "h",
    "w", "kw", "mw", "kwh", "wh", "v", "kv", "a", "ma", "hz", "khz",
    "c", "f", "k",
    "pcs", "pc", "no", "nos", "ea", "each", "unit",
})


def _strip_exponent(seg: str) -> str:
    """'cm2' -> 'cm', 'm3' -> 'm', 'mm2' -> 'mm'; leaves 'd999' unchanged
    (only a SINGLE trailing exponent digit after an alpha base is stripped)."""
    m = re.fullmatch(r"([a-z]{1,4})([23])", seg)
    return m.group(1) if m else seg


def _looks_like_unit(token: str) -> bool:
    """True when the token is a measurement unit or unit-ratio (kg/cm2, n/mm2,
    kn/m3, m3) rather than a document reference code. Ratios split on '/' (or the
    middot); every part, once its exponent is stripped, must be a known unit
    atom. A bare single unit (m3) also qualifies. Reference codes use '-'/'.'
    separators and non-unit alpha stems, so they are never flagged."""
    t = token.lower().strip()
    parts = [p for p in re.split(r"[/·]", t) if p]
    if not parts:
        return False
    if all(_strip_exponent(p) in _UNIT_ATOMS for p in parts):
        return True
    return False


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
        # ...and it must not be a typo'd English word. This rule cheerfully
        # split "specif8cation" into label "spec" + code "if8cation", which
        # carries a digit and so passed the check above (live 2026-08-02).
        if (
            code
            and any(ch.isdigit() for ch in code)
            and not _is_misspelled_word(code)
        ):
            found.add(f"{label.lower()} {code.lower()}")
            found.add(code.lower())

    # 4. Standalone alphanumeric codes containing digits.
    for m in _ALPHANUMERIC_RE.finditer(query):
        token = m.group(0).strip("-.:,;")
        if len(token) >= 5 and not _is_prose_compound(token):
            found.add(token.lower())

    # Filter out trivial stopwords, very short tokens, and measurement units
    # (a spec unit like "kg/cm2" is not a reference code — see _looks_like_unit).
    result = [
        t for t in found
        if len(t) >= 2 and t not in _STOPWORDS and not _looks_like_unit(t)
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
    *,
    intent: Optional[str] = None,
) -> List[Chunk]:
    """Backwards-compatible: returns top-K AFTER the noise filter."""
    chunks, _ = retrieve_with_filter(query, project_id, k=k, intent=intent)
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
    ids = [p.strip() for p in raw.split(",") if p.strip()]
    # STEP 0 structural isolation: the master-corpus / client fallback corpus
    # is NEVER part of the always-on GK merge, even when a stale env still lists
    # it (prod once had drive_archive + client_infra_pack_1 in this var, silently
    # merging the whole the client project client corpus into every OTHER project's results —
    # the ha_long -> the client project leak). It may only surface as the disclosed empty/thin
    # fallback below. This makes the client corpus structurally unreachable from
    # another project's populated query regardless of score.
    fb = _master_corpus_fallback_id()
    if fb:
        ids = [p for p in ids if p != fb]
    return ids


def _master_corpus_fallback_id() -> Optional[str]:
    """The corpus queried ONLY when the active project is empty/thin, and always
    disclosed as a Master-Corpus fallback (STEP 0b).

    This is the client/master corpus (``MASTER_CORPUS_SOURCE_PROJECT_ID``) —
    deliberately kept SEPARATE from the general-knowledge layer so it can never
    silently blend into another project's results. Returns None when unset (the
    CI / self-host default), so the empty-project contract stays ``[]`` there.
    """
    pid = (os.getenv("MASTER_CORPUS_SOURCE_PROJECT_ID") or "").strip()
    return pid or None


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


# Intent classes gating the GK-contamination knobs (RAG_GK_SCORE_MARGIN /
# RAG_OWN_DOC_BOOST / RAG_GK_TOPK_CAP). RAG_AUDIT_V2 found curated GK notes
# outranking a user's own uploads 9/12 times, but calculation/standards
# features DEPEND on GK winning (unit tables, CESMM/POMI, FIDIC clauses) — so
# the knobs apply only to lookup-shaped retrieval. intent=None (the chat path
# today, and any caller that doesn't classify) counts as lookup.
DOC_LOOKUP_INTENTS = frozenset({"document_lookup", "project_lookup", "doc_qa"})
CALC_KB_INTENTS = frozenset({"calculation", "standards", "knowledge"})


def _knob_float(name: str) -> Optional[float]:
    """Env-driven ranking knob. Unset/blank/unparsable means OFF (None) so a
    typo'd value can never silently change ranking; 0.0 is a valid ON value."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; knob disabled", name, raw)
        return None


def _knob_int(name: str) -> Optional[int]:
    """Integer variant of _knob_float; same OFF-on-garbage contract."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; knob disabled", name, raw)
        return None


def _knob_flag(name: str) -> bool:
    """Boolean variant of _knob_float; unset/blank/garbage means OFF so a
    typo'd value can never silently change ranking."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("invalid %s=%r; knob disabled", name, raw)
    return False


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


# Dual-query retrieval (F18, phase-3 campaign). Measured on a 203-page
# contract and a 129-page tender: the needle chunk ranks FIRST for a query
# whose wording overlaps the answer's, and falls out of the top-12 for the
# same fact asked as a natural question — the interrogative scaffolding
# ("In the X, how many days is ...") drags the query vector away from the
# declarative prose of the document. Stopword stripping does NOT fix it
# (it breaks the noun phrases that carry the signal); what does, verified
# by live probes, is removing ONLY the wrapper while keeping every content
# phrase contiguous: "In the Conditions of Contract, how many days is the
# Time for Completion for the whole of the Works?" -> "days is the Time
# for Completion for the whole of the Works" moved the needle from
# outside the top-12 to rank 1 (0.933). The retriever therefore searches
# with BOTH phrasings and merges candidates by max score per chunk.
_WRAPPER_LEAD_RX = re.compile(
    # a short "In/From/Per/According to <source>," clause before the question
    r"^(?:in|from|under|per|according\s+to|based\s+on|as\s+per)\s+[^,]{1,60},\s*",
    re.IGNORECASE,
)
_WRAPPER_IMPERATIVE_RX = re.compile(
    r"^(?:please\s+)?(?:tell\s+me|give\s+me|show\s+me|state|list|specify)\s+",
    re.IGNORECASE,
)
_WRAPPER_TOKEN_RX = re.compile(
    # interrogative tokens only -- NEVER articles/copulas ("is", "the", "of"),
    # which are the glue inside the phrases that must stay contiguous
    r"\b(?:how\s+(?:many|much|long)|what|which|who|whose|when|why|does|did|please)\b",
    re.IGNORECASE,
)


def _strip_question_wrapper(query: str) -> Optional[str]:
    """The wrapper-stripped, phrase-intact variant of a natural question,
    or None when stripping changes nothing (terse queries cost no second
    search). Interior word order and every content phrase are preserved."""
    base = (query or "").strip().rstrip("?").strip()
    if not base:
        return None
    s = _WRAPPER_LEAD_RX.sub("", base)
    s = _WRAPPER_IMPERATIVE_RX.sub("", s)
    s = _WRAPPER_TOKEN_RX.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,.;:")
    if len(s.split()) < 3 or s.lower() == base.lower():
        return None
    return s


def _dual_query_enabled() -> bool:
    """ON by default -- RAG_DUAL_QUERY=0/false/no/off is the kill-switch."""
    return (os.getenv("RAG_DUAL_QUERY") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _dual_search(
    store, project_id: str, query_vec, query: str,
    alt_vec, alt_query: Optional[str], *, k: int,
) -> List[Chunk]:
    """Search with the raw query and (when present) the wrapper-stripped
    variant; merge by chunk_id keeping each chunk's BEST score. The alt
    leg failing can never break the primary results."""
    hits = store.search(project_id, query_vec, k=k, query_text=query)
    if alt_vec is None:
        return hits
    try:
        alt_hits = store.search(project_id, alt_vec, k=k, query_text=alt_query)
    except Exception as exc:  # noqa: BLE001 -- alt leg is best-effort
        logger.warning(
            "dual-query alt retrieval for %s failed: %s; primary results stand",
            project_id, exc,
        )
        return hits
    best: Dict[str, Chunk] = {c.chunk_id: c for c in hits}
    for c in alt_hits:
        prev = best.get(c.chunk_id)
        if prev is None or (c.score or 0.0) > (prev.score or 0.0):
            best[c.chunk_id] = c
    return sorted(best.values(), key=lambda c: -(c.score or 0.0))


def retrieve_with_filter(
    query: str,
    project_id: str,
    k: int = 5,
    *,
    intent: Optional[str] = None,
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

    **GK contamination knobs (all default OFF — unset env var means the
    merge is bit-identical to the description above):** RAG_AUDIT_V2
    measured curated GK notes beating a user's own uploads 9/12 times,
    because GK candidates compete purely on score. Three independent,
    env-driven counters:

      * ``RAG_GK_SCORE_MARGIN`` (float) — a GK candidate survives only
        when its score >= best active-project candidate's score + margin.
        A project with no candidates leaves GK untouched (the GK-only
        fallback for empty projects must keep working).
      * ``RAG_OWN_DOC_BOOST`` (float) — additive boost on active-project
        candidates (ownership, not recency) before the merged re-rank.
      * ``RAG_GK_TOPK_CAP`` (int) — at most this many GK chunks in the
        final top-K; excess GK slots backfill with the next-best project
        chunks, or shrink the result when none remain.
      * ``RAG_GK_LEXICAL_FOLD`` (bool flag) — folds the GK lexical bonus
        inside the margin gate: the H1 comparison runs on each GK chunk's
        raw fused score (lexical bonus subtracted), so a GK note that only
        outranks the project's own document because of its keyword bonus
        is gated out. Survivors keep the bonus for ordering. No-op unless
        ``RAG_GK_SCORE_MARGIN`` is also set.

    The knobs apply only when ``intent`` is None or in DOC_LOOKUP_INTENTS;
    any other intent (notably CALC_KB_INTENTS) bypasses them so features
    that depend on GK winning are unaffected.

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
    query_vec = embedder.encode_queries([query])[0]
    # F18 dual-query: also embed the wrapper-stripped phrase-intact variant
    # of a natural question (None for terse queries -- no extra cost).
    alt_query = _strip_question_wrapper(query) if _dual_query_enabled() else None
    alt_vec = embedder.encode_queries([alt_query])[0] if alt_query else None
    store = get_store(dim=embedder.dim)
    over_fetch = max(k * 4, 20)
    # The GK corpus is small and curated (units / CESMM / FIDIC / procedures), so
    # over-fetch it generously: a lexically-relevant reference chunk must enter
    # the candidate pool even when its semantic score for a broad query is low
    # -- the lexical boost below can only re-rank chunks that made the fetch.
    gk_over_fetch = max(k * 12, 80)

    # Active project (operator's own corpus — first so it wins ties).
    raw_active = _dual_search(
        store, project_id, query_vec, query, alt_vec, alt_query, k=over_fetch,
    )

    # STEP 0b — empty/thin detection for the labeled Master-Corpus fallback.
    # "Thin" reuses RAG_CONFIDENCE_THRESHOLD (the same bar rag_inject applies):
    # a project whose best own chunk can't clear it has nothing usable of its
    # own, so we disclose-and-fall-back to the Master Corpus rather than answer
    # from thin air. Empty (no own chunks) is the degenerate thin case.
    own_top = max((c.score or 0.0) for c in raw_active) if raw_active else 0.0
    fallback_min = float(os.getenv("RAG_CONFIDENCE_THRESHOLD", "0.4"))
    own_thin = own_top < fallback_min

    # General-knowledge projects (cross-project background context).
    # Only merge GK when the active project already has indexed chunks.
    # An empty/unindexed project must return [] — not training_material
    # hits — or search_project_documents, lazy bootstrap, and the
    # "unindexed project" contract all break (Postgres CI shares a DB where
    # GK rows exist from other tests / the migrated corpus).
    # Prefer authoritative corpus-size check, but fall back to the fetched
    # active candidates for mocked/in-memory test stores that don't model
    # ``count`` consistently with ``search``.
    include_gk = store.count(project_id) > 0 or bool(raw_active)
    gk_ids = (
        [pid for pid in _general_knowledge_project_ids() if pid != project_id]
        if include_gk
        else []
    )
    raw_gk: List[Chunk] = []
    for gk_pid in gk_ids:
        try:
            raw_gk.extend(store.search(gk_pid, query_vec, k=gk_over_fetch, query_text=query))
        except Exception as exc:  # noqa: BLE001 — never let GK break primary path
            logger.warning(
                "general-knowledge retrieval for %s failed: %s; primary results stand",
                gk_pid, exc,
            )

    # STEP 0b — labeled Master-Corpus fallback. Queried ONLY when the active
    # project is empty/thin, and NEVER silently: the chunks are tagged
    # ``layer="master_corpus"`` so the chat runtime discloses the fallback in
    # the answer and the sources panel. The fallback corpus is barred from the
    # GK merge (see _general_knowledge_project_ids), so this is the ONLY way it
    # can surface for another project — and only with disclosure.
    fb_id = _master_corpus_fallback_id()
    use_fallback = (
        own_thin
        and bool(fb_id)
        and fb_id != project_id
        and fb_id not in gk_ids
    )
    raw_fb: List[Chunk] = []
    if use_fallback:
        try:
            # Dual-query applies here too: a client's contract in the Master
            # Corpus has the same declarative prose the alt variant rescues.
            raw_fb = _dual_search(
                store, fb_id, query_vec, query, alt_vec, alt_query, k=over_fetch,
            )
        except Exception as exc:  # noqa: BLE001 — fallback must never break the turn
            logger.warning(
                "master-corpus fallback retrieval for %s failed: %s", fb_id, exc,
            )
            raw_fb = []
        if not raw_fb:
            use_fallback = False

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
    for c in list(raw_active) + raw_gk + raw_fb:
        fused[c.chunk_id] = (c, c.score or 0.0, 0.0)

    IDENTIFIER_BONUS_MAX = 2.0
    for chunk_id, (id_chunk, id_score) in id_candidates.items():
        if chunk_id in fused:
            sem_chunk, sem_score, _ = fused[chunk_id]
            fused[chunk_id] = (sem_chunk, sem_score, id_score * IDENTIFIER_BONUS_MAX)
        else:
            # Identifier-only hit: keep its text but start from zero semantic.
            fused[chunk_id] = (id_chunk, 0.0, id_score * IDENTIFIER_BONUS_MAX)

    # 2026-07-26 precision fix: identifier_search returns an ARBITRARY top-k
    # of the (possibly hundreds of) chunks containing the code, so on a large
    # corpus the semantically-best chunk that ALSO carries the identifier can
    # miss the id_candidates set entirely — and then flat-bonused label-soup
    # (drawing station tables, schedule rows) displaces it. Award the same
    # bonus to every SEMANTIC candidate whose text contains the identifiers:
    # cosine + bonus then always outranks identifier-only hits (bonus alone),
    # which is the ordering the boost was designed to produce. (Live find:
    # 'WWPS-01 total flow rate' on the corpus project buried the 0.75-cosine
    # spec table under zero-semantic drawing chunks.)
    if identifiers:
        # Token-wise matching, mirroring identifier_search: "VO Ref: 99" and
        # "VO 99" both match, punctuation between tokens is ignored.
        ident_token_sets = []
        for ident in identifiers:
            toks = [t for t in re.split(r"[^a-z0-9]+", ident.lower()) if t]
            if toks:
                ident_token_sets.append(toks)
        for chunk_id, entry in list(fused.items()):
            sem_chunk, sem_score, id_bonus = entry
            # Skip entries that already carry the search-assigned bonus
            # (identifier-only hits live there with sem_score 0.0). Real
            # semantic candidates keep eligibility even at negative cosine.
            if id_bonus > 0.0 or not ident_token_sets:
                continue
            text_lower = (sem_chunk.text or "").lower()
            matched = sum(
                1 for toks in ident_token_sets
                if all(t in text_lower for t in toks)
            )
            if matched:
                local_score = matched / len(ident_token_sets)
                fused[chunk_id] = (
                    sem_chunk, sem_score, local_score * IDENTIFIER_BONUS_MAX,
                )

    # General-knowledge lexical boost: lift GK reference chunks that overlap the
    # query so everyday phrasings surface curated references (units/CESMM/FIDIC).
    # ``gk_lex_added`` records the bonus per chunk so the H1 margin gate below
    # can compare on the pre-bonus (raw fused) score when RAG_GK_LEXICAL_FOLD
    # is on. Recording here (rather than deferring the bonus until after the
    # gate) is the smaller diff: the flag-off path stays byte-identical, and
    # the gate only needs one subtraction instead of a second scoring pass.
    q_terms = _significant_terms(query)
    gk_lex_added: Dict[str, float] = {}
    for gk_chunk_id in {c.chunk_id for c in raw_gk}:
        entry = fused.get(gk_chunk_id)
        if entry is None:
            continue
        gk_chunk, sem_score, bonus = entry
        add = _gk_lexical_bonus(q_terms, gk_chunk.text)
        if add:
            fused[gk_chunk_id] = (gk_chunk, sem_score, bonus + add)
            gk_lex_added[gk_chunk_id] = add

    # GK background factor: penalise GK vs the active project's own docs. Default
    # 1.0 (OFF) — a 0.9 penalty was found to DEMOTE the authoritative curated KB
    # below a project's contract templates for knowledge questions (a FIDIC "IPC
    # payment days" query grounded on a project's amended 45-day contract instead
    # of the KB's 56-day FIDIC default), which is worse than the project-question
    # case it was meant to help. Kept as a live env knob for future tuning.
    gk_id_set = set(gk_ids)
    GK_BACKGROUND_FACTOR = float(os.getenv("RAG_GK_BACKGROUND_FACTOR", "1.0"))
    scored: List[Tuple[float, Chunk]] = []
    for chunk, sem_score, id_bonus in fused.values():
        final_score = (sem_score or 0.0) + (id_bonus or 0.0)
        if chunk.project_id in gk_id_set and final_score > 0:
            final_score *= GK_BACKGROUND_FACTOR
        chunk.score = round(final_score, 6)
        scored.append((final_score, chunk))

    # GK contamination knobs — see the docstring. Each is None (OFF) unless
    # its env var is set AND the intent is lookup-shaped; when all are None
    # the pipeline below is byte-for-byte the pre-knob behavior.
    knobs_apply = intent is None or intent in DOC_LOOKUP_INTENTS
    gk_margin = _knob_float("RAG_GK_SCORE_MARGIN") if knobs_apply else None
    own_boost = _knob_float("RAG_OWN_DOC_BOOST") if knobs_apply else None
    gk_cap = _knob_int("RAG_GK_TOPK_CAP") if knobs_apply else None
    lex_fold = _knob_flag("RAG_GK_LEXICAL_FOLD") if knobs_apply else False

    # H2: ownership boost — the active project's own chunks get an additive
    # lift before the merged re-rank, so a user's uploaded doc can beat a
    # keyword-dense GK note of similar raw score.
    if own_boost is not None:
        for i, (score, chunk) in enumerate(scored):
            if chunk.project_id == project_id:
                boosted = score + own_boost
                chunk.score = round(boosted, 6)
                scored[i] = (boosted, chunk)

    # H1: GK score margin — GK must BEAT the project's best candidate by the
    # margin to enter the pool at all. Compared after H2 so an enabled boost
    # also raises the bar. Empty projects skip the gate: GK-only fallback.
    #
    # Lexical fold (RAG_GK_LEXICAL_FOLD): RAG_AUDIT_V3 showed the GK lexical
    # bonus (+0.25/term, cap +1.2) lifts curated notes ~+0.5 above any project
    # chunk on collision queries, so a user's own contract can never clear the
    # margin comparison against a bonused GK note. When the flag is on, the
    # margin is compared on the GK chunk's RAW fused score (bonus subtracted);
    # a GK chunk that only beats the project because of its lexical bonus is
    # gated out, while one that clears the margin on raw score survives and
    # keeps its bonus for ordering. The recorded bonus is scaled by
    # GK_BACKGROUND_FACTOR because the factor multiplied the whole fused score
    # (bonus included) above; subtracting the scaled bonus recovers exactly
    # the factor-adjusted pre-bonus score.
    if gk_margin is not None:
        project_scores = [s for s, c in scored if c.project_id == project_id]
        if project_scores:
            bar = max(project_scores) + gk_margin

            def _margin_score(s: float, c: Chunk) -> float:
                if lex_fold and c.project_id in gk_id_set:
                    return s - gk_lex_added.get(c.chunk_id, 0.0) * GK_BACKGROUND_FACTOR
                return s

            scored = [
                (s, c) for s, c in scored
                if c.project_id not in gk_id_set or _margin_score(s, c) >= bar
            ]

    # Revision currency (§5.2) — ALWAYS ON, independent of RAG_LAYERED. A
    # stale-revision drawing answer is a real construction-safety hazard, so
    # this runs regardless of the layered flag. Both signals key off the
    # uploader's original filename (resolved once per distinct doc below):
    #   1. annotate every chunk with its parsed revision / drawing number;
    #   2. firmly down-rank any chunk whose filename marks it SUPERSEDED so a
    #      current document of comparable relevance always wins — a penalty, not
    #      a drop, so a superseded chunk still survives as a last resort when
    #      nothing current matches.
    # The same-drawing "prefer highest revision" suppression is computed from
    # these annotations and applied in the kept-selection loop below.
    name_by_id: Dict[str, str] = {}
    for _, chunk in scored:
        if chunk.doc_id not in name_by_id:
            name_by_id[chunk.doc_id] = _doc_name_for_id(chunk.doc_id)
    for i, (score, chunk) in enumerate(scored):
        nm = name_by_id.get(chunk.doc_id, "")
        chunk.revision = _revision.revision_token(nm)
        chunk.drawing_number = _revision.drawing_number(nm)
        if _revision.is_superseded(nm):
            chunk.superseded = True
            demoted = score - _revision.SUPERSEDED_PENALTY
            chunk.score = round(demoted, 6)
            scored[i] = (demoted, chunk)

    # Stage 3 (layered RAG): authority-precedence re-rank. Add a small term so a
    # higher-authority / higher-layer chunk (e.g. an L2B contractual clause)
    # outranks a comparably-relevant low-authority one (an L1 historical note).
    # Applied AFTER the GK-margin gate so it only changes final ordering, and
    # flag-gated: when RAG_LAYERED is off, `scored` is untouched — today's
    # ordering byte-for-byte.
    if layers.layered_enabled():
        for i, (score, chunk) in enumerate(scored):
            bonus = layers.precedence_bonus(
                getattr(chunk, "knowledge_layer", None),
                getattr(chunk, "authority", None),
            )
            if bonus:
                new_score = score + bonus
                chunk.score = round(new_score, 6)
                scored[i] = (new_score, chunk)

    # Sort by fused score descending; active-project chunks naturally come
    # first when scores are equal because they were inserted first.
    scored.sort(key=lambda x: -x[0])

    # Revision currency (§5.2 step 2): highest COMPARABLE revision retrieved per
    # drawing number, bucketed by revision kind ((drawing_number, kind) -> max
    # value). Numeric and alphabetic revisions of one sheet live in separate
    # buckets and never suppress each other — so a mixed-scheme sheet keeps both
    # rather than risk hiding the current one (see revision.revision_rank).
    best_rev: Dict[Tuple[str, int], int] = {}
    for _, c in scored:
        dn = c.drawing_number
        rank = _revision.revision_rank(c.revision)
        if dn and rank is not None:
            key = (dn, rank[0])
            if key not in best_rev or rank[1] > best_rev[key]:
                best_rev[key] = rank[1]

    # Photo chunks RAG leg was removed in migration 0008 along with the
    # photo_chunks table. Chat-attached photos are now question-context
    # (see POST /v1/chat/analyze-photo), not corpus material.
    # H3: GK top-K cap — skipping (not truncating) excess GK chunks lets the
    # next-best project chunks flow into the freed slots; when the pool has
    # no project chunks left the result simply comes back shorter.
    #
    # Cross-encoder rerank (RAG_RERANKER, default OFF): collect a DEEPER
    # candidate pool through the very same gates below, then let the
    # cross-encoder pick the best k from it. Flag off -> target == k and the
    # loop is byte-identical to before. The gates run FIRST either way, so a
    # noise-filtered, revision-suppressed, or GK-capped chunk can never be
    # resurrected by a good rerank score.
    rerank_on = _reranker.enabled()
    target = _reranker.candidate_depth(k) if rerank_on else k
    kept: List[Chunk] = []
    noise_dropped = 0
    revision_suppressed = 0
    gk_kept = 0
    for _, c in scored:
        name = name_by_id.get(c.doc_id, "")
        if _is_noise_filename(name):
            noise_dropped += 1
            continue
        # Prefer the highest revision of a given drawing: skip this chunk when a
        # strictly-higher, same-kind revision of the SAME drawing number is also
        # in the pool. Stale-revision safety, so this suppresses rather than
        # merely nudges — but only among chunks that share a parsed drawing id.
        dn = c.drawing_number
        rank = _revision.revision_rank(c.revision)
        if dn and rank is not None and best_rev.get((dn, rank[0]), rank[1]) > rank[1]:
            revision_suppressed += 1
            continue
        if gk_cap is not None and c.project_id in gk_id_set:
            if gk_kept >= gk_cap:
                continue
            gk_kept += 1
        kept.append(c)
        if len(kept) == target:
            break

    # Second-stage rerank: reorder the survivors by cross-encoder relevance
    # and cut to k. Degrades to kept[:k] (i.e. today's exact result) on any
    # model/scoring failure — see reranker.rerank.
    if rerank_on and len(kept) > k:
        kept = _reranker.rerank(query, kept, k)

    # Tag each returned chunk with its retrieval layer so the chat runtime can
    # disclose a Master-Corpus fallback (STEP 0b). "own" is the active project;
    # a chunk from the fallback corpus is "master_corpus"; anything else that
    # made it through is a disclosed general-knowledge chunk.
    for c in kept:
        if c.project_id == project_id:
            c.layer = "own"
        elif use_fallback and c.project_id == fb_id:
            c.layer = "master_corpus"
        else:
            c.layer = "general_knowledge"

    if revision_suppressed:
        logger.debug(
            "revision currency: suppressed %d lower-revision chunk(s) in favour "
            "of a higher revision of the same drawing", revision_suppressed,
        )

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
    # Layered RAG (flag-gated): tag the doc's chunks with their knowledge layer
    # (L1/L2A/L2B/L3) and authority so retrieval can rank by precedence. Off by
    # default -> (None, None), i.e. today's behaviour byte-for-byte. A doc whose
    # metadata carries provenance="user_upload" (set by the interactive upload
    # endpoint) is routed to the user_session layer (Stage 4).
    knowledge_layer = authority = None
    if layers.layered_enabled():
        name, is_user_upload = _doc_name_and_provenance(doc_id)
        knowledge_layer, authority = layers.classify(
            project_id, name, is_user_upload=is_user_upload)
    return store.upsert_chunks(
        project_id, doc_id, chunks, embeddings,
        knowledge_layer=knowledge_layer, authority=authority)


def _doc_name_and_provenance(doc_id: str) -> tuple:
    """Return ``(original_name, is_user_upload)`` for a doc. is_user_upload is
    True when the doc's metadata provenance marks it an interactive upload.
    Safe: unknown/missing doc -> ('', False)."""
    try:
        from app.core import projects as _projects
        doc = _projects.get_document(doc_id) or {}
        name = doc.get("original_name") or ""
        prov = (doc.get("metadata") or {}).get("provenance")
        return name, prov == "user_upload"
    except Exception:
        return "", False
