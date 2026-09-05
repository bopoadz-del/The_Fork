"""High-level retrieval — the unit the chat block and the HTTP route call.

Composes the embedder + the vector store into a single ``retrieve()``
call. All public callers should go through this module rather than
talking to ``Embedder`` / ``VectorStore`` directly; the composition is
where caching, dimension matching, and graceful-degradation policy live.
"""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.core.contract_data_chunks import particulars_chunk_states_a_value
from app.core.rag.embeddings import Embedder, get_embedder
from app.core.rag.vector_store import (
    Chunk,
    get_lexical_store,
    get_store,
    normalize_cesmm_item_codes,
)
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
    # Currencies in rate units (AED/m2, USD/ft2). Live 2026-08-20: a
    # self-coding conversion was identifier-miss short-circuited because
    # `aed/m2` contains a digit (the exponent) but is not a document code.
    "aed", "usd", "sar", "eur", "gbp", "qar", "bhd", "kwd", "omr", "egp",
    "cny", "inr", "jpy",
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

    # OCR / CESMM print ``D 549.2``; compact that before the token regexes
    # so a spaced code extracts as ``d549.2`` (WAVE 2 B5). The original
    # fences still apply to the collapsed string — a typo like
    # ``specif8cation`` is unchanged.
    query = normalize_cesmm_item_codes(query)

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
        # A pure number with a decimal point is a QUANTITY, not a reference
        # code. Live find 2026-08-15 (F21): a costing request carrying
        # measured quantities ("1947.87 square metres ... 2342.20 metres")
        # had both decimals extracted as identifiers; no chunk contains
        # them, so the exact-reference gate short-circuited every grounded
        # costing question with "could not confirm this reference".
        # Letterless dot/comma-separated digits are never document codes;
        # hyphenated digit pairs (drawing/sheet refs like 054-0009) keep
        # matching. A decimal minus a number (18.4-16) is leftover L7
        # arithmetic, not a sheet ref — that token used to fire the
        # RAG-miss short-circuit ("could not confirm this reference")
        # before sympy_reasoning ever ran.
        if re.fullmatch(r"\d+[.,]\d+", token):
            continue
        if re.fullmatch(r"\d+[.,]\d+[-+*/]\d+(?:[.,]\d+)?", token):
            continue
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


def identifier_present_in_text(ident: str, text: str) -> bool:
    """True when every token of ``ident`` appears in ``text``.

    CESMM OCR spacing is collapsed first so query ``D549.2`` matches
    stored ``D 549.2`` (WAVE 2 B5 live miss: the inject gate split the
    stored code into ``d``+``549`` and the query into ``d549``+``2``,
    then AND-failed and short-circuited chat). Label words (Ref / No /
    #) are ignored so ``VO 99`` still matches ``VO Ref: 99``.
    """
    blob = normalize_cesmm_item_codes(text or "")
    ident_norm = normalize_cesmm_item_codes(ident or "")
    text_tokens = set(re.split(r"[^a-z0-9]+", blob.lower())) - {"", "ref", "no", "#"}
    ident_tokens = [t for t in re.split(r"[^a-z0-9]+", ident_norm.lower()) if t]
    return bool(ident_tokens) and all(t in text_tokens for t in ident_tokens)


def _identifier_context_terms(query: str, identifiers: List[str]) -> List[str]:
    """Distinctive query terms excluding the identifier tokens themselves.

    WAVE 2 B4: a query for D599.5 + carriageway/340904 must prefer the
    priced carriageway row over an Excluded culvert that only shares the
    code. Identifier tokens (``d599``, ``5``) are dropped so the overlap
    score measures the rest of the question.
    """
    ident_toks: Set[str] = set()
    for ident in identifiers:
        ident_toks.update(
            t for t in re.split(
                r"[^a-z0-9]+", normalize_cesmm_item_codes(ident).lower()
            ) if t
        )
    terms: List[str] = []
    seen: Set[str] = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", query or ""):
        lowered = word.lower()
        if lowered in seen or lowered in ident_toks or lowered in _STOPWORDS:
            continue
        seen.add(lowered)
        terms.append(lowered)
    compact_q = (query or "").replace(",", "")
    for num in re.findall(r"\d{3,}(?:\.\d+)?", compact_q):
        if num in seen or num in ident_toks:
            continue
        seen.add(num)
        terms.append(num)
    return terms


def _identifier_context_overlap(terms: List[str], text: str) -> float:
    """Fraction of ``terms`` that appear in CESMM-normalised chunk text."""
    if not terms:
        return 0.0
    blob = normalize_cesmm_item_codes(text or "").lower().replace(",", "")
    matched = sum(1 for term in terms if term in blob)
    return matched / len(terms)


# Tender / executed-contract numbers: PREFIX-YEAR-SEQ (DD-2023-118, FX-2044-001).
# Drawing codes (IP-INF-054-...) and quantities do not match this shape.
# Underscore-glued filenames ("DD-2023-118_Vol 1.pdf") must still match, so
# this is not a \b word-boundary pattern (_ is a word character).
_CONTRACT_DOC_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{2,}-\d{4}-\d+)(?![A-Za-z0-9])"
)


def extract_contract_doc_ids(text: str) -> List[str]:
    """Return lowercase PREFIX-YEAR-SEQ contract/doc ids in ``text``.

    Used to scope a named-contract question to that contract's files so a
    DD-2023 question cannot surface DD-2022 chunks. Empty when the text
    names no such id.
    """
    if not text:
        return []
    found: List[str] = []
    seen: Set[str] = set()
    for m in _CONTRACT_DOC_ID_RE.finditer(text):
        tok = m.group(1).lower()
        if tok not in seen:
            seen.add(tok)
            found.append(tok)
    return found


def filename_matches_named_contracts(
    filename: str,
    named_ids: List[str],
    *,
    chunk_text: str = "",
) -> bool:
    """True when this document belongs to a contract the query named.

    The upload filename is the authority — live corpus contract numbers
    live there. An unresolved filename falls back to a *contiguous* id in
    chunk text. Token-soup matching ('dd' + '2023' + '118' scattered) is
    rejected: a DD-2022 Conditions of Contract chunk can contain those
    tokens as a prefix, a date, and a clause number.
    """
    if not named_ids:
        return True
    name_l = (filename or "").lower()
    if name_l:
        return any(cid in name_l for cid in named_ids)
    text_l = (chunk_text or "").lower()
    return any(cid in text_l for cid in named_ids)


def elect_answer_bearing_contract(
    query: str,
    ranked_docs: Iterable[Tuple[str, str]],
) -> Optional[str]:
    """Which contract owns an UNNAMED answer, decided by evidence not by rank.

    ``ranked_docs`` is ``(filename, chunk_text)`` in descending final-score
    order. It may be a lazy iterable: nothing is consumed for a question that
    wants no particular, and the walk stops at the first filled row.
    Returns the winning PREFIX-YEAR-SEQ, or None to leave the choice to
    arrival order (today's behaviour).

    WHY THIS EXISTS. The unnamed fence locks onto the first candidate that
    carries a contract id and drops every other id, so the top-ranked chunk
    does not merely outrank the rest — it DELETES the other contract from the
    result set. On the live Master Corpus that is decided by whichever chunk
    happens to sort first, and wave-1 measured both outcomes on one corpus in
    one session: A2 and A6 passed because a DD-2023-118 Contract Data row
    sorted first, while A5 and A9 failed because a DD-2022-175 Conditions of
    Contract clause did — and once it had, the DD-2023-118 row holding the
    answer could not appear at any rank.

    A General Conditions clause or a defined-term glossary entry is not an
    answer to "what is the rate" or "who is the Engineer"; it is a pointer to
    the row that holds it. So when the question asks for a kind of answer and
    the pool contains one, the contract that owns the best-ranked chunk OF
    THAT KIND wins the pool — a pointer from another contract cannot take it
    away.

    Each ask shape brings its own idea of what an answer looks like, because
    the wrong-year chunk that wins is different every time:

    * a filled Contract Data particulars row, for a particular or a numbered
      contract Schedule (wave-1 A3/A5/A9, wave-2 G1);
    * a chunk of a bill of quantities, for measured scope (wave-2 F1, whose
      three citations were all another year's Conditions of Contract prose
      describing the demolition scope in words).

    An ask that matches no shape returns None and keeps arrival order, so
    this can never reorder a corpus it has no opinion about.

    This complements the named-id fence (#443) rather than re-implementing
    it: a question that names its contract never reaches here.
    """
    # (does the ask want this kind of answer?, is this chunk that answer?)
    # Built here, not at module scope: both halves are defined further down.
    kinds = [
        (query_asks_for_contract_particulars,
         lambda _name, text: (
             is_contract_data_particulars_row(text)
             and particulars_row_answers_asked_label(query, text)
         )),
        (query_asks_for_boq_scope,
         lambda name, _text: document_is_a_bill_of_quantities(name)),
    ]
    active = [is_answer for asks, is_answer in kinds if asks(query)]
    if not active:
        return None
    for filename, text in ranked_docs:
        if not any(is_answer(filename, text) for is_answer in active):
            continue
        ids = extract_contract_doc_ids(filename or "")
        if ids:
            return ids[0]
    return None


class _ContractScope:
    """Drop wrong-contract chunks before top-K selection.

    Named query (DD-2023-118 in the question): keep only that id's files;
    if none remain the result is empty (fail closed — do not fill with
    another year's DD contract).

    Unnamed query: one contract wins the result set and chunks from a
    different PREFIX-YEAR-SEQ are dropped, so one answer cannot mix years.
    The winner is elected from answer-bearing evidence when the question
    asks for a filled Contract Data particular (see
    :func:`elect_answer_bearing_contract`); otherwise the first kept chunk
    carrying a contract id wins, as before.
    """

    def __init__(
        self,
        query: str,
        ranked_docs: Optional[Iterable[Tuple[str, str]]] = None,
    ) -> None:
        self.query = query or ""
        self.named = extract_contract_doc_ids(self.query)
        self.winning: Optional[str] = None
        self._particulars = (
            not self.named
            and query_asks_for_contract_particulars(self.query)
        )
        self._title_phrases: List[str] = []
        self._spec_identity_in_pool = False
        self._aca_contract_data_in_pool = False
        docs: Optional[List[Tuple[str, str]]] = (
            list(ranked_docs) if ranked_docs is not None else None
        )
        if not self.named and docs is not None:
            self.winning = elect_answer_bearing_contract(self.query, docs)
        if docs is not None:
            if spec_title_rescue_enabled() and query_asks_which_specification_document(
                self.query,
            ):
                self._title_phrases = extract_document_title_phrases(self.query)
                if self._title_phrases:
                    self._spec_identity_in_pool = any(
                        chunk_states_spec_document_identity(text, self._title_phrases)
                        or spec_title_filename_bonus(name, self._title_phrases) > 0
                        for name, text in docs
                    )
            if (
                contract_data_filename_rescue_enabled()
                and query_asks_for_accepted_contract_amount(self.query)
            ):
                self._aca_contract_data_in_pool = any(
                    contract_data_chunk_states_aca(name, text, self.query)
                    for name, text in docs
                )

    def allow(self, filename: str, chunk_text: str = "") -> bool:
        if self._spec_identity_in_pool:
            titled = spec_title_filename_bonus(filename, self._title_phrases) > 0
            identity = chunk_states_spec_document_identity(
                chunk_text, self._title_phrases,
            )
            if not (titled or identity):
                return False
        if self._aca_contract_data_in_pool:
            if not filename_looks_like_contract_data(filename):
                return False
        if self.named:
            return filename_matches_named_contracts(
                filename, self.named, chunk_text=chunk_text,
            )
        ids = extract_contract_doc_ids(filename or "")
        if not ids:
            return True
        if self.winning is None:
            # A particulars ask whose election declined must not freeze the
            # pool on a Volume 4 schedule duration or a GC pointer. Only a
            # matching-label filled row may lock arrival order (live A3).
            if self._particulars:
                if (
                    is_contract_data_particulars_row(chunk_text)
                    and particulars_row_answers_asked_label(
                        self.query, chunk_text,
                    )
                ):
                    self.winning = ids[0]
                    return True
                # Live A2: scanned Contract Data has PREFIX-YEAR-SEQ in
                # the filename and no index-time particulars prefix, so
                # the filled-row predicate never fires. The ACA filename
                # election already decided this file owns the ask.
                if (
                    self._aca_contract_data_in_pool
                    and filename_looks_like_contract_data(filename)
                    and contract_data_chunk_states_aca(
                        filename, chunk_text, self.query,
                    )
                ):
                    self.winning = ids[0]
                    return True
                return False
            self.winning = ids[0]
            return True
        return self.winning in ids


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


# ── lexical term rescue ─────────────────────────────────────────────────────
#
# Live failure (2026-08-17): the operator asked about the "Saudi Building Code".
# Two turns earlier the assistant had itself quoted "SBC 304 — Saudi Building
# Code" out of the structural general notes, so the phrase demonstrably sits in
# the corpus. The later turn retrieved five unrelated chunks (cable ladders, LV
# single-line diagrams, fire alarm, water pipelines, road alignment) and the
# model reported the corpus "does not mention the Saudi Building Code at all".
#
# Two things went wrong. The overclaim is handled in rag/inject.py (an answer
# may only speak for the retrieved excerpts). The RECALL miss is handled here.
#
# Why the existing machinery did not catch it: extract_query_identifiers only
# fires on code-SHAPED tokens (digits, hyphens, uppercase runs). "Saudi Building
# Code" is three ordinary words, so no identifier was extracted and no lexical
# pass ran at all — the turn was pure cosine over a 227-document corpus with
# k=5, and the user's typo ("buiding") degraded the lexical half of the hybrid
# search too.
#
# The rescue therefore matches on term CO-OCCURRENCE rather than exact phrase:
# terms are taken pairwise, so "saudi"+"code" still selects the right chunk when
# "building" is misspelt beyond recognition. A single common term ("code") is
# never enough to match, which is what keeps this from dragging in boilerplate —
# the failure mode a naive OR-any-term search would have.
#
# It runs ONLY when the semantic pass has already failed to surface any chunk
# carrying two or more of the query's distinctive terms, so on a healthy
# retrieval it is a no-op and costs one cheap SQL query at most.
_TERM_RESCUE_BONUS_MAX = 1.0   # below IDENTIFIER_BONUS_MAX: exact codes still win
_TERM_RESCUE_MAX_TERMS = 5     # caps the pair expansion at C(5,2) = 10 clauses
_TERM_RESCUE_MIN_TERMS = 2     # co-occurrence needs at least a pair


def term_rescue_enabled() -> bool:
    """Kill-switch. On by default — this fixes a live recall defect, so the
    safe state is enabled; set RAG_TERM_RESCUE=0 to fall back to pre-fix
    behaviour if it ever proves noisy on a specific corpus."""
    return (os.getenv("RAG_TERM_RESCUE", "1") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def extract_rescue_terms(query: str) -> List[str]:
    """The distinctive content terms of ``query``, most distinctive first.

    Proper-noun-shaped terms (capitalised) rank above ordinary words, then
    longer above shorter, because those carry the naming that makes a corpus
    lookup specific ("Saudi", "Building" before "code").
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", query or "")
    seen: Set[str] = set()
    ranked: List[Tuple[bool, int, str]] = []
    for word in words:
        lowered = word.lower()
        if lowered in seen or lowered in _GK_STOPWORDS or lowered in _STOPWORDS:
            continue
        seen.add(lowered)
        ranked.append((word[:1].isupper(), len(lowered), lowered))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [term for _cap, _len, term in ranked[:_TERM_RESCUE_MAX_TERMS]]


def build_rescue_phrases(terms: List[str]) -> List[str]:
    """Pairwise co-occurrence phrases for :meth:`VectorStore.identifier_search`.

    identifier_search AND-matches the tokens within one phrase and OR-matches
    across phrases, so a list of pairs asks exactly: "any chunk containing at
    least two of these terms". Scoring comes back as the fraction of pairs
    matched, which ranks a chunk carrying all the terms above one carrying two.
    """
    import itertools

    return [" ".join(pair) for pair in itertools.combinations(terms, 2)]


# ── letter / named-party filename rescue (live D1) ──────────────────────────
#
# Live Master Corpus D1 (SHA 567147a): "who signed the UBCC Concrete
# Batching Plant at Wadi Safar letter" retrieved only Volume 5 Other
# Documents (geotech / plot agreement / weekly reports). The letter was
# already in Neon (ids 8199b14b, b5033ec2) — its filename carries Letter +
# UBCC + Batching Plant + wadi Safar. Term rescue skipped the out-of-pool
# fetch because Volume 5 already mentioned the place-name in-chunk.
#
# Re-score on tip d7a4ca8 (2026-09-05, Neon project the-fork): retrieval
# now finds b5033ec2, but the indexed text is corpus-blocked. 8199b14b is
# MISSING from ``documents``. b5033ec2 is TEXT_SPARSE
# (``single_window:terminal``, one 1168-char chunk) and ends
# ``Yours sincerely, ,`` — no signatory name, role, or company. Do not
# invent a name that is not in the chunk. Re-extract / re-ingest of the
# richer id is an ingest job, not a ranking delta.
#
# Filename overlap is the discriminator Volume 5 cannot fake: its name is
# a contract volume, not a letter. Kill-switch: RAG_LETTER_FILENAME_RESCUE=0.
_LETTER_OR_SIGNATORY_RE = re.compile(
    r"(?i)\b(?:"
    r"who\s+signed|who\s+put\s+their\s+name|"
    r"signed\s+the\s+letter|signator(?:y|ies)|"
    r"in\s+what\s+capacity|"
    r"letter\s+(?:about|on|regarding|for|to)\b"
    r")"
)
_LETTER_IN_NAME_RE = re.compile(
    r"(?i)(?:^|[\s_\-/.(])letter(?:s)?(?:[\s_\-/.)]|$)",
)
# Below IDENTIFIER_BONUS_MAX (2.0) so exact codes still win; above typical
# cosine + Volume-5 term overlap so a filename-matched letter leads.
_FILENAME_OVERLAP_BONUS_MAX = 1.6
_FILENAME_LETTER_BONUS = 0.4
_FILENAME_RESCUE_MIN_TERMS = 2
_FILENAME_RESCUE_OPEN_MIN_TERMS = 3


def letter_filename_rescue_enabled() -> bool:
    """ON by default — this is a live recall defect. RAG_LETTER_FILENAME_RESCUE=0
    restores pre-fix ranking if the lift ever proves noisy on a corpus."""
    return (os.getenv("RAG_LETTER_FILENAME_RESCUE", "1") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def query_asks_for_letter_or_signatory(query: str) -> bool:
    """True for a who-signed / letter-about ask (D1), not a contract-role ask.

    "Who is the Engineer" stays on the Contract Data path (#483). "Who
    signed the letter about the batching plant" is this class.
    """
    return bool(_LETTER_OR_SIGNATORY_RE.search(query or ""))


def filename_looks_like_letter(filename: str) -> bool:
    """True when the upload name or path is a correspondence letter."""
    return bool(_LETTER_IN_NAME_RE.search(filename or ""))


def filename_query_overlap(filename: str, terms: List[str]) -> float:
    """Fraction of distinctive ``terms`` that appear in the filename/path."""
    blob = (filename or "").lower()
    cleaned = [t.lower() for t in terms if t and len(t) >= 3]
    if not blob or not cleaned:
        return 0.0
    hits = sum(1 for t in cleaned if t in blob)
    return hits / len(cleaned)


def filename_match_bonus(
    filename: str,
    terms: List[str],
    *,
    letter_query: bool,
) -> float:
    """Additive lift for a chunk whose document name matches the query.

    A letter-shaped name gets an extra tier so Volume 5 'Other Documents'
    that share a place-name cannot outrank the letter the question named.
    Zero when the name shares no distinctive term, and zero on a weak
    one-token collision (``contract.pdf`` vs "Conditions of Contract")
    so ordinary Q&A ranking stays byte-identical.
    """
    cleaned = [t for t in terms if t and len(t) >= 3]
    overlap = filename_query_overlap(filename, cleaned)
    if overlap <= 0.0 or not cleaned:
        return 0.0
    hits = overlap * len(cleaned)
    is_letter = filename_looks_like_letter(filename)
    if letter_query:
        if hits < _FILENAME_RESCUE_MIN_TERMS and not is_letter:
            return 0.0
    elif hits < _FILENAME_RESCUE_OPEN_MIN_TERMS and not (
        is_letter and hits >= _FILENAME_RESCUE_MIN_TERMS
    ):
        return 0.0
    bonus = overlap * _FILENAME_OVERLAP_BONUS_MAX
    if is_letter:
        bonus += _FILENAME_LETTER_BONUS
    return bonus


def _apply_filename_overlap_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
    name_by_id: Dict[str, str],
) -> None:
    """In-place: lift chunks whose resolved filename matches the query."""
    if not letter_filename_rescue_enabled():
        return
    terms = extract_rescue_terms(query)
    if len(terms) < _FILENAME_RESCUE_MIN_TERMS:
        return
    letter_q = query_asks_for_letter_or_signatory(query)
    for i, (score, chunk) in enumerate(scored):
        name = name_by_id.get(chunk.doc_id, "") or getattr(chunk, "source_name", "") or ""
        add = filename_match_bonus(name, terms, letter_query=letter_q)
        if add <= 0.0:
            continue
        boosted = score + add
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


def _rescue_filename_matched_docs(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    extra_pids: List[str],
) -> Dict[str, str]:
    """Pull chunks from filename-matched letters into ``fused``.

    Returns ``{doc_id: original_name}`` so the later name resolution does
    not re-query the documents table for docs we just looked up.
    Failures never raise — the semantic pool stands.
    """
    names: Dict[str, str] = {}
    if not letter_filename_rescue_enabled():
        return names
    terms = extract_rescue_terms(query)
    letter_q = query_asks_for_letter_or_signatory(query)
    if letter_q:
        if len(terms) < _FILENAME_RESCUE_MIN_TERMS:
            return names
        require_letter = True
        min_terms = _FILENAME_RESCUE_MIN_TERMS
    else:
        # Named-site lookup without "letter": only fire when the query is
        # specific enough that a filename collision is unlikely.
        if len(terms) < _FILENAME_RESCUE_OPEN_MIN_TERMS:
            return names
        require_letter = False
        min_terms = _FILENAME_RESCUE_OPEN_MIN_TERMS

    try:
        from app.core.projects import documents_matching_filename_terms
    except Exception:  # noqa: BLE001
        logger.warning("filename rescue: projects import failed", exc_info=True)
        return names

    pids = [project_id] + [p for p in extra_pids if p and p != project_id]
    fetch = getattr(store, "chunks_for_docs", None)
    if not callable(fetch):
        return names

    recovered = 0
    for pid in pids:
        try:
            matches = documents_matching_filename_terms(
                pid, terms, min_terms=min_terms, require_letter=require_letter,
            )
            if letter_q and not matches:
                # Filename has the site/party but not the word "letter"
                # (handover certificate). Retry on overlap alone.
                matches = documents_matching_filename_terms(
                    pid, terms, min_terms=_FILENAME_RESCUE_OPEN_MIN_TERMS,
                    require_letter=False,
                )
        except Exception as exc:  # noqa: BLE001 — extras must not break the turn
            logger.warning("filename rescue listing for %s failed: %s", pid, exc)
            continue
        if not matches:
            continue
        for doc in matches:
            names[doc["id"]] = doc.get("original_name") or ""
        try:
            hits = fetch(pid, [d["id"] for d in matches])
        except Exception as exc:  # noqa: BLE001
            logger.warning("filename rescue fetch for %s failed: %s", pid, exc)
            continue
        for chunk in hits:
            names.setdefault(chunk.doc_id, names.get(chunk.doc_id, ""))
            if chunk.chunk_id in fused:
                continue
            fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
            recovered += 1
    if recovered:
        logger.info(
            "filename rescue recovered %d chunk(s) for terms %r (letter_query=%s)",
            recovered, terms, letter_q,
        )
    return names


# ── specification-title filename rescue (live C2) ──────────────────────────
#
# Live Master Corpus C2 (SHA 567147a): "Which specification document covers
# the Variation Procedure, and what is its number?" retrieved DD-2022-175
# Demolition Specs Part 3. The governing spec is already in Neon —
# ``DGDAX-DGD-PMO-SPE-012650-1.0 Variation Procedure``. Term rescue treated
# the demolition volume's in-chunk "specification" / "procedure" overlap as
# already-grounded and skipped the out-of-pool fetch. Cosine prefers the
# long demolition volume over the short titled spec.
#
# The filename is the discriminator Demolition Specs cannot fake: it
# carries the Title-Case phrase the question used. Kill-switch:
# RAG_SPEC_TITLE_RESCUE=0.
#
# Re-score on tip d7a4ca8: there is still no standalone upload named
# ``DGDAX-DGD-PMO-SPE-012650-1.0 Variation Procedure``. The identifier
# lives as a register line inside Vol 2 Specification (8 of 9). Cosine
# prefers the later CSI heading ``Section 012650 — Variation and
# Adjustments`` in the same file. Filename rescue cannot see a title
# that is not in the upload name; the remaining delta is in-chunk
# spec-identity election (SPE-NNNNN + title).
_SPEC_IDENTITY_ASK_RE = re.compile(
    r"(?i)\b(?:which|what)\s+specification\s+(?:document|section)s?\b"
    r"|\bspecification\s+document\s+covers\b"
    r"|\band\s+what\s+is\s+its\s+number\b"
)
# Two-or-more consecutive Title-Case words ("Variation Procedure").
# Leading question words ("Which Specification") are stripped below.
_TITLE_CASE_PHRASE_RE = re.compile(
    r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)+)\b"
)
_TITLE_PHRASE_STOP = frozenset({
    "which", "what", "whose", "when", "where", "why",
    "this", "that", "the", "and", "for", "its", "our",
})
# Equal to IDENTIFIER_BONUS_MAX so a titled filename beats a high-cosine
# demolition volume the way an exact code beats boilerplate.
_SPEC_TITLE_FILENAME_BONUS = 2.0
# A CSI section number (``012650``) is not a document identity. The
# register line carries ``SPE-`` + five-or-more digits.
_SPE_DOC_CODE_RE = re.compile(r"(?i)\bSPE-\d{5,}\b")


def spec_title_rescue_enabled() -> bool:
    """ON by default — this is a live recall defect. RAG_SPEC_TITLE_RESCUE=0
    restores pre-fix ranking if the lift ever proves noisy on a corpus."""
    return (os.getenv("RAG_SPEC_TITLE_RESCUE", "1") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def query_asks_which_specification_document(query: str) -> bool:
    """True for a which-spec-covers-X / what-is-its-number ask (C2).

    Numbered-spec questions ("Specification 003113") stay on the
    identifier path. Contract-role and letter asks are not this class.
    """
    return bool(_SPEC_IDENTITY_ASK_RE.search(query or ""))


def extract_document_title_phrases(query: str) -> List[str]:
    """Title-Case phrases of two or more content words from ``query``.

    ``Variation Procedure`` is a document title. ``Which Specification``
    is question scaffolding and is dropped. Lowercased, deduplicated.
    """
    found: List[str] = []
    seen: Set[str] = set()
    for match in _TITLE_CASE_PHRASE_RE.finditer(query or ""):
        words = [
            w for w in match.group(1).split()
            if w.lower() not in _TITLE_PHRASE_STOP
        ]
        if len(words) < 2:
            continue
        phrase = " ".join(words).lower()
        if phrase in seen or len(phrase) < 8:
            continue
        seen.add(phrase)
        found.append(phrase)
    return found


def spec_title_filename_bonus(filename: str, phrases: List[str]) -> float:
    """Additive lift when the upload name carries a queried title phrase.

    Zero when the name shares no title phrase, so ordinary Q&A ranking
    stays byte-identical.
    """
    blob = (filename or "").lower()
    if not blob or not phrases:
        return 0.0
    if any(phrase and phrase in blob for phrase in phrases):
        return _SPEC_TITLE_FILENAME_BONUS
    return 0.0


def _normalize_retrieval_ws(text: str) -> str:
    """Collapse OCR / table newlines so a scanned label still matches."""
    return re.sub(r"\s+", " ", text or "").strip()


def chunk_states_spec_document_identity(text: str, phrases: List[str]) -> bool:
    """True when the chunk names a SPE-NNNNN document whose title is the ask.

    Live C2 on d7a4ca8: the register line ``DGDAX-DGD-PMO-SPE-012650-1.0
    Variation Procedure`` is the document number. ``Section 012650 —
    Variation and Adjustments`` in the same volume is a CSI heading, not
    the identifier the question asked for. ``SPE-`` + five digits is the
    discriminator; a bare ``012650`` is not.
    """
    if not phrases:
        return False
    blob = _normalize_retrieval_ws(text)
    if not _SPE_DOC_CODE_RE.search(blob):
        return False
    lower = blob.lower()
    return any(bool(p) and p in lower for p in phrases)


def _apply_spec_title_filename_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
    name_by_id: Dict[str, str],
) -> None:
    """In-place: lift chunks whose resolved filename matches a spec title."""
    if not spec_title_rescue_enabled():
        return
    if not query_asks_which_specification_document(query):
        return
    phrases = extract_document_title_phrases(query)
    if not phrases:
        return
    for i, (score, chunk) in enumerate(scored):
        name = name_by_id.get(chunk.doc_id, "") or getattr(chunk, "source_name", "") or ""
        add = spec_title_filename_bonus(name, phrases)
        if add <= 0.0:
            continue
        boosted = score + add
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


def _rescue_spec_title_docs(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    extra_pids: List[str],
) -> Dict[str, str]:
    """Pull chunks from title-matched specs into ``fused``.

    Returns ``{doc_id: original_name}`` so later name resolution does
    not re-query the documents table for docs we just looked up.
    Failures never raise — the semantic pool stands.
    """
    names: Dict[str, str] = {}
    if not spec_title_rescue_enabled():
        return names
    if not query_asks_which_specification_document(query):
        return names
    phrases = extract_document_title_phrases(query)
    if not phrases:
        return names

    try:
        from app.core.projects import documents_matching_title_phrase
    except Exception:  # noqa: BLE001
        logger.warning("spec-title rescue: projects import failed", exc_info=True)
        return names

    fetch = getattr(store, "chunks_for_docs", None)
    if not callable(fetch):
        return names

    pids = [project_id] + [p for p in extra_pids if p and p != project_id]
    recovered = 0
    for pid in pids:
        matches: List[Dict[str, str]] = []
        for phrase in phrases:
            try:
                matches.extend(documents_matching_title_phrase(pid, phrase))
            except Exception as exc:  # noqa: BLE001 — extras must not break the turn
                logger.warning(
                    "spec-title rescue listing for %s (%r) failed: %s",
                    pid, phrase, exc,
                )
        if not matches:
            continue
        seen_docs: Set[str] = set()
        unique_matches: List[Dict[str, str]] = []
        for doc in matches:
            did = doc.get("id") or ""
            if not did or did in seen_docs:
                continue
            seen_docs.add(did)
            unique_matches.append(doc)
            names[did] = doc.get("original_name") or ""
        try:
            hits = fetch(pid, [d["id"] for d in unique_matches])
        except Exception as exc:  # noqa: BLE001
            logger.warning("spec-title rescue fetch for %s failed: %s", pid, exc)
            continue
        for chunk in hits:
            names.setdefault(chunk.doc_id, names.get(chunk.doc_id, ""))
            if chunk.chunk_id in fused:
                continue
            fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
            recovered += 1
    if recovered:
        logger.info(
            "spec-title rescue recovered %d chunk(s) for phrases %r",
            recovered, phrases,
        )
    return names


def _apply_spec_identity_text_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
) -> None:
    """In-place: lift chunks whose body is a SPE-NNNNN + title register line."""
    if not spec_title_rescue_enabled():
        return
    if not query_asks_which_specification_document(query):
        return
    phrases = extract_document_title_phrases(query)
    if not phrases:
        return
    for i, (score, chunk) in enumerate(scored):
        if not chunk_states_spec_document_identity(chunk.text or "", phrases):
            continue
        boosted = score + _SPEC_TITLE_FILENAME_BONUS
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


def _rescue_spec_identity_chunks(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    extra_pids: List[str],
) -> int:
    """Pull SPE-NNNNN + title register lines into ``fused``.

    Filename title rescue cannot see a title that lives only in a volume's
    table of contents. Failures never raise.
    """
    if not spec_title_rescue_enabled():
        return 0
    if not query_asks_which_specification_document(query):
        return 0
    phrases = extract_document_title_phrases(query)
    if not phrases:
        return 0
    fetch = getattr(store, "chunks_containing_all", None)
    if not callable(fetch):
        return 0
    pids = [project_id] + [p for p in extra_pids if p and p != project_id]
    recovered = 0
    for pid in pids:
        for phrase in phrases:
            try:
                hits = fetch(pid, ["SPE-", phrase], k=20)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "spec-identity rescue for %s (%r) failed: %s",
                    pid, phrase, exc,
                )
                continue
            for chunk in hits:
                if not chunk_states_spec_document_identity(chunk.text or "", phrases):
                    continue
                if chunk.chunk_id in fused:
                    continue
                fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
                recovered += 1
    if recovered:
        logger.info(
            "spec-identity rescue recovered %d chunk(s) for phrases %r",
            recovered, phrases,
        )
    return recovered


# ── Contract Data filename rescue (live A2) ────────────────────────────────
#
# Live Master Corpus A2 retry on tip d7a4ca8: "What is the Accepted
# Contract Amount including VAT?" retrieved Long Form PSA / CPM permit
# trackers and reported the figure absent. The executed amount sits in
# ``…_Contract Data.pdf`` (scanned table, newlines between Accepted /
# Contract / Amount). That file has no ``CONTRACT DATA particulars``
# index-time prefix, so the particulars boost and unnamed election never
# fire. Filename "Contract Data" is the discriminator PSA/CPM cannot fake.
# Kill-switch: RAG_CONTRACT_DATA_FILENAME_RESCUE=0.
#
# Not #501 (A3/A5 newest-year lock). This only gets the Contract Data
# file into the pool for an Accepted Contract Amount ask.
_ACA_ASK_RE = re.compile(r"(?i)accepted\s+contract\s+amount")
_CONTRACT_DATA_FILENAME_BONUS = 2.0
_INCLUDING_VAT_RE = re.compile(r"(?i)including\s+vat|incl\.?\s+vat")


def contract_data_filename_rescue_enabled() -> bool:
    """ON by default — live A2 recall defect. RAG_CONTRACT_DATA_FILENAME_RESCUE=0
    restores pre-fix ranking if the lift ever proves noisy."""
    return (
        os.getenv("RAG_CONTRACT_DATA_FILENAME_RESCUE", "1") or ""
    ).strip().lower() not in (
        "0", "false", "no", "off",
    )


def query_asks_for_accepted_contract_amount(query: str) -> bool:
    """True for a filled Accepted Contract Amount ask, not a definition."""
    q = query or ""
    if _DEFINITION_QUESTION_RE.search(q):
        return False
    return bool(_ACA_ASK_RE.search(_normalize_retrieval_ws(q)))


def filename_looks_like_contract_data(filename: str) -> bool:
    """True when the upload name is a Contract Data file, not a PSA/CPM."""
    blob = (filename or "").replace("_", " ")
    return bool(re.search(r"(?i)contract\s+data", blob))


def contract_data_chunk_states_aca(filename: str, text: str, query: str) -> bool:
    """True when a Contract Data file's chunk states the asked ACA figure.

    Scanned Particular Conditions split the label across lines
    (``Accepted\\nContract\\nAmount (including VAT)``). Whitespace is
    collapsed before the label test. A money amount must still be visible.
    """
    if not filename_looks_like_contract_data(filename):
        return False
    blob = _normalize_retrieval_ws(text).lower()
    if "accepted contract amount" not in blob:
        return False
    if not _CD_MONETARY_VALUE_RE.search(text or ""):
        return False
    if _INCLUDING_VAT_RE.search(query or "") and not _INCLUDING_VAT_RE.search(blob):
        return False
    return True


def _apply_contract_data_filename_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
    name_by_id: Dict[str, str],
) -> None:
    """In-place: lift Contract Data files on an Accepted Contract Amount ask."""
    if not contract_data_filename_rescue_enabled():
        return
    if not query_asks_for_accepted_contract_amount(query):
        return
    for i, (score, chunk) in enumerate(scored):
        name = name_by_id.get(chunk.doc_id, "") or getattr(chunk, "source_name", "") or ""
        if not filename_looks_like_contract_data(name):
            continue
        boosted = score + _CONTRACT_DATA_FILENAME_BONUS
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


def _rescue_contract_data_docs(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    extra_pids: List[str],
) -> Dict[str, str]:
    """Pull chunks from filename-matched Contract Data files into ``fused``."""
    names: Dict[str, str] = {}
    if not contract_data_filename_rescue_enabled():
        return names
    if not query_asks_for_accepted_contract_amount(query):
        return names
    try:
        from app.core.projects import documents_matching_title_phrase
    except Exception:  # noqa: BLE001
        logger.warning("contract-data rescue: projects import failed", exc_info=True)
        return names
    fetch = getattr(store, "chunks_for_docs", None)
    if not callable(fetch):
        return names
    pids = [project_id] + [p for p in extra_pids if p and p != project_id]
    recovered = 0
    for pid in pids:
        try:
            matches = documents_matching_title_phrase(pid, "contract data")
        except Exception as exc:  # noqa: BLE001
            logger.warning("contract-data rescue listing for %s failed: %s", pid, exc)
            continue
        if not matches:
            continue
        for doc in matches:
            names[doc["id"]] = doc.get("original_name") or ""
        try:
            hits = fetch(pid, [d["id"] for d in matches], k_per_doc=20)
        except Exception as exc:  # noqa: BLE001
            logger.warning("contract-data rescue fetch for %s failed: %s", pid, exc)
            continue
        for chunk in hits:
            names.setdefault(chunk.doc_id, names.get(chunk.doc_id, ""))
            if chunk.chunk_id in fused:
                continue
            fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
            recovered += 1
    if recovered:
        logger.info("contract-data rescue recovered %d chunk(s) for an ACA ask", recovered)
    return names


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


# Contract Data particulars vs defined-term glossary (live S1 Q&A).
# Asking for a filled-in amount/duration/percentage must prefer the
# Contract Data / Appendix-to-Tender / Contract Particulars row over the
# GC glossary ("X means the amount accepted…"). Below IDENTIFIER_BONUS_MAX
# so exact reference codes still win. Kill-switch: RAG_CD_PARTICULARS_BOOST=0.
_DEFINITION_QUESTION_RE = re.compile(
    r"(?i)\b(?:what\s+does\b.+\bmean|defin(?:e|ition\s+of)|meaning\s+of)\b",
)
_PARTICULARS_FIELD_RE = re.compile(
    r"(?i)(?:excluding\s+vat|including\s+vat|accepted\s+contract\s+amount|"
    r"delay\s+damages|liquidated\s+damages|time\s+for\s+completion|"
    r"defects\s+notification|performance\s+(?:bond|security|guarantee)|"
    r"contract\s+data|appendix\s+to\s+(?:the\s+)?tender|"
    r"contract\s+particulars)",
)
_FILLED_IN_ASK_RE = re.compile(
    r"(?i)(?:how\s+many\s+days|what\s+is\s+the\s+(?:amount|rate|percentage|"
    r"duration|figure)|per\s+(?:calendar\s+)?day|calendar\s+days|"
    r"\bpercentage\b|\bamount\b)",
)
# Not every Contract Data particular is a number. A filled row can name a
# party (the Engineer), a method (the approved electronic communication), or
# an address — and asking WHO the Engineer is wants that filled row, not the
# General Conditions' "'Engineer' means the person appointed by the Employer"
# glossary entry. Live wave-1 A9 answered from the glossary of a DIFFERENT
# contract year because the ask was never recognised as particulars-shaped,
# so no part of the particulars machinery ran on it.
_CD_CONTRACT_ROLE_RE = re.compile(
    r"(?i)\b(?:engineer(?:'s\s+representative)?|"
    r"employer(?:'s\s+representative)?|contractor|"
    r"dispute\s+(?:adjudication\s+)?board|adjudicator)\b",
)
_CD_WHO_IS_RE = re.compile(
    r"(?i)\bwho\s+(?:is|are)\b|\bname\s+of\s+the\b|"
    r"\bwhich\s+(?:firm|company|entity|organisation|organization)\b",
)
# A numbered Schedule / Appendix / Annex of the contract is a Contract Data
# register row: "Schedule 10 | Not Used", "Schedule 9 | Health & Safety KPIs".
# Live wave-2 G1 (Sev-1) asked what Schedule 10 contains and was answered out
# of a DIFFERENT project's show package, which has a Schedule 10 of its own
# and talks about it at length. The register row that says "Not Used" IS the
# answer, and it is a filled particulars row — but the ask was not recognised
# as wanting one, so nothing lifted it and the arrival-order fence dropped
# the contract it belongs to.
#
# The context word is required: a numbered schedule also appears inside
# specifications and method statements, and those asks must stay where they
# are rather than being scoped to a contract's Contract Data.
_CD_SCHEDULE_ASK_RE = re.compile(
    r"(?i)\b(?:schedule|appendix|annex(?:ure)?)\s+(?:no\.?\s*)?\d+[A-Za-z]?\b",
)
_CD_SCHEDULE_CONTEXT_RE = re.compile(
    r"(?i)\b(?:contract|contracts|volume|volumes|"
    r"conditions\s+of\s+contract|tender)\b",
)
# Arithmetic over a particular that wants a MONEY answer. Live wave-2 E1:
# "Calculate the delay damages per calendar day in SAR for the whole of the
# Works" retrieved the 0.1%-per-day rate row at rank 1 and then reported the
# SAR figure as absent — because a percentage is not an amount, and the row
# carrying the amount shares no wording with the question, so it lost every
# top-5 slot to rows that do.
_CD_MONEY_ARITHMETIC_ASK_RE = re.compile(
    r"(?i)\b(?:calculate|compute|work\s+out|how\s+much)\b",
)
_CD_MONEY_UNIT_ASK_RE = re.compile(
    r"(?i)\b(?:sar|aed|usd|eur|gbp|qar|bhd|kwd|omr)\b|"
    r"\bmonetary\b|\bamount\s+per\b|\bvalue\s+per\b",
)
# A particulars row whose value IS an amount of money — the base a
# percentage-of-the-Contract-Price calculation needs.
_CD_MONETARY_VALUE_RE = re.compile(
    r"(?i)\b(?:sar|aed|usd|eur|gbp|qar|bhd|kwd|omr)\b[^\n]{0,12}"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?",
)
# Bill-of-quantities / measured-scope asks. Live wave-2 F1 asked for a WBS
# over "the demolition and site clearance scope in this project's BOQ" and
# every citation came from another year's Conditions of Contract, whose prose
# describes that scope in words while the BOQ carries it as measured rows.
_BOQ_SCOPE_ASK_RE = re.compile(
    r"(?i)\bbo[q]\b|\bbill\s+of\s+quantit|\bschedule\s+of\s+quantit|"
    r"\bmeasured\s+(?:work|works|items?|quantit)|\bpriced\s+bill\b",
)
_CD_PARTICULARS_PREFIX_RE = re.compile(
    r"contract\s+data\s+particulars", re.IGNORECASE,
)
_CD_HEADING_IN_CHUNK_RE = re.compile(
    r"(?:contract\s+data|appendix\s+to\s+(?:the\s+)?tender|"
    r"contract\s+particulars)",
    re.IGNORECASE,
)
_CD_MEANS_RE = re.compile(
    r"\bmeans\s+the\b|\bshall\s+mean\b|\bis\s+defined\s+as\b", re.IGNORECASE,
)
_CD_FILLED_VALUE_RE = re.compile(
    r"(?i)(?:\b(?:sar|aed|usd|eur|gbp|qar|bhd|kwd|omr)\b|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d[\d,]*\.\d{2}|"
    r"\d+(?:\.\d+)?\s*%|"
    r"\d+\s+(?:calendar\s+|working\s+)?days?)",
)
# A Conditions-of-Contract clause that says "at the rate stated in the
# Contract Data" is a POINTER to the answer, not the answer. It carries the
# heading phrase and — being a clause about delay damages — repeats every
# label word the question uses, so it used to collect the heading tier plus
# the full label bonus and land within ~0.45 of the row that actually holds
# the rate. Live wave-1 A5 is that chunk winning: the answer cited
# Sub-Clause 8.8 and then reported the rate and cap as absent from its
# excerpts, because the clause it had only refers to where they are stated.
#
# The discriminator is what precedes the phrase. A section heading sits at a
# line start; a cross-reference is governed by a preposition ("in the",
# "stated in the", "set out in the"). Only when EVERY mention in the chunk is
# prepositional is the chunk a pointer — one genuine heading is enough to
# keep the tier.
_CD_XREF_LEAD_RE = re.compile(
    r"(?i)\b(?:stated|set\s+out|specified|given|listed|described|defined|"
    r"identified|named|shown|provided|inserted|entered|contained|"
    r"referred\s+to|required)?\s*"
    r"\b(?:in|within|under|per|to|of|from|into)\s+(?:the\s+)?$",
)
_CD_XREF_LOOKBACK = 48
_CD_PARTICULARS_PREFIX_BONUS = 0.85
_CD_PARTICULARS_HEADING_BONUS = 0.40
_CD_DEFINITION_PENALTY = 0.40
# Scope disambiguation inside the particulars family (live A3 finding:
# "Time for Completion for the whole of the Works" answered with the
# milestone table — every particulars row got the same lift, and
# milestones win on bulk). When the query names one scope, rows of the
# other scope lose the family bonus.
_CD_WHOLE_WORKS_QUERY_RE = re.compile(
    r"(?i)\bwhole\s+of\s+the\s+works\b|\bwhole\s+works\b|\bworks\s+as\s+a\s+whole\b",
)
_CD_MILESTONE_QUERY_RE = re.compile(r"(?i)\bmilestones?\b")
_CD_MILESTONE_CHUNK_RE = re.compile(r"(?i)\bmilestones?\b")
_CD_SCOPE_MISMATCH_PENALTY = 1.10

# Label-awareness inside the particulars family (UI-PHYS A5/E1/A6/A2).
#
# The family bonus is flat: every "CONTRACT DATA particulars" chunk with a
# filled value gets the same +0.85. Within the family nothing distinguishes the
# row the question is about from the 200+ that are not, so ordering falls back
# to raw cosine — and these chunks are near-identical to the embedder. Measured
# on the live index: top-5 scores spanning 0.003, with the answer-bearing row at
# rank 21 (A5) and 29 (E1); A2 and A6 survived only because a small candidate
# pool happened not to supply enough competitors.
#
# The scope rule above was the first instance of this, solved for one axis
# (whole-of-Works vs milestone). This generalises it: reward the row whose LABEL
# the question actually names.
#
# Two things make it work where a small nudge would not:
#   * the bonus must dominate the family tie, not break it — competitors sit
#     within 0.003 of each other, and a larger candidate pool supplies more of
#     them, so the separation has to be decisive
#   * overlap is computed on the chunk BODY, never the header. Every particulars
#     chunk opens with an identical ~138-char header carrying the document
#     title; terms matching there are the same for every candidate and would
#     add noise in exactly the place discrimination is needed.
_CD_LABEL_TERM_BONUS = 0.35
_CD_LABEL_BONUS_CAP = 1.40


def _cd_chunk_body(text: str) -> str:
    """Chunk text minus the identical particulars header line."""
    t = text or ""
    nl = t.find("\n")
    return t[nl + 1:] if nl != -1 else t


def _cd_label_bonus(query_terms: frozenset, text: str) -> float:
    """Reward a particulars row for containing the label the query names."""
    if not query_terms:
        return 0.0
    body = _cd_chunk_body(text).lower()
    if not body:
        return 0.0
    overlap = sum(1 for t in query_terms if t in body)
    return min(overlap * _CD_LABEL_TERM_BONUS, _CD_LABEL_BONUS_CAP)


def contract_data_mention_is_only_a_cross_reference(text: str) -> bool:
    """True when every "Contract Data" mention points AT it rather than IS it.

    ``... at the rate stated in the Contract Data for every calendar day ...``
    is a clause telling the reader where to look. ``Contract Data`` on its own
    line, followed by rows, is the thing itself. False when the chunk has no
    mention at all — the caller has already established there is one.
    """
    t = text or ""
    mentions = list(_CD_HEADING_IN_CHUNK_RE.finditer(t))
    if not mentions:
        return False
    for m in mentions:
        lead = t[max(0, m.start() - _CD_XREF_LOOKBACK):m.start()]
        if not _CD_XREF_LEAD_RE.search(lead):
            return False
    return True


def is_contract_data_particulars_row(text: str) -> bool:
    """True for an index-time ``CONTRACT DATA particulars`` chunk that states
    a particular — the "answer-bearing Contract Data evidence" predicate the
    unnamed contract election runs on (:func:`elect_answer_bearing_contract`).

    Deliberately stricter than the scoring tier below, which asks only "is
    this a particulars chunk". The election decides which contract owns the
    whole result set, so it must be satisfied by a row that carries a VALUE
    and not by a window of unfilled keys — a clause number like ``1.1.67``
    reads as a decimal to the numeric test, so numbers alone are not proof
    that anything is filled in.
    """
    t = text or ""
    if not _CD_PARTICULARS_PREFIX_RE.search(t):
        return False
    return particulars_chunk_states_a_value(t)


def particulars_row_answers_asked_label(query: str, text: str) -> bool:
    """True when a particulars chunk's BODY names the particular the ask
    is about.

    The unnamed election used to lock the pool to the first filled
    particulars row of any kind. A filled Accepted Contract Amount window
    from another package then stole A3 (Time for Completion), the same
    way a glossary definition used to steal A9 before the role-identity
    ask was recognised. Label overlap is the same signal the scoring
    bonus already uses, computed on the body so the identical header
    every particulars chunk carries cannot elect.
    """
    return _cd_label_bonus(_significant_terms(query), text) > 0.0


def _cd_particulars_boost_enabled() -> bool:
    return (os.getenv("RAG_CD_PARTICULARS_BOOST") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def query_asks_for_contract_particulars(query: str) -> bool:
    """True when the question wants a filled-in Contract Data particular.

    Definition questions ("what does Accepted Contract Amount mean") stay
    on the glossary path. Arithmetic unit-rate questions are not this.

    A particular is not always a figure: "who is the Engineer" asks for the
    filled row that names the party, which is why a contract-role identity
    ask counts here too.
    """
    q = (query or "").strip()
    if not q or _DEFINITION_QUESTION_RE.search(q):
        return False
    if _PARTICULARS_FIELD_RE.search(q):
        return True
    if _CD_WHO_IS_RE.search(q) and _CD_CONTRACT_ROLE_RE.search(q):
        return True
    if _CD_SCHEDULE_ASK_RE.search(q) and _CD_SCHEDULE_CONTEXT_RE.search(q):
        return True
    return bool(_FILLED_IN_ASK_RE.search(q) and _CD_HEADING_IN_CHUNK_RE.search(q))


def query_asks_for_boq_scope(query: str) -> bool:
    """True when the question is about measured scope in a bill of quantities.

    The answer lives in BOQ item rows, not in the prose of a Conditions of
    Contract that happens to describe the same scope — and certainly not in
    another contract year's prose, which is what wave-2 F1 returned for all
    three of its citations.
    """
    return bool(_BOQ_SCOPE_ASK_RE.search((query or "").strip()))


def document_is_a_bill_of_quantities(filename: str) -> bool:
    """True when the DOCUMENT NAME says it is a bill of quantities.

    Consumes ``doc_index.BOQ_FILENAME_RE`` rather than restating it, so the
    retrieval-side test cannot drift from the one the indexer already uses to
    pick a chunker and an OCR budget. The import is deferred because
    ``doc_index`` imports this module; it is reached only for a BOQ-shaped
    ask, and ``sys.modules`` caches it after the first.
    """
    name = filename or ""
    if not name:
        return False
    try:
        from app.core.doc_index import BOQ_FILENAME_RE
    except Exception as exc:  # noqa: BLE001 — never break a turn over this
        logger.warning("BOQ filename test unavailable: %s", exc)
        return False
    return bool(BOQ_FILENAME_RE.search(name))


def query_needs_a_monetary_base(query: str) -> bool:
    """True when the ask is arithmetic over a particular and wants money out.

    A rate expressed as a percentage cannot answer "how much per day in SAR"
    on its own; the amount it is a percentage OF has to be in the excerpts
    too. Wave-2 E1 is the whole failure: the 0.1%-per-day row came back at
    rank 1 and the answer then reported the SAR figure as absent.
    """
    q = (query or "").strip()
    if not q:
        return False
    return bool(
        _CD_MONEY_ARITHMETIC_ASK_RE.search(q) and _CD_MONEY_UNIT_ASK_RE.search(q)
    )


def particulars_row_states_an_amount_of_money(text: str) -> bool:
    """True when a particulars row's value is an amount of money.

    This is the base row an arithmetic ask needs. Deliberately the row's
    VALUE and not its label: "10% of the Accepted Contract Amount" names the
    base without stating it, and that row is what wave-2 E1 already had.
    """
    t = text or ""
    if not _CD_PARTICULARS_PREFIX_RE.search(t):
        return False
    return bool(_CD_MONETARY_VALUE_RE.search(_cd_chunk_body(t)))


def reserve_monetary_base_row(
    query: str,
    kept: List[Chunk],
    ranked: List[Chunk],
    *,
    allow=None,
) -> bool:
    """Give the amount a percentage refers to one slot in the top-k.

    Returns True when a swap happened. ``kept`` is modified in place: the
    LOWEST-ranked survivor is replaced, so k is unchanged and the rows the
    question actually named keep their places.

    A reservation rather than a bigger bonus, deliberately. The money row
    earns no label bonus — the question says "in SAR", and the row says
    "SAR 8,640,000.00", and they share no term the overlap can see — so on
    the live Contract Data it competes against 200 siblings that each earn
    the full 1.40. Any constant large enough to clear that field is a
    constant fitted to one corpus's cosine spread; one slot is a guarantee.

    ``allow`` is the caller's contract-scope test, so a reserved row cannot
    re-enter a contract the fence already excluded.
    """
    if not kept or not query_needs_a_monetary_base(query):
        return False
    if any(particulars_row_states_an_amount_of_money(c.text or "") for c in kept):
        return False
    present = {c.chunk_id for c in kept}
    for chunk in ranked:
        if chunk.chunk_id in present:
            continue
        if not particulars_row_states_an_amount_of_money(chunk.text or ""):
            continue
        if allow is not None and not allow(chunk):
            continue
        kept[-1] = chunk
        logger.debug(
            "reserved a Contract Data money row for an arithmetic ask; "
            "a percentage alone cannot answer it",
        )
        return True
    return False


def reserve_matching_particulars_row(
    query: str,
    kept: List[Chunk],
    ranked: List[Chunk],
    *,
    allow=None,
) -> bool:
    """Give the particulars row the question named one slot in the top-k.

    Same-year General Conditions clauses can fill every slot after the
    fence has locked the right PREFIX-YEAR-SEQ (live A5: three HIGH
    Sub-Clause 8.8 chunks, no rate). A reservation rather than a bigger
    bonus: the clause repeats every label word and its cosine is not
    bounded.
    """
    if not kept or not query_asks_for_contract_particulars(query):
        return False
    if any(
        is_contract_data_particulars_row(c.text or "")
        and particulars_row_answers_asked_label(query, c.text or "")
        for c in kept
    ):
        return False
    present = {c.chunk_id for c in kept}
    for chunk in ranked:
        if chunk.chunk_id in present:
            continue
        text = chunk.text or ""
        if not (
            is_contract_data_particulars_row(text)
            and particulars_row_answers_asked_label(query, text)
        ):
            continue
        if allow is not None and not allow(chunk):
            continue
        kept[-1] = chunk
        logger.debug(
            "reserved a matching Contract Data particulars row; "
            "same-year General Conditions had filled the top-k",
        )
        return True
    return False


def contract_data_particulars_delta(text: str) -> float:
    """Score delta for a chunk when the query is particulars-shaped.

    Dedicated ``CONTRACT DATA particulars`` chunks (index-time prefix) get
    the strongest lift. Other chunks that still carry a Contract Data
    heading plus a filled value get a smaller lift — but a chunk whose only
    mention of the Contract Data is a cross-reference to it does not, because
    a clause that says where the rate is stated is not the row that states
    it. Glossary "means the" chunks with no filled figure are demoted so they
    cannot bury the row.
    """
    t = text or ""
    if _CD_PARTICULARS_PREFIX_RE.search(t) and (
        _CD_FILLED_VALUE_RE.search(t) or particulars_chunk_states_a_value(t)
    ):
        return _CD_PARTICULARS_PREFIX_BONUS
    if (
        _CD_HEADING_IN_CHUNK_RE.search(t)
        and _CD_FILLED_VALUE_RE.search(t)
        and not _CD_MEANS_RE.search(t)
        and not contract_data_mention_is_only_a_cross_reference(t)
    ):
        return _CD_PARTICULARS_HEADING_BONUS
    if _CD_MEANS_RE.search(t) and not _CD_FILLED_VALUE_RE.search(t):
        return -_CD_DEFINITION_PENALTY
    return 0.0


def _apply_contract_data_particulars_boost(query: str, scored: List[Tuple[float, Chunk]]) -> None:
    """In-place re-score when the query asks for a filled-in particular."""
    if not _cd_particulars_boost_enabled():
        return
    if not query_asks_for_contract_particulars(query):
        return
    wants_whole = bool(_CD_WHOLE_WORKS_QUERY_RE.search(query))
    wants_milestone = bool(_CD_MILESTONE_QUERY_RE.search(query))
    query_terms = _significant_terms(query)
    for i, (score, chunk) in enumerate(scored):
        text = chunk.text or ""
        delta = contract_data_particulars_delta(text)
        if not delta:
            continue
        if delta > 0:
            chunk_is_milestone = bool(_CD_MILESTONE_CHUNK_RE.search(text))
            if wants_whole and not wants_milestone and chunk_is_milestone:
                # Whole-works ask: milestone rows lose the family bonus and
                # take a penalty so the 1.1.75 whole-works row can surface.
                delta = -_CD_SCOPE_MISMATCH_PENALTY
            elif wants_milestone and not wants_whole and not chunk_is_milestone:
                # Milestone ask: non-milestone particulars keep their score
                # but get no family lift over the milestone rows.
                delta = 0.0
            if delta > 0:
                # Still inside the family: separate the row the question names
                # from the rest of it.
                delta += _cd_label_bonus(query_terms, text)
        boosted = score + delta
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


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


# Production chat retrieval is k=5 (runtime search_project_documents,
# rag inject). UI-PHYS A5/E1 particulars rows sat at ranks 21 and 29 on
# the live index — the old floor of 20 dropped them before #430's
# label-awareness could promote them. Floor 60 is the parked
# pool-stability change that must land *after* #430: raising it first
# flooded A2/A6 with family competitors and they failed. The SHA
# 7efeadb was never pushed; this reconstructs that change from the
# #430 evidence (k=5, candidate pool 60).
_OVERFETCH_MULTIPLIER = 4
_OVERFETCH_FLOOR = 60


def candidate_overfetch(k: int) -> int:
    """How many raw candidates to pull before ranking down to ``k``.

    Production callers pass k=5, which yields 60. Do not lower the floor:
    a particulars row sitting at rank ~21–29 never enters a pool of 20,
    so the label bonus has nothing to promote.
    """
    try:
        n = int(k)
    except (TypeError, ValueError):
        n = 5
    if n < 1:
        n = 1
    return max(n * _OVERFETCH_MULTIPLIER, _OVERFETCH_FLOOR)


def _lexical_only_retrieve(query: str, project_id: str, k: int) -> tuple:
    """BM25-only retrieval for when no embedder can be loaded.

    Used when the embedding model is absent, removed, or failed to load. The
    vector leg is unavailable, so ranking is purely lexical — worse than hybrid,
    and dramatically better than the empty list this used to return.

    Deliberately mirrors the main path's shape: active project first (so its
    chunks win ties over general knowledge), GK merged, noise-filtered, top-k,
    and the same ``(chunks, noise_filtered_count)`` tuple. Chunks are tagged
    ``layer="general_knowledge"`` for GK hits exactly as the main path does, so
    every downstream consumer — citation markers, the sources panel, disclosure
    — behaves identically.

    Failures here return ``([], 0)`` rather than raising: this IS the
    degradation path, and it must not become a new way to break a request.
    """
    if not query or not query.strip():
        return [], 0
    if not project_id:
        raise ValueError("project_id is required")

    try:
        # get_lexical_store, not get_store: the latter constructs an embedder
        # just to read the table width, which is the coupling this path exists
        # to break.
        store = get_lexical_store()
    except Exception as exc:  # noqa: BLE001 — degradation must not raise
        logger.warning("lexical-only retrieval unavailable: %s", exc)
        return [], 0

    over_fetch = candidate_overfetch(k)
    candidates: List[Chunk] = []
    try:
        candidates.extend(store.bm25_search(project_id, query, over_fetch))
    except Exception as exc:  # noqa: BLE001
        logger.warning("lexical retrieval failed for %s: %s", project_id, exc)
        return [], 0

    for gk_pid in _general_knowledge_project_ids():
        if gk_pid == project_id:
            continue
        try:
            for chunk in store.bm25_search(gk_pid, query, over_fetch):
                chunk.layer = "general_knowledge"
                candidates.append(chunk)
        except Exception as exc:  # noqa: BLE001 — GK never breaks the primary leg
            logger.warning("lexical GK retrieval for %s failed: %s", gk_pid, exc)

    if (
        _cd_particulars_boost_enabled()
        and query_asks_for_contract_particulars(query)
    ):
        particulars_q = (
            f"{query.strip()} Contract Data particulars filled-in amount "
            "duration percentage"
        )
        try:
            extra = store.bm25_search(project_id, particulars_q, over_fetch)
            seen = {c.chunk_id for c in candidates}
            for chunk in extra:
                if chunk.chunk_id not in seen:
                    candidates.append(chunk)
                    seen.add(chunk.chunk_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "lexical contract-data particulars retrieval failed: %s", exc,
            )
        for chunk in candidates:
            delta = contract_data_particulars_delta(chunk.text or "")
            if delta:
                chunk.score = round((chunk.score or 0.0) + delta, 6)

    fused_lex: Dict[str, Tuple] = {
        c.chunk_id: (c, c.score or 0.0, 0.0) for c in candidates
    }
    extra_lex_pids = _general_knowledge_project_ids()
    filename_names = _rescue_filename_matched_docs(
        query, project_id, fused_lex, store,
        extra_pids=extra_lex_pids,
    )
    filename_names.update(_rescue_spec_title_docs(
        query, project_id, fused_lex, store,
        extra_pids=extra_lex_pids,
    ))
    filename_names.update(_rescue_contract_data_docs(
        query, project_id, fused_lex, store,
        extra_pids=extra_lex_pids,
    ))
    _rescue_spec_identity_chunks(
        query, project_id, fused_lex, store,
        extra_pids=extra_lex_pids,
    )
    if len(fused_lex) > len(candidates):
        seen = {c.chunk_id for c in candidates}
        for chunk, _sem, _b in fused_lex.values():
            if chunk.chunk_id not in seen:
                candidates.append(chunk)
                seen.add(chunk.chunk_id)

    name_by_id: Dict[str, str] = dict(filename_names)
    scored_lex: List[Tuple[float, Chunk]] = [
        (c.score or 0.0, c) for c in candidates
    ]
    for _, chunk in scored_lex:
        if chunk.doc_id not in name_by_id:
            name_by_id[chunk.doc_id] = _doc_name_for_id(chunk.doc_id)
    _apply_filename_overlap_boost(query, scored_lex, name_by_id)
    _apply_spec_title_filename_boost(query, scored_lex, name_by_id)
    _apply_spec_identity_text_boost(query, scored_lex)
    _apply_contract_data_filename_boost(query, scored_lex, name_by_id)
    candidates = [chunk for _s, chunk in scored_lex]

    # Stable sort keeps the active project ahead of GK on equal scores.
    candidates.sort(key=lambda c: -(c.score or 0.0))

    kept: List[Chunk] = []
    noise_filtered = 0

    def _name(doc_id: str) -> str:
        return name_by_id.get(doc_id, "") or _doc_name_for_id(doc_id)

    # Elect the unnamed contract from answer-bearing evidence. Reads the
    # SAME ``name_by_id`` the loop below uses, which #490 populates from its
    # filename rescue — so a document that only entered the pool because its
    # NAME matched the query is electable on that name.
    scope = _ContractScope(
        query,
        ranked_docs=((_name(c.doc_id), c.text or "") for c in candidates),
    )
    for chunk in candidates:
        name = _name(chunk.doc_id)
        if _is_noise_filename(name):
            noise_filtered += 1
            continue
        if not scope.allow(name, chunk.text or ""):
            continue
        chunk.source_name = name
        kept.append(chunk)
        if len(kept) >= k:
            break
    _allow = (
        lambda c: not _is_noise_filename(_name(c.doc_id))
        and scope.allow(_name(c.doc_id), c.text or "")
    )
    reserve_matching_particulars_row(query, kept, candidates, allow=_allow)
    reserve_monetary_base_row(query, kept, candidates, allow=_allow)
    for chunk in kept:
        chunk.source_name = _name(chunk.doc_id)
    return kept, noise_filtered


def retrieve_with_filter(
    query: str,
    project_id: str,
    k: int = 5,
    *,
    intent: Optional[str] = None,
) -> tuple:
    """Returns ``(chunks, noise_filtered_count)``.

    Pulls ``candidate_overfetch(k)`` raw candidates (floor 60, so
    production k=5 yields a pool of 60) from the active project's
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
        # NOT an automatic []. BM25 is pure text matching over chunks.text and
        # needs no embedder at all — the vector leg does. Returning [] here
        # coupled the ENTIRE search surface to the embedder: remove the model
        # and search_project_documents went to zero hits on a corpus whose text
        # was sitting right there, fully indexed and matchable.
        #
        # This is what lets the embedder be removed, swapped, or fail without
        # taking search down with it: semantic ranking is lost, keyword
        # retrieval survives. Degrade a capability, not the product.
        logger.debug("embedding stack unavailable; retrieving lexical-only")
        return _lexical_only_retrieve(query, project_id, k)
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
    over_fetch = candidate_overfetch(k)
    # The GK corpus is small and curated (units / CESMM / FIDIC / procedures), so
    # over-fetch it generously: a lexically-relevant reference chunk must enter
    # the candidate pool even when its semantic score for a broad query is low
    # -- the lexical boost below can only re-rank chunks that made the fetch.
    gk_over_fetch = max(k * 12, 80)

    # Active project (operator's own corpus — first so it wins ties).
    raw_active = _dual_search(
        store, project_id, query_vec, query, alt_vec, alt_query, k=over_fetch,
    )

    # Particulars-shaped questions: a third search whose wording matches the
    # index-time "CONTRACT DATA particulars" prefix. Additive to F18 dual-query
    # (that transform stays the alt_query above). Failures never break primary.
    if (
        _cd_particulars_boost_enabled()
        and query_asks_for_contract_particulars(query)
    ):
        particulars_q = (
            f"{query.strip()} Contract Data particulars filled-in amount "
            "duration percentage"
        )
        try:
            pvec = embedder.encode_queries([particulars_q])[0]
            p_hits = store.search(
                project_id, pvec, k=over_fetch, query_text=particulars_q,
            )
            by_id = {c.chunk_id: c for c in raw_active}
            for c in p_hits:
                prev = by_id.get(c.chunk_id)
                if prev is None or (c.score or 0.0) > (prev.score or 0.0):
                    by_id[c.chunk_id] = c
            raw_active = sorted(
                by_id.values(), key=lambda c: -(c.score or 0.0),
            )
        except Exception as exc:  # noqa: BLE001 — extras must not break the turn
            logger.warning(
                "contract-data particulars retrieval for %s failed: %s; "
                "primary results stand",
                project_id, exc,
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
    # Tie-break among identifier hits. Capped equal to the identifier
    # bonus so a full-overlap code row can beat a high-cosine Excluded
    # mention of the same code, but a no-code semantic chunk (≤ ~1.0)
    # still cannot outrank a bare identifier hit (2.0).
    IDENTIFIER_CONTEXT_BOOST_MAX = 2.0
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
        # "VO 99" both match, punctuation between tokens is ignored. CESMM
        # codes are collapsed so ``d549`` is a substring of ``D 549.2``.
        ident_token_sets = []
        for ident in identifiers:
            toks = [
                t for t in re.split(
                    r"[^a-z0-9]+", normalize_cesmm_item_codes(ident).lower()
                ) if t
            ]
            if toks:
                ident_token_sets.append(toks)
        for chunk_id, entry in list(fused.items()):
            sem_chunk, sem_score, id_bonus = entry
            # Skip entries that already carry the search-assigned bonus
            # (identifier-only hits live there with sem_score 0.0). Real
            # semantic candidates keep eligibility even at negative cosine.
            if id_bonus > 0.0 or not ident_token_sets:
                continue
            text_lower = normalize_cesmm_item_codes(sem_chunk.text or "").lower()
            matched = sum(
                1 for toks in ident_token_sets
                if all(t in text_lower for t in toks)
            )
            if matched:
                local_score = matched / len(ident_token_sets)
                fused[chunk_id] = (
                    sem_chunk, sem_score, local_score * IDENTIFIER_BONUS_MAX,
                )

        # WAVE 2 B4: several BOQ rows can share a CESMM code (carriageway
        # qty vs an Excluded culvert that mentions D599.5). Identifier
        # bonus is flat, so the arbitrary SQL hit wins. Add a secondary
        # boost from the rest of the query (carriageway / 340904) — only
        # on chunks that already earned the identifier bonus, so
        # boilerplate without the code cannot climb the fence.
        ctx_terms = _identifier_context_terms(query, identifiers)
        if ctx_terms:
            for chunk_id, (chunk, sem_score, id_bonus) in list(fused.items()):
                if id_bonus <= 0.0:
                    continue
                overlap = _identifier_context_overlap(ctx_terms, chunk.text)
                if overlap <= 0.0:
                    continue
                fused[chunk_id] = (
                    chunk,
                    sem_score,
                    id_bonus + overlap * IDENTIFIER_CONTEXT_BOOST_MAX,
                )

    # ── lexical term rescue ────────────────────────────────────────────────
    # Two passes, because the live failure had two distinct shapes and only
    # doing one of them leaves the other broken:
    #
    #  (a) IN-POOL. The chunk carrying the query's terms WAS fetched, but sits
    #      deep in the over-fetch (rank ~15 of the pool) on cosine alone and never
    #      reaches the top-5 the user sees. Bonusing it in place is what lifts
    #      it. An earlier version of this fix gated the whole rescue on "is a
    #      term-carrying chunk anywhere in the candidate pool" — which this case
    #      satisfies, so the rescue was skipped and the chunk still never
    #      surfaced. The gate reproduced the very bug it was meant to fix.
    #
    #  (b) OUT-OF-POOL. The chunk was never fetched at all (the SBC 304 case:
    #      one document in 227, no semantic pull, k*4 candidates). Only a
    #      lexical lookup can recover it, so that runs when — and only when —
    #      pass (a) found nothing, keeping the extra SQL off the healthy path.
    if term_rescue_enabled():
        rescue_terms = extract_rescue_terms(query)
        if len(rescue_terms) >= _TERM_RESCUE_MIN_TERMS:
            pairs = build_rescue_phrases(rescue_terms)

            def _pair_fraction(text: str) -> float:
                """Fraction of term PAIRS co-occurring in ``text``. Graduated:
                a chunk with every term scores 1.0, one with two of five scores
                0.1 — so a passing word overlap earns a token bonus, not a
                promotion."""
                lowered = (text or "").lower()
                if not pairs:
                    return 0.0
                matched = sum(
                    1 for pair in pairs
                    if all(tok in lowered for tok in pair.split())
                )
                return matched / len(pairs)

            # Healthy-retrieval gate. If the top-K the user would ALREADY see
            # carries the query's terms, retrieval is working and the rescue
            # must be a strict no-op — scores included. Without this the bonus
            # perturbs every ordinary query, inflating top_score and pushing
            # marginal retrievals past RAG_CONFIDENCE_THRESHOLD, which trades a
            # recall bug for an ungrounded-answer bug.
            provisional_top = sorted(
                fused.values(), key=lambda e: -((e[1] or 0.0) + (e[2] or 0.0)),
            )[:k]
            already_grounded = any(
                _pair_fraction(chunk.text) > 0.0 for chunk, _s, _b in provisional_top
            )

            # (a) bonus every candidate already in the pool.
            found_in_pool = False
            for chunk_id, (chunk, sem_score, id_bonus) in list(fused.items()):
                fraction = _pair_fraction(chunk.text)
                if fraction <= 0.0:
                    continue
                found_in_pool = True
                if already_grounded:
                    continue
                bonus = fraction * _TERM_RESCUE_BONUS_MAX
                # Never displace a stronger identifier bonus — an exact code
                # match remains the strongest signal available.
                fused[chunk_id] = (chunk, sem_score, max(id_bonus, bonus))

            # (b) lexical fetch for chunks the semantic pass never saw.
            if not found_in_pool and not already_grounded:
                rescue_pids = [project_id] + gk_ids
                if use_fallback and fb_id:
                    rescue_pids.append(fb_id)
                recovered = 0
                for pid in rescue_pids:
                    try:
                        hits = store.identifier_search(pid, pairs, k=over_fetch)
                    except Exception as exc:  # noqa: BLE001 — never break the turn
                        logger.warning(
                            "lexical term rescue for %s failed: %s", pid, exc,
                        )
                        continue
                    for chunk in hits:
                        if chunk.chunk_id in fused:
                            continue
                        # Enters on the rescue bonus alone (zero cosine), so it
                        # ranks below any genuine semantic match — but above
                        # nothing, which is what the corpus-wide "does not
                        # mention it at all" answer amounted to.
                        fused[chunk.chunk_id] = (
                            chunk, 0.0,
                            _pair_fraction(chunk.text) * _TERM_RESCUE_BONUS_MAX,
                        )
                        recovered += 1
                if recovered:
                    logger.info(
                        "term rescue recovered %d chunk(s) for terms %r that "
                        "semantic retrieval missed entirely",
                        recovered, rescue_terms,
                    )

    # Letter / named-party filename rescue (D1). Runs EVEN WHEN term rescue
    # already found place-name overlap in Volume 5 — that in-pool hit is
    # what used to skip the out-of-pool fetch of the actual letter.
    extra_rescue_pids = gk_ids + ([fb_id] if use_fallback and fb_id else [])
    filename_names = _rescue_filename_matched_docs(
        query,
        project_id,
        fused,
        store,
        extra_pids=extra_rescue_pids,
    )
    # Spec-title filename rescue (C2). Runs EVEN WHEN term rescue already
    # found "specification" / "procedure" overlap in a demolition volume
    # — that in-pool hit is what used to skip the out-of-pool fetch of
    # the titled Variation Procedure spec.
    filename_names.update(_rescue_spec_title_docs(
        query,
        project_id,
        fused,
        store,
        extra_pids=extra_rescue_pids,
    ))
    filename_names.update(_rescue_contract_data_docs(
        query,
        project_id,
        fused,
        store,
        extra_pids=extra_rescue_pids,
    ))
    _rescue_spec_identity_chunks(
        query,
        project_id,
        fused,
        store,
        extra_pids=extra_rescue_pids,
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

    _apply_contract_data_particulars_boost(query, scored)

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
    name_by_id: Dict[str, str] = dict(filename_names)
    for _, chunk in scored:
        if chunk.doc_id not in name_by_id:
            name_by_id[chunk.doc_id] = _doc_name_for_id(chunk.doc_id)
    for i, (score, chunk) in enumerate(scored):
        nm = name_by_id.get(chunk.doc_id, "")
        # The filename itself is evidence, not just a ranking input — see
        # Chunk.source_name. Set here because this is where the name is already
        # resolved, so it costs nothing extra.
        chunk.source_name = nm
        chunk.revision = _revision.revision_token(nm)
        chunk.drawing_number = _revision.drawing_number(nm)
        if _revision.is_superseded(nm):
            chunk.superseded = True
            demoted = score - _revision.SUPERSEDED_PENALTY
            chunk.score = round(demoted, 6)
            scored[i] = (demoted, chunk)

    _apply_filename_overlap_boost(query, scored, name_by_id)
    _apply_spec_title_filename_boost(query, scored, name_by_id)
    _apply_spec_identity_text_boost(query, scored)
    _apply_contract_data_filename_boost(query, scored, name_by_id)

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
    # Cross-encoder rerank (RERANK_ENABLED / RAG_RERANKER, default OFF):
    # collect a DEEPER candidate pool (default top-50 hybrid) through the
    # very same gates below, then let the cross-encoder pick the best k.
    # Flag off -> target == k and the loop is byte-identical to before.
    # The gates run FIRST either way, so a noise-filtered, revision-
    # suppressed, or GK-capped chunk can never be resurrected by a good
    # rerank score. Do not enable in production until artifacts/fork/
    # RERANK_EVAL.md shows zero regressions + a held p95.
    rerank_on = _reranker.enabled()
    target = _reranker.candidate_depth(k) if rerank_on else k
    kept: List[Chunk] = []
    noise_dropped = 0
    revision_suppressed = 0
    gk_kept = 0
    # `scored` is already in final rank order, so this election sees exactly
    # the ranking the user would have got — and prevents a wrong-contract
    # pointer at rank 1 from deleting the row that holds the answer.
    scope = _ContractScope(
        query,
        ranked_docs=((name_by_id.get(c.doc_id, ""), c.text or "") for _, c in scored),
    )
    for _, c in scored:
        name = name_by_id.get(c.doc_id, "")
        if _is_noise_filename(name):
            noise_dropped += 1
            continue
        # A3: named-contract questions stay on that contract/doc id. Wrong
        # year (DD-2023 query / DD-2022 chunk) is dropped here, not ranked
        # through. Empty kept after this loop is fail-closed.
        if not scope.allow(name, c.text or ""):
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

    # Runs on the FINAL k, after the rerank cut, so the reserved row cannot be
    # reordered back out. Re-uses the same noise and contract-scope gates the
    # loop above applied.
    _allow_final = (
        lambda c: not _is_noise_filename(name_by_id.get(c.doc_id, ""))
        and scope.allow(name_by_id.get(c.doc_id, ""), c.text or "")
    )
    reserve_matching_particulars_row(
        query, kept, [c for _, c in scored], allow=_allow_final,
    )
    reserve_monetary_base_row(
        query, kept, [c for _, c in scored], allow=_allow_final,
    )

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
    chunks = [normalize_cesmm_item_codes(c) for c in chunks]
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
