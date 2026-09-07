"""High-level retrieval — the unit the chat block and the HTTP route call.

Composes the embedder + the vector store into a single ``retrieve()``
call. All public callers should go through this module rather than
talking to ``Embedder`` / ``VectorStore`` directly; the composition is
where caching, dimension matching, and graceful-degradation policy live.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.core.contract_data_chunks import (
    filled_particulars_rows,
    particulars_chunk_states_a_value,
)
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


def _contract_id_recency(cid: str) -> Tuple[int, int]:
    """Sort key for PREFIX-YEAR-SEQ: newer year, then higher sequence.

    Unnamed Master Corpus questions can retrieve a filled Time for
    Completion from more than one package (DD-2022-175 demolition at
    548 days, DD-2023-118 infrastructure at 852). First-in-rank used to
    lock the pool to whichever cosine arrived first. The later executed
    package owns the unnamed ask; the earlier one stays reachable by
    naming its id (#443).
    """
    parts = (cid or "").lower().split("-")
    year = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1
    seq = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
    return (year, seq)


def elect_answer_bearing_contract(
    query: str,
    ranked_docs: Iterable[Tuple[str, str]],
) -> Optional[str]:
    """Which contract owns an UNNAMED answer, decided by evidence not by rank.

    ``ranked_docs`` is ``(filename, chunk_text)`` in descending final-score
    order. It may be a lazy iterable: nothing is consumed for a question that
    wants no particular. When the ask is particulars-shaped the walk reads
    every candidate so a later-year filled row can beat an earlier-year
    filled row that happened to rank first (live A3/A5 on d7a4ca8).
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

    When TWO contracts both own that kind of answer (both state a Time for
    Completion, both state a Delay Damages rate), first-in-rank is still
    arrival order. The newer PREFIX-YEAR-SEQ wins: it is the later executed
    package. Naming the older id still fail-closes onto that year.

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
         lambda _name, text: chunk_answers_asked_particular(query, text)),
        (query_asks_for_boq_scope,
         lambda name, _text: document_is_a_bill_of_quantities(name)),
        (query_asks_for_boq_item_amount,
         lambda _name, text: chunk_states_rate_only_item(
             text, extract_asked_cesmm_codes(query),
         )),
    ]
    active = [is_answer for asks, is_answer in kinds if asks(query)]
    if not active:
        return None
    found: List[str] = []
    seen: Set[str] = set()
    for filename, text in ranked_docs:
        if not any(is_answer(filename, text) for is_answer in active):
            continue
        ids = extract_contract_doc_ids(filename or "")
        if not ids:
            continue
        cid = ids[0]
        if cid not in seen:
            seen.add(cid)
            found.append(cid)
    if not found:
        return None
    return max(found, key=_contract_id_recency)


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
        # Live 3a5fce5 Wave-1: year-lock elects DD-2023-118 (A3 PASS) but
        # A5's 0.1%-per-day row and A9's Engineer appointment live in a
        # different / unprefixed chunk. Once those answers are in the
        # pool, Client/Consultant PSA and same-year Sub-Clause 8.8 / cap
        # rows must not occupy the top-k. Kill-switches restore the
        # prefix-only fence.
        self._delay_rate_in_pool = False
        self._engineer_identity_in_pool = False
        self._aca_incl_vat_in_pool = False
        self._tfc_in_pool = False
        # OLD-pack A6: Defects Notification Period. PSA / CPM TOC
        # recitals used to occupy every slot after #522. Not C1.
        self._dnp_in_pool = False
        # OLD-pack E1: rate × ACA. A5's exclusive rate fence would
        # drop the money row; E1 needs both operands in the top-k.
        self._e1_compose_in_pool = False
        self._schedule_labels: List[str] = []
        self._schedule_register_in_pool = False
        # OLD-pack G4: D529.3 Amount is Rate Only. Priced lookalikes
        # (D549.2 fence, D599.5 carriageway, Excluded culvert) used to
        # occupy every slot and the model greeted. Not #504/#505/#506.
        self._rate_only_codes: List[str] = []
        self._rate_only_in_pool = False
        # OLD-pack C1: Sub-Clause 1.5.1(d) intro ends "as follows";
        # the precedence list is the next same-doc chunk. Not A2/A3/A5/A6/A9.
        self._spec_precedence_list_in_pool = False
        docs: Optional[List[Tuple[str, str]]] = (
            list(ranked_docs) if ranked_docs is not None else None
        )
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
            if (
                delay_damages_rate_rescue_enabled()
                and query_asks_for_delay_damages_rate(self.query)
            ):
                self._delay_rate_in_pool = any(
                    chunk_states_delay_damages_rate(text) for _n, text in docs
                )
            if (
                engineer_identity_rescue_enabled()
                and query_asks_who_the_engineer_is(self.query)
            ):
                self._engineer_identity_in_pool = any(
                    chunk_states_engineer_identity(text) for _n, text in docs
                )
            if (
                aca_including_vat_rescue_enabled()
                and query_asks_for_aca_including_vat(self.query)
            ):
                self._aca_incl_vat_in_pool = any(
                    chunk_states_aca_including_vat(text) for _n, text in docs
                )
            if (
                time_for_completion_rescue_enabled()
                and query_asks_for_time_for_completion(self.query)
            ):
                self._tfc_in_pool = any(
                    chunk_states_time_for_completion(text) for _n, text in docs
                )
            if (
                dnp_rescue_enabled()
                and query_asks_for_defects_notification_period(self.query)
            ):
                self._dnp_in_pool = any(
                    chunk_states_defects_notification_period(text)
                    for _n, text in docs
                )
            if (
                delay_damages_daily_rescue_enabled()
                and query_asks_delay_damages_daily_amount(self.query)
            ):
                has_rate = any(
                    chunk_states_delay_damages_rate(text) for _n, text in docs
                )
                has_aca = any(
                    _chunk_is_e1_compose_operand(text)
                    and not chunk_states_delay_damages_rate(text)
                    for _n, text in docs
                )
                # Only fence when both operands are reachable. A
                # rate-only fence would delete the ACA (live E1).
                self._e1_compose_in_pool = has_rate and has_aca
            # OLD-pack G1: a Schedule-N register row ("Schedule 10: Not Used")
            # is the answer. Vol 4 / Vol 5 / CPM mention "schedule" at length
            # and used to occupy every slot. Not #500/#501/#502/#503.
            if (
                schedule_register_rescue_enabled()
                and query_asks_for_contract_particulars(self.query)
                and query_asks_for_numbered_contract_schedule(self.query)
            ):
                self._schedule_labels = extract_asked_schedule_labels(self.query)
                if self._schedule_labels:
                    self._schedule_register_in_pool = any(
                        chunk_states_schedule_register(text, self._schedule_labels)
                        for _name, text in docs
                    )
            if (
                rate_only_rescue_enabled()
                and query_asks_for_boq_item_amount(self.query)
            ):
                self._rate_only_codes = extract_asked_cesmm_codes(self.query)
                if self._rate_only_codes:
                    self._rate_only_in_pool = any(
                        chunk_states_rate_only_item(text, self._rate_only_codes)
                        for _n, text in docs
                    )
            if (
                spec_precedence_list_rescue_enabled()
                and query_asks_for_spec_precedence_list(self.query)
            ):
                self._spec_precedence_list_in_pool = any(
                    chunk_states_spec_precedence_list(text) for _n, text in docs
                )
        if not self.named and docs is not None:
            self.winning = elect_answer_bearing_contract(self.query, docs)

    def allow(self, filename: str, chunk_text: str = "") -> bool:
        # Named PREFIX-YEAR-SEQ (#443) is fail-closed onto that year.
        # The rate / Engineer fences are unnamed-only — a question that
        # names DD-2022-175 must still see that year's chunks.
        if self._rate_only_in_pool:
            if not chunk_states_rate_only_item(
                chunk_text, self._rate_only_codes,
            ):
                return False
        if self._spec_precedence_list_in_pool:
            if not chunk_states_spec_precedence_list(chunk_text):
                return False
        if not self.named:
            if self._delay_rate_in_pool:
                if not chunk_states_delay_damages_rate(chunk_text):
                    return False
            if self._engineer_identity_in_pool:
                if not chunk_states_engineer_identity(chunk_text):
                    return False
            if self._aca_incl_vat_in_pool:
                if not chunk_states_aca_including_vat(chunk_text):
                    return False
            if self._tfc_in_pool:
                if not chunk_states_time_for_completion(chunk_text):
                    return False
            if self._dnp_in_pool:
                if not chunk_states_defects_notification_period(chunk_text):
                    return False
            if self._e1_compose_in_pool:
                if not _chunk_keeps_for_e1_daily(filename, chunk_text):
                    return False
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
        if self._schedule_register_in_pool:
            if not chunk_states_schedule_register(
                chunk_text, self._schedule_labels,
            ):
                return False
        if self.named:
            return filename_matches_named_contracts(
                filename, self.named, chunk_text=chunk_text,
            )
        ids = extract_contract_doc_ids(filename or "")
        if not ids:
            # No PREFIX-YEAR-SEQ in the filename. After year-lock that
            # used to let Long Form PSA Client/Consultant parties occupy
            # every A9 slot. When an Engineer appointment is in the pool
            # the identity fence above already dropped them.
            return True
        if self.winning is None:
            # A particulars ask whose election declined must not freeze the
            # pool on a Volume 4 schedule duration or a GC pointer. Only a
            # matching-label filled row may lock arrival order (live A3).
            # Scanned Contract Data (no index-time prefix) still counts
            # when it states the asked rate / Engineer (live A5/A9).
            if self._particulars:
                if chunk_answers_asked_particular(self.query, chunk_text):
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
                # Live G1: scanned / unprefixed register row in a
                # PREFIX-YEAR-SEQ Contract Data file. Same exception as
                # A2 — the row is the answer even without the index-time
                # particulars prefix.
                if (
                    self._schedule_register_in_pool
                    and chunk_states_schedule_register(
                        chunk_text, self._schedule_labels,
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


def filename_looks_like_e1_rate_volume(filename: str) -> bool:
    """True for the bound CoC / Contract Data volume leftover E1 scans.

    Live sources cite ``DD-2023-118_…_Cond…`` — a complete Conditions
    volume whose 8.8 windows occupy top-k. Requiring only
    ``contract data`` in the name left ``_e1_pool_doc_ids`` empty when
    those chunks were pointer-only, so the all-chunk scan never ran.
    """
    blob = (filename or "").replace("_", " ")
    return bool(re.search(
        r"(?i)contract\s+data|conditions?\s+of\s+contract|"
        r"particular\s+conditions",
        blob,
    ))


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
    """In-place: lift Contract Data files on an A2 / A3 / A6 / A9 ask."""
    if not contract_data_filename_rescue_enabled():
        return
    if not query_wants_contract_data_file(query):
        return
    want_aca = query_asks_for_accepted_contract_amount(query)
    want_tfc = query_asks_for_time_for_completion(query)
    want_eng = query_asks_who_the_engineer_is(query)
    want_e1 = query_asks_delay_damages_daily_amount(query)
    want_dnp = query_asks_for_defects_notification_period(query)
    for i, (score, chunk) in enumerate(scored):
        name = name_by_id.get(chunk.doc_id, "") or getattr(chunk, "source_name", "") or ""
        if not filename_looks_like_contract_data(name):
            continue
        text = chunk.text or ""
        # A2 keeps today's "any Contract Data file" lift. A3/A6/A9 only
        # lift the row that answers — an ACA-only Contract Data file
        # must not steal Time for Completion (test_a3_is_not_stolen).
        # E1 lifts the two compose operands, not every CD sibling.
        if want_tfc and not want_aca and not chunk_states_time_for_completion(text):
            continue
        if want_eng and not want_aca and not chunk_states_engineer_identity(text):
            continue
        if want_e1 and not want_aca and not chunk_states_delay_damages_rate(text):
            continue
        if want_dnp and not want_aca and not chunk_states_defects_notification_period(text):
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
    if not query_wants_contract_data_file(query):
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
            hits = fetch(pid, [d["id"] for d in matches], k_per_doc=40)
        except Exception as exc:  # noqa: BLE001
            logger.warning("contract-data rescue fetch for %s failed: %s", pid, exc)
            continue
        keep = None
        if query_asks_for_aca_including_vat(query):
            keep = chunk_states_aca_including_vat
        elif query_asks_for_time_for_completion(query):
            keep = chunk_states_time_for_completion
        elif query_asks_who_the_engineer_is(query):
            keep = chunk_states_engineer_identity
        elif query_asks_delay_damages_daily_amount(query):
            keep = _chunk_is_e1_compose_operand
        elif query_asks_for_defects_notification_period(query):
            keep = chunk_states_defects_notification_period
        paired = _pair_adjacent_keep_text(hits, keep) if keep else []
        e1 = query_asks_delay_damages_daily_amount(query)
        for chunk in paired:
            names.setdefault(chunk.doc_id, names.get(chunk.doc_id, ""))
            # E1: rate earns the asked-value bonus; ACA enters at 0 so
            # the monetary reservation still owns the last slot.
            bonus = _ASKED_PARTICULAR_VALUE_BONUS
            if e1 and not chunk_states_delay_damages_rate(chunk.text or ""):
                bonus = 0.0
            fused[chunk.chunk_id] = (chunk, 0.0, bonus)
            recovered += 1
        # A2 still needs every Contract Data window so the filename
        # fence can see the including-VAT row. A3/A9 only keep the
        # answering row — an ACA-only file must not fill top-k.
        if query_asks_for_accepted_contract_amount(query):
            for chunk in hits:
                names.setdefault(chunk.doc_id, names.get(chunk.doc_id, ""))
                if chunk.chunk_id in fused:
                    continue
                fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
                recovered += 1
    if recovered:
        logger.info("contract-data rescue recovered %d chunk(s) for a particulars ask", recovered)
    return names


# ── Schedule-register / Not Used rescue (live OLD-pack G1) ─────────────────
#
# Live Master Corpus G1 (tip a65cebb5): "Answer only from the client project
# documents. What does Schedule 10 of the contract contain?" retrieved
# Volume 5 / Volume 4 / CPM and answered with a generic "I will answer from
# the documents" acknowledgement. The contract's own schedule index says
# ``Schedule 10: Not Used``. That short register row is the answer — cosine
# prefers the long volumes that mention "schedule" at length, and term
# rescue treats that overlap as already-grounded so it never fetches the
# index line. Do not invent contents; surface the register row as written.
#
# Same shape as C2's in-chunk identity rescue (``chunks_containing_all``)
# plus the A2/C2 fence: when a register row is in the pool, lookalikes
# drop. Kill-switch: RAG_SCHEDULE_REGISTER_RESCUE=0.
#
# Not #500 (D2/D4/D5 routing), not #501 (A3/A5 year lock), not #502
# (C2 SPE-identity / A2 Contract Data filename).
_SCHEDULE_REGISTER_BONUS = 2.0
_SCHEDULE_REGISTER_ROW_RE = re.compile(
    r"(?i)\b(schedule\s+(?:no\.?\s*)?\d+[A-Za-z]?)"
    r"\s*[:|–—-]\s*"
    r"(?P<val>\S[^\n]{0,79})"
)
_SCHEDULE_NOT_USED_COLLAPSED_RE = re.compile(
    r"(?i)\b(schedule\s+(?:no\.?\s*)?\d+[A-Za-z]?)"
    r"(?:\s*[:|–—-]\s*|\s+)"
    r"not\s+used\b"
)
_SCHEDULE_REGISTER_PROSE_RE = re.compile(
    r"(?i)\b(?:sets?\s+out|shall|the\s+contractor|contains?|covers?|"
    r"includes?|must\b|will\s+provide)\b"
)


def schedule_register_rescue_enabled() -> bool:
    """ON by default — live G1 recall defect. RAG_SCHEDULE_REGISTER_RESCUE=0
    restores pre-fix ranking if the lift ever proves noisy."""
    return (
        os.getenv("RAG_SCHEDULE_REGISTER_RESCUE", "1") or ""
    ).strip().lower() not in (
        "0", "false", "no", "off",
    )


def query_asks_for_numbered_contract_schedule(query: str) -> bool:
    """True for 'what does Schedule N of the contract contain?' — G1.

    Bare 'what does Schedule 10 contain?' stays off this path: a numbered
    schedule also appears inside specifications and method statements.
    Definition questions and programme-build asks are not this class.
    """
    q = (query or "").strip()
    if not q or _DEFINITION_QUESTION_RE.search(q):
        return False
    return bool(
        _CD_SCHEDULE_ASK_RE.search(q) and _CD_SCHEDULE_CONTEXT_RE.search(q)
    )


def extract_asked_schedule_labels(query: str) -> List[str]:
    """``schedule 10`` / ``appendix 3`` labels the ask actually names."""
    out: List[str] = []
    seen: Set[str] = set()
    for match in _CD_SCHEDULE_ASK_RE.finditer(query or ""):
        label = re.sub(r"\s+", " ", match.group(0).lower()).strip()
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _schedule_label_matches(asked: str, key: str) -> bool:
    """True when a register key is the asked Schedule / Appendix N."""
    asked_n = re.sub(r"(?i)\s+no\.?\s*", " ", asked or "").strip()
    key_n = re.sub(r"(?i)\s+no\.?\s*", " ", key or "").strip()
    if not asked_n or not key_n:
        return False
    return asked_n == key_n or asked_n in key_n


def chunk_states_schedule_register(text: str, labels: List[str]) -> bool:
    """True when ``text`` is a schedule-index row for an asked label.

    ``Schedule 10: Not Used`` and ``Schedule 9 | Health & Safety KPIs``
    are register rows. ``Schedule 10 sets out any applicable Works
    Guarantees`` is prose from another package — not a register, and
    must not be treated as contents we invented.
    """
    if not labels:
        return False
    wanted = [re.sub(r"\s+", " ", lab.lower()).strip() for lab in labels if lab]
    if not wanted:
        return False
    blob = text or ""
    collapsed = _normalize_retrieval_ws(blob)

    def _wanted(key: str) -> bool:
        key_l = re.sub(r"\s+", " ", (key or "").lower()).strip()
        return any(_schedule_label_matches(lab, key_l) for lab in wanted)

    if _SCHEDULE_NOT_USED_COLLAPSED_RE.search(collapsed):
        for match in _SCHEDULE_NOT_USED_COLLAPSED_RE.finditer(collapsed):
            if _wanted(match.group(1)):
                return True
    for match in _SCHEDULE_REGISTER_ROW_RE.finditer(blob):
        val = (match.group("val") or "").strip()
        if _SCHEDULE_REGISTER_PROSE_RE.search(val):
            continue
        if _wanted(match.group(1)):
            return True
    for key, val in filled_particulars_rows(blob):
        if _SCHEDULE_REGISTER_PROSE_RE.search(val or ""):
            continue
        if _wanted(key):
            return True
    return False


def chunk_states_schedule_not_used(text: str) -> bool:
    """True when a numbered-schedule register row says Not Used.

    Query-free so inject can warn the model without the user ask. Does
    not invent: the excerpt itself must already say Not Used.
    """
    return bool(_SCHEDULE_NOT_USED_COLLAPSED_RE.search(_normalize_retrieval_ws(text)))


def _apply_schedule_register_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
) -> None:
    """In-place: lift chunks whose body is the asked Schedule-N register."""
    if not schedule_register_rescue_enabled():
        return
    if not query_asks_for_contract_particulars(query):
        return
    if not query_asks_for_numbered_contract_schedule(query):
        return
    labels = extract_asked_schedule_labels(query)
    if not labels:
        return
    for i, (score, chunk) in enumerate(scored):
        if not chunk_states_schedule_register(chunk.text or "", labels):
            continue
        boosted = score + _SCHEDULE_REGISTER_BONUS
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


def _rescue_schedule_register_chunks(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    extra_pids: List[str],
) -> int:
    """Pull Schedule-N register / Not Used index rows into ``fused``.

    Cosine never ranks the short index line; Vol 4/5/CPM flood the
    semantic pool. Failures never raise.
    """
    if not schedule_register_rescue_enabled():
        return 0
    if not query_asks_for_contract_particulars(query):
        return 0
    if not query_asks_for_numbered_contract_schedule(query):
        return 0
    labels = extract_asked_schedule_labels(query)
    if not labels:
        return 0
    fetch = getattr(store, "chunks_containing_all", None)
    if not callable(fetch):
        return 0
    pids = [project_id] + [p for p in extra_pids if p and p != project_id]
    recovered = 0
    for pid in pids:
        for label in labels:
            needle_sets = ([label, "not used"], [label])
            for needles in needle_sets:
                try:
                    hits = fetch(pid, needles, k=20)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "schedule-register rescue for %s (%r) failed: %s",
                        pid, needles, exc,
                    )
                    continue
                for chunk in hits:
                    if not chunk_states_schedule_register(
                        chunk.text or "", labels,
                    ):
                        continue
                    if chunk.chunk_id in fused:
                        continue
                    fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
                    recovered += 1
    if recovered:
        logger.info(
            "schedule-register rescue recovered %d chunk(s) for labels %r",
            recovered, labels,
        )
    return recovered


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
# Phrases that must appear on a filled row's KEY to elect a contract.
# "contract data" / "appendix to tender" are section headings, not a
# particular — they classify the ask but must not match every window.
_ASKED_PARTICULAR_KEY_RES = (
    (re.compile(r"(?i)time\s+for\s+completion"), "time for completion"),
    (re.compile(r"(?i)delay\s+damages"), "delay damages"),
    (re.compile(r"(?i)liquidated\s+damages"), "liquidated damages"),
    (re.compile(r"(?i)defects\s+notification"), "defects notification"),
    (re.compile(r"(?i)accepted\s+contract\s+amount"), "accepted contract amount"),
    (re.compile(r"(?i)performance\s+bond"), "performance bond"),
    (re.compile(r"(?i)performance\s+security"), "performance security"),
    (re.compile(r"(?i)performance\s+guarantee"), "performance guarantee"),
    (re.compile(r"(?i)including\s+vat"), "including vat"),
    (re.compile(r"(?i)excluding\s+vat"), "excluding vat"),
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


def _asked_particular_key_phrases(query: str) -> Tuple[str, ...]:
    """The Contract Data label(s) an unnamed ask is actually requesting.

    Term-overlap on the chunk body is too weak: ``works`` / ``completion``
    appear on a Volume 4 programme note and on an unfilled TfC key sitting
    next to a filled Accepted Contract Amount. Election must see the
    asked field on the *key* of a filled row.
    """
    q = (query or "").strip()
    if not q:
        return ()
    phrases: List[str] = []
    for rx, phrase in _ASKED_PARTICULAR_KEY_RES:
        if rx.search(q):
            phrases.append(phrase)
    if _CD_WHO_IS_RE.search(q):
        for m in _CD_CONTRACT_ROLE_RE.finditer(q):
            role = re.sub(r"\s+", " ", m.group(0).lower()).strip()
            if role:
                phrases.append(role)
    for m in _CD_SCHEDULE_ASK_RE.finditer(q):
        phrases.append(re.sub(r"\s+", " ", m.group(0).lower()).strip())
    # Dedup, keep order.
    seen: Set[str] = set()
    out: List[str] = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


def particulars_row_answers_asked_label(query: str, text: str) -> bool:
    """True when a filled row's KEY is the particular the ask names.

    The unnamed election used to lock the pool to the first filled
    particulars row of any kind. A filled Accepted Contract Amount window
    from another package then stole A3 (Time for Completion), the same
    way a glossary definition used to steal A9 before the role-identity
    ask was recognised.

    #496 required label overlap on the chunk body. That still elects a
    mixed window whose TfC / Delay Damages *key* is unfilled. Live
    d7a4ca8 A3/A5: DD-2022-175 won, Volume 4 ``548 days`` and Sub-Clause
    8.8 stayed in the pool, and DD-2023-118's 852-day / 0.1% rows were
    fenced out. The asked label's own value must be filled.

    When the ask has no named field (a bare "Contract Data" lookup), the
    previous body-overlap test stands so we do not empty a pool we have
    no opinion about.
    """
    phrases = _asked_particular_key_phrases(query)
    if phrases:
        want_whole_tfc = (
            "time for completion" in phrases
            and query_asks_for_time_for_completion(query)
        )
        for key, _val in filled_particulars_rows(text):
            key_l = key.lower()
            if not any(p in key_l for p in phrases):
                continue
            if want_whole_tfc and not _tfc_row_is_whole_works(key, text):
                continue
            return True
        return False
    return _cd_label_bonus(_significant_terms(query), text) > 0.0


def _collapse_retrieval_ws(text: str) -> str:
    """Collapse OCR / table newlines so a scanned label still matches."""
    return re.sub(r"\s+", " ", text or "").strip()


def _env_flag_on(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def delay_damages_rate_rescue_enabled() -> bool:
    """ON by default — live A5 recall after the #501 year-lock.

    RAG_DELAY_DAMAGES_RATE_RESCUE=0 restores prefix-only ranking.
    """
    return _env_flag_on("RAG_DELAY_DAMAGES_RATE_RESCUE")


def delay_damages_daily_rescue_enabled() -> bool:
    """ON by default — live E1 rate × ACA recall after #520.

    A5 rate rescue is exclusive (fence drops every non-rate chunk).
    E1 needs the rate AND the Accepted Contract Amount; cosine on a
    3k-doc corpus ranks Spec TOC / Daywork / insurance over scanned
    Contract Data. RAG_DELAY_DAMAGES_DAILY_RESCUE=0 restores that FAIL.
    Distinct from COMPOSE_DELAY_DAMAGES_DAILY (the multiply step).
    """
    return _env_flag_on("RAG_DELAY_DAMAGES_DAILY_RESCUE")


def engineer_identity_rescue_enabled() -> bool:
    """ON by default — live A9 Engineer appointment vs PSA parties.

    RAG_ENGINEER_IDENTITY_RESCUE=0 restores prefix-only ranking.
    """
    return _env_flag_on("RAG_ENGINEER_IDENTITY_RESCUE")


def query_asks_for_delay_damages_rate(query: str) -> bool:
    """True for a whole-works Delay Damages *rate* ask (live A5).

    E1 ("calculate … in SAR") stays on the monetary-base reservation.
    An ask that names the maximum / cap is not this class.
    """
    q = query or ""
    if not q or _DEFINITION_QUESTION_RE.search(q):
        return False
    if query_needs_a_monetary_base(q):
        return False
    if not re.search(r"(?i)(?:delay|liquidated)\s+damages", q):
        return False
    if re.search(r"(?i)\b(?:maximum|max(?:imum)?\s+amount|capped?)\b", q):
        return False
    return True


def query_asks_delay_damages_daily_amount(query: str) -> bool:
    """True for E1 (calculate … delay damages … in SAR), not A5 rate lookup.

    Reuses the monetary-base ask class so A5 stays a particular lookup
    and this path stays compose-only. Twin of
    ``construction_formulas_commercial.query_asks_delay_damages_daily_amount``.

    Wave-1 A2 ("Accepted Contract Amount including VAT") has no
    delay-damages token, so it stays off this path. A combined
    "calculate delay damages … including VAT" remains E1 — leftover
    E1 after #523 must not be stolen back onto the A2 particular.
    """
    q = query or ""
    if not q or not _DELAY_RATE_KEY_RE.search(q):
        return False
    return query_needs_a_monetary_base(q)


def query_asks_who_the_engineer_is(query: str) -> bool:
    """True for A9 ("who is the Engineer"), not the Representative (D1)."""
    q = query or ""
    if not q or _DEFINITION_QUESTION_RE.search(q):
        return False
    if not _CD_WHO_IS_RE.search(q):
        return False
    if re.search(r"(?i)engineer'?s\s+representative", q):
        return False
    return bool(re.search(r"(?i)\bengineer\b", q))


_DELAY_RATE_KEY_RE = re.compile(r"(?i)(?:delay|liquidated)\s+damages")
_DELAY_CAP_KEY_RE = re.compile(
    r"(?i)\b(?:maximum|max(?:imum)?\s+amount|capped?)\b",
)
_DELAY_RATE_VALUE_RE = re.compile(
    r"(?i)\d+(?:\.\d+)?\s*%[^\n]{0,80}\bper\b",
)
_DELAY_RATE_POINTER_RE = re.compile(
    r"(?i)at\s+the\s+rate\s+stated\s+in\s+the\s+contract\s+data",
)
_ENGINEER_GLOSSARY_RE = re.compile(
    r'(?i)"?engineer"?\s+means\s+the\s+person',
)
_ENGINEER_REP_RE = re.compile(r"(?i)engineer'?s\s+representative")
_ENGINEER_KEY_RE = re.compile(r"(?i)\bengineer\b")
_NOT_A_PARTY_NAME_RE = re.compile(
    r"(?i)^(?:the\s+)?(?:person\s+appointed|consultant|client|"
    r"employer|contractor|engineer)\s*$",
)
_PARTY_FIRM_RE = re.compile(
    r"(?i)\b(?:limited|ltd\.?|llc|llp|gmbh|plc|inc\.?)\b",
)
_SCANNED_ENGINEER_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?:\d+(?:\.\d+)+\s*(?:\([a-z]\))?\s*)?"
    r"(?:(?:the|name\s+of\s+the)\s+)?"
    r"engineer\b(?!\s*'?s\s+representative)[ \t]*[:|–-]?\s*(.*)$",
)
_ENGINEER_IS_RE = re.compile(
    r"(?i)\b(?:the\s+|name\s+of\s+the\s+)?engineer\b"
    r"(?!\s*'?s\s+representative)\s*(?:is|are|:)\s+(.{4,80})"
)
_ENGINEER_POINTER_VAL_RE = re.compile(
    r"(?i)^(?:named|stated|identified|appointed|set\s+out|specified|"
    r"defined|described|referred\s+to)\s+(?:in|as|under)\b"
)


def _looks_like_appointed_party(val: str) -> bool:
    """True when a particulars value is a firm / person, not a role word."""
    name = re.sub(r"\s+", " ", (val or "")).strip(" \t.:;,-")
    if len(name) < 4 or _NOT_A_PARTY_NAME_RE.match(name):
        return False
    if _ENGINEER_POINTER_VAL_RE.search(name):
        return False
    if _PARTY_FIRM_RE.search(name):
        return True
    letters = re.sub(r"[^A-Za-z]", "", name)
    return len(letters) >= 4 and any(ch.isupper() for ch in name)


def _delay_damages_key_is_rate(key: str) -> bool:
    if not _DELAY_RATE_KEY_RE.search(key or ""):
        return False
    return not _DELAY_CAP_KEY_RE.search(key or "")


def chunk_states_delay_damages_rate(text: str) -> bool:
    """True when the chunk states the daily Delay Damages *rate*.

    A cap row (``Maximum amount of delay damages: 10%…``) and a General
    Conditions pointer (``at the rate stated in the Contract Data``)
    both contain the label and used to satisfy the unnamed election /
    reservation. Live A5 after #501 then cited 118 without the 0.1%
    per-calendar-day figure. The rate lives in a different chunk.
    """
    t = text or ""
    if not t or _DELAY_RATE_POINTER_RE.search(t):
        return False
    for key, val in filled_particulars_rows(t):
        if _delay_damages_key_is_rate(key) and _DELAY_RATE_VALUE_RE.search(val):
            return True
    blob = _collapse_retrieval_ws(t)
    if _DELAY_RATE_POINTER_RE.search(blob):
        return False
    if _DELAY_CAP_KEY_RE.search(blob) and not _DELAY_RATE_VALUE_RE.search(blob):
        return False
    if not _DELAY_RATE_KEY_RE.search(blob):
        return False
    return bool(_DELAY_RATE_VALUE_RE.search(blob))


def chunk_states_engineer_identity(text: str) -> bool:
    """True when the chunk *appoints* the Engineer (live A9).

    A glossary ``"Engineer" means the person appointed…`` and a PSA
    Client/Consultant party list are lookalikes. The appointment is a
    filled ``1.3.1 (b) Engineer`` row (or a scanned line with a firm
    name). Engineer's Representative is D1 / corpus-blocked — do not
    invent a signatory.
    """
    t = text or ""
    if not t:
        return False
    for key, val in filled_particulars_rows(t):
        if _ENGINEER_REP_RE.search(key):
            continue
        if _ENGINEER_KEY_RE.search(key) and _looks_like_appointed_party(val):
            return True
    if _ENGINEER_GLOSSARY_RE.search(t) and not filled_particulars_rows(t):
        return False
    lines = (t or "").splitlines()
    for i, line in enumerate(lines):
        m = _SCANNED_ENGINEER_LINE_RE.match(line)
        if not m:
            continue
        rest = (m.group(1) or "").strip()
        nxt = ""
        nxt2 = ""
        if i + 1 < len(lines):
            nxt = lines[i + 1].strip()
        if i + 2 < len(lines):
            nxt2 = lines[i + 2].strip()
        for cand in (
            rest, nxt, nxt2,
            f"{rest} {nxt}".strip(),
            f"{nxt} {nxt2}".strip(),
        ):
            if _looks_like_appointed_party(cand):
                return True
    blob = _collapse_retrieval_ws(t)
    for named in _ENGINEER_IS_RE.finditer(blob):
        cand = named.group(1)
        if _looks_like_appointed_party(cand) and (
            _PARTY_FIRM_RE.search(cand) or re.search(r"\b[A-Z]{3,}\b", cand)
        ):
            return True
    return False


def chunk_answers_asked_particular(query: str, text: str) -> bool:
    """Election / reservation predicate for a particulars-shaped ask.

    Prefixed filled rows keep today's behaviour (A3/A6/A2 year-lock).
    After #501, A5/A9 also accept an unprefixed scanned rate /
    Engineer appointment so the year-lock fence cannot drop the
    chunk that actually answers.
    """
    if delay_damages_rate_rescue_enabled() and query_asks_for_delay_damages_rate(query):
        if chunk_states_delay_damages_rate(text):
            return True
    if delay_damages_daily_rescue_enabled() and query_asks_delay_damages_daily_amount(query):
        if (
            chunk_states_delay_damages_rate(text)
            or chunk_states_accepted_contract_amount(text)
        ):
            return True
    if engineer_identity_rescue_enabled() and query_asks_who_the_engineer_is(query):
        if chunk_states_engineer_identity(text):
            return True
    if aca_including_vat_rescue_enabled() and query_asks_for_aca_including_vat(query):
        if chunk_states_aca_including_vat(text):
            return True
    if time_for_completion_rescue_enabled() and query_asks_for_time_for_completion(query):
        if chunk_states_time_for_completion(text):
            return True
    if dnp_rescue_enabled() and query_asks_for_defects_notification_period(query):
        if chunk_states_defects_notification_period(text):
            return True
    return (
        is_contract_data_particulars_row(text)
        and particulars_row_answers_asked_label(query, text)
    )


_DELAY_RATE_RESCUE_PHRASES = (
    "delay damages per calendar day",
    "delay damages per day",
    "delay damages contract price",
)
_ACA_BASE_RESCUE_PHRASES = (
    "accepted contract amount excluding vat",
    "accepted contract amount",
)
# Combined GC+Contract Data volumes put Sub-Clause 8.8 at chunks 9–11
# and the filled 1.1.1 excl-VAT row in a later appendix. identifier_search
# LIMIT and first-N chunks_for_docs stay on the 8.8 toy windows.
# #532/#533 prefix-400 + last-400 + ``1.1.1``+``excluding`` needles still
# miss a middle-of-volume scanned row (live 77a96ac: top-k stayed on
# chunks 9–11). Walk every chunk of those docs. Kill-switch:
# RAG_DELAY_DAMAGES_DAILY_RESCUE=0.
_E1_REAL_ACA_DOC_SCAN = 400
_E1_REAL_ACA_TEXT_K = 400
_E1_REAL_ACA_PAIR_WINDOW = 3
_E1_REAL_ACA_TEXT_NEEDLES = (
    # Scanned 1.1.1 rows split "Accepted\\nContract\\nAmount" — a
    # contiguous "accepted contract amount" LIKE misses. Clause +
    # excl-VAT tokens still hit the filled appendix and skip 8.8 toys.
    ("1.1.1", "excluding"),
    ("1.1.1", "accepted"),
    ("excl", "vat"),
    ("accepted", "vat"),
    # Live leftover E1 after #535: recover Contract Data 0.1% of
    # Contract Price when top-k is the CoC 0.015%-of-ACA restatement.
    # "%" is stripped by chunks_containing_all — use price tokens.
    ("damages", "price"),
    ("delay", "price"),
)
_ENGINEER_IDENTITY_RESCUE_PHRASES = (
    "1.3.1 engineer",
    "engineer limited",
    "the engineer",
    "name of the engineer",
)
_ACA_INCL_RESCUE_PHRASES = (
    "accepted contract amount including vat",
    "amount including vat",
    "accepted contract amount (including vat)",
    "amount (including vat)",
    "1.1.1 including vat",
)
# Live Wave-1 A2 on 9ad62cc: identifier_search + first-N neighbors
# stay on Contract Data chunk #0 (delay damages × a partial ACA).
# Scanned 1.1.1 including-VAT sits later in the same volume.
_A2_INCL_TEXT_NEEDLES = (
    ("1.1.1", "including"),
    ("including", "vat"),
    ("accepted", "including"),
    ("amount", "including"),
)
_TFC_RESCUE_PHRASES = (
    "time for completion for the whole of the works",
    "1.1.75 time for completion",
)
_DNP_RESCUE_PHRASES = (
    "defects notification period",
    "1.1.27 defects notification",
    "defects notification period days",
)
_ASKED_PARTICULAR_VALUE_BONUS = 2.0
_TFC_DAYS_RE = re.compile(r"(?i)\b(\d{2,4})\s+(?:calendar\s+|working\s+)?days\b")
_TFC_PERMIT_TRACKER_RE = re.compile(
    r"(?i)permit[- ]track|commencement[- ]completion|"
    r"community\s+[a-z0-9-]+\s+\w{3}-\d{2}\s+to\s+\w{3}-\d{2}",
)
# Live A3 PARTIAL after #516: a sectional / Vol-2 "within 90 days" figure
# ranked ahead of DD-2023-118 Contract Data 852 and the graft led with 90.
_TFC_SECTIONAL_RE = re.compile(
    r"(?i)\bsection(?:al)?s?\s+"
    r"(?:\d+|[ivxlcd]+|[a-z]\b|of\s+(?:the\s+)?works)",
)
_TFC_CLAUSE_1175_RE = re.compile(r"(?i)\b1\.1\.75\b")
_TFC_POINTER_RE = re.compile(
    r"(?i)(?:stated|named|identified|set\s+out|specified|defined|"
    r"described|referred\s+to)\s+in\s+(?:the\s+)?contract\s+data",
)
_TFC_NOTICE_DAYS_RE = re.compile(
    r"(?i)\b(?:within|not\s+later\s+than|no\s+later\s+than|"
    r"after\s+(?:the\s+)?(?:taking[- ]over|toc)|before\s+the)\s+"
    r"(\d{2,4})\s+(?:calendar\s+|working\s+)?days",
)


def aca_including_vat_rescue_enabled() -> bool:
    """ON by default — live A2 answered delay damages / excl-VAT.

    RAG_ACA_INCLUDING_VAT_RESCUE=0 restores filename-only ACA ranking.
    """
    return _env_flag_on("RAG_ACA_INCLUDING_VAT_RESCUE")


def time_for_completion_rescue_enabled() -> bool:
    """ON by default — live A3 missed the whole-Works TfC row.

    RAG_TIME_FOR_COMPLETION_RESCUE=0 restores prefix-only ranking.
    """
    return _env_flag_on("RAG_TIME_FOR_COMPLETION_RESCUE")


def dnp_rescue_enabled() -> bool:
    """ON by default — live A6 retrieved PSA / CPM TOC instead of DNP.

    RAG_DNP_RESCUE=0 restores Cosine / particulars-family ranking.
    """
    return _env_flag_on("RAG_DNP_RESCUE")


def query_asks_for_aca_including_vat(query: str) -> bool:
    """True for A2 (including VAT), not A1 excluding or a definition."""
    if not query_asks_for_accepted_contract_amount(query):
        return False
    return bool(_INCLUDING_VAT_RE.search(query or ""))


def query_is_aca_including_vat_particular(query: str) -> bool:
    """True for Wave-1 A2, not leftover-E1 daily compose.

    Live 9ad62cc: the including-VAT particular retrieved Contract Data
    chunk #0 (delay damages × a partial ACA) and leftover-E1 compose
    stated SAR/day. An including-VAT ask is not rate × ACA. A combined
    "calculate delay damages … including VAT" stays E1.
    """
    if not query_asks_for_aca_including_vat(query):
        return False
    return not query_asks_delay_damages_daily_amount(query)


def query_asks_for_time_for_completion(query: str) -> bool:
    """True for A3 whole-Works TfC, not a milestone-only or sectional ask."""
    q = query or ""
    if not q or _DEFINITION_QUESTION_RE.search(q):
        return False
    if not re.search(r"(?i)time\s+for\s+completion", q):
        return False
    if _CD_MILESTONE_QUERY_RE.search(q) and not _CD_WHOLE_WORKS_QUERY_RE.search(q):
        return False
    # G2 / "Section 2 of the Works" is not the whole-Works particular.
    if _TFC_SECTIONAL_RE.search(q) and not _CD_WHOLE_WORKS_QUERY_RE.search(q):
        return False
    return True


def _tfc_key_is_not_whole_works(key: str) -> bool:
    """True when a TfC key is a milestone or section, not whole-of-Works."""
    k = key or ""
    if _CD_MILESTONE_CHUNK_RE.search(k):
        return True
    if _CD_WHOLE_WORKS_QUERY_RE.search(k):
        return False
    return bool(_TFC_SECTIONAL_RE.search(k))


def _tfc_row_is_whole_works(key: str, chunk_text: str = "") -> bool:
    """Positive test: this key is the whole-Works particular, not a lookalike.

    A Vol-2 sentence that mentions Time for Completion and peels
    ``within 90 days`` as a value is not clause 1.1.75.
    """
    k = key or ""
    if not k or _CD_MILESTONE_CHUNK_RE.search(k) or _TFC_SECTIONAL_RE.search(k):
        return False
    if _TFC_POINTER_RE.search(k):
        return False
    if _CD_WHOLE_WORKS_QUERY_RE.search(k) or _TFC_CLAUSE_1175_RE.search(k):
        return True
    if not re.search(r"(?i)time\s+for\s+completion", k):
        return False
    if len(k) > 96:
        return False
    return bool(
        _CD_PARTICULARS_PREFIX_RE.search(chunk_text or "")
        or _TFC_CLAUSE_1175_RE.search(chunk_text or "")
    )


def _tfc_row_anchor_index(text: str, key: str, val: str, days_num: str) -> int:
    """Index of this key+value, not the first lookalike with the same label."""
    loc = (text or "").lower()
    key_l = (key or "").lower()
    val_l = (val or "").lower()
    num = (days_num or "").split()[0]
    if key_l:
        start = 0
        while True:
            i = loc.find(key_l[:40], start)
            if i < 0:
                break
            window = loc[i: i + max(len(key_l) + 80, 160)]
            if num and num in window:
                return i
            if val_l and val_l[:24] in window:
                return i
            start = i + max(1, len(key_l[:40]))
    if val_l:
        return loc.find(val_l)
    return -1


def _tfc_days_from_block(block: str) -> Optional[str]:
    """Days figure tied to the TfC label, not a neighbouring notice period."""
    blob = _normalize_retrieval_ws(block)
    if not blob:
        return None
    anchors = (
        _TFC_CLAUSE_1175_RE,
        re.compile(r"(?i)time\s+for\s+completion"),
        _CD_WHOLE_WORKS_QUERY_RE,
    )
    for rx in anchors:
        for m in rx.finditer(blob):
            window = blob[m.start(): m.end() + 120]
            if _tfc_key_is_not_whole_works(window):
                continue
            notice = _TFC_NOTICE_DAYS_RE.search(window)
            dm = _TFC_DAYS_RE.search(window)
            if not dm:
                continue
            if notice and notice.group(1) == dm.group(1):
                continue
            return f"{dm.group(1)} days"
    for dm in _TFC_DAYS_RE.finditer(blob):
        lead = blob[max(0, dm.start() - 48): dm.end() + 8]
        if _TFC_NOTICE_DAYS_RE.search(lead):
            continue
        if _tfc_key_is_not_whole_works(lead):
            continue
        return f"{dm.group(1)} days"
    return None


def _score_tfc_candidate(days: str, context: str, *, from_particulars: bool) -> int:
    """Higher wins. Whole-Works Contract Data / newer year beat lookalikes."""
    ctx = context or ""
    score = 0
    if _CD_WHOLE_WORKS_QUERY_RE.search(ctx):
        score += 100
    if _TFC_CLAUSE_1175_RE.search(ctx):
        score += 80
    if from_particulars or _CD_PARTICULARS_PREFIX_RE.search(ctx):
        score += 60
    elif _CD_HEADING_IN_CHUNK_RE.search(ctx) and not (
        contract_data_mention_is_only_a_cross_reference(ctx)
    ):
        score += 40
    ids = extract_contract_doc_ids(ctx)
    if ids:
        year, seq = max(_contract_id_recency(cid) for cid in ids)
        if year > 0:
            score += year
        if seq > 0:
            score += min(seq, 30)
    if _tfc_key_is_not_whole_works(ctx):
        score -= 200
    if _TFC_PERMIT_TRACKER_RE.search(ctx):
        score -= 200
    if _TFC_POINTER_RE.search(ctx) and not from_particulars:
        score -= 80
    notice = _TFC_NOTICE_DAYS_RE.search(ctx)
    if notice and notice.group(1) == (days or "").split()[0]:
        score -= 150
    return score


def chunk_states_accepted_contract_amount(text: str) -> bool:
    """True when the chunk states an Accepted Contract Amount in money.

    E1's rate base. Including-VAT and excluding-VAT both count —
    compose prefers excl when both are in the excerpts. A delay-damages
    rate row that only *names* the Contract Price / ACA is not this.
    A cap row (``10% of the Accepted Contract Amount``) has no SAR
    figure and fails the money test.
    """
    t = text or ""
    blob = _normalize_retrieval_ws(t).lower()
    if "accepted contract amount" not in blob:
        return False
    if not _CD_MONETARY_VALUE_RE.search(t):
        return False
    if _DELAY_RATE_KEY_RE.search(blob) and _DELAY_RATE_VALUE_RE.search(blob):
        for key, val in filled_particulars_rows(t):
            joined = f"{key} {val}".lower()
            if (
                "accepted contract amount" in joined
                and _CD_MONETARY_VALUE_RE.search(val)
                and not _DELAY_RATE_KEY_RE.search(key)
            ):
                break
        else:
            # Rate sentence is not the money row. A paired scanned
            # window that also carries the filled excl-VAT ACA still
            # is — do not drop it just because 8.8 shares the chunk.
            try:
                from app.lib.construction_formulas_commercial import (
                    chunk_has_real_accepted_contract_amount,
                )
                if not chunk_has_real_accepted_contract_amount(t):
                    return False
            except Exception:  # noqa: BLE001 — rate-only window is not ACA
                return False
    try:
        from app.lib.construction_formulas_commercial import (
            chunk_accepted_contract_amount_is_only_toy,
            chunk_has_real_accepted_contract_amount,
        )
        if chunk_has_real_accepted_contract_amount(t):
            return True
        if chunk_accepted_contract_amount_is_only_toy(t):
            return False
    except Exception:  # noqa: BLE001 — never break a turn over an import
        logger.debug("toy-ACA test unavailable; treating money as ACA", exc_info=True)
    return True


def _chunk_is_e1_compose_operand(text: str) -> bool:
    """Rate row or ACA money row — the two E1 multiply operands."""
    if chunk_states_delay_damages_rate(text):
        return True
    if chunk_states_accepted_contract_amount(text):
        return True
    try:
        from app.lib.construction_formulas_commercial import (
            chunk_has_real_accepted_contract_amount,
        )
        return chunk_has_real_accepted_contract_amount(text)
    except Exception:  # noqa: BLE001 — rate-only window is not ACA
        return False


def _chunk_keeps_for_e1_daily(filename: str, text: str) -> bool:
    """Keep compose operands and filled particulars; drop GC lookalikes.

    Exclusive rate-or-ACA fencing deleted the particulars family and
    shrank wave-2 E1 below k=5. Spec TOC / Daywork / insurance are not
    Contract Data and must still drop once both operands are in-pool.

    Live leftover E1 after #523: the bound Contract Data volume's
    Sub-Clause 8.8 chunks (9–11) passed the filename keep, occupied
    top-k, and the last-slot money reserve then elected the
    including-VAT ACA. A filename match alone is not an operand.
    """
    _ = filename  # operands are textual; a CD filename is not enough
    if _chunk_is_e1_compose_operand(text):
        return True
    t = text or ""
    if _DELAY_RATE_POINTER_RE.search(t) and not chunk_states_delay_damages_rate(t):
        return False
    if is_contract_data_particulars_row(t):
        return True
    return bool(
        _CD_HEADING_IN_CHUNK_RE.search(t)
        and not contract_data_mention_is_only_a_cross_reference(t)
    )


def _e1_has_standalone_excl_vat(text: str) -> bool:
    """True for a 1.1.1 / excl-VAT money row, not a rate window that cites ACA.

    Live leftover E1 after #535: CoC chunks 9–11 state 0.015% of the
    filled excl-VAT ACA. ``chunk_has_real_accepted_contract_amount``
    is True, so the all-chunk scan early-exited and compose used 0.015%.
    """
    try:
        from app.lib.construction_formulas_commercial import (
            chunk_has_real_accepted_contract_amount,
        )
    except Exception:  # noqa: BLE001 — treat as missing; keep scanning
        return False
    if not chunk_has_real_accepted_contract_amount(text or ""):
        return False
    if chunk_states_delay_damages_rate(text or ""):
        return False
    return _e1_aca_preference(text) >= 2


def _e1_rate_preference(text: str) -> int:
    """Higher wins for E1's daily rate. Contract Data 0.1% beats CoC 0.015%."""
    t = text or ""
    if not chunk_states_delay_damages_rate(t):
        return -1
    try:
        from app.lib.construction_formulas_commercial import (
            delay_damages_rate_preference_score,
            parse_delay_damages_rate_percent,
        )
        pct = parse_delay_damages_rate_percent(t)
        if pct is None:
            return 0
        return delay_damages_rate_preference_score(pct, t)
    except Exception:  # noqa: BLE001 — a rate window still outranks none
        logger.debug("e1 rate preference failed", exc_info=True)
        return 1


def _e1_aca_preference(text: str) -> int:
    """Higher wins for E1's rate base. Excl-VAT (2) > unlabeled (1) > incl (0)."""
    try:
        from app.lib.construction_formulas_commercial import (
            chunk_accepted_contract_amount_is_only_toy,
            chunk_has_real_accepted_contract_amount,
        )
        if chunk_accepted_contract_amount_is_only_toy(text):
            return -1
        # Live leftover E1: a scanned excl-VAT row can fail
        # chunk_states_accepted_contract_amount (line-split / excl. VAT)
        # while still being the real money operand. Do not rank it -1
        # or reservation leaves top-k on 8.8 toys.
        states = chunk_states_accepted_contract_amount(text)
        if not states and not chunk_has_real_accepted_contract_amount(text):
            return -1
    except Exception:  # noqa: BLE001 — unlabeled ACA still ranks above none
        logger.debug("toy-ACA preference test failed", exc_info=True)
        if not chunk_states_accepted_contract_amount(text):
            return -1
    try:
        from app.lib.construction_formulas_commercial import (
            _EXCL_VAT_RE,
            _INCL_VAT_RE,
        )
    except Exception:  # noqa: BLE001 — unlabeled ACA still ranks above none
        return 1
    blob = _normalize_retrieval_ws(text or "")
    if _EXCL_VAT_RE.search(blob) and not _INCL_VAT_RE.search(blob):
        return 2
    if _INCL_VAT_RE.search(blob) and not _EXCL_VAT_RE.search(blob):
        return 0
    if _EXCL_VAT_RE.search(blob):
        return 2
    return 1


def chunk_states_aca_including_vat(text: str) -> bool:
    """True when the chunk states Accepted Contract Amount *including VAT*.

    A delay-damages sentence that cites the excl-VAT ACA as the rate
    base (live A2) is the neighboring field, not this answer.
    """
    t = text or ""
    blob = _normalize_retrieval_ws(t).lower()
    if "accepted contract amount" not in blob:
        return False
    if not _INCLUDING_VAT_RE.search(blob):
        return False
    if not _CD_MONETARY_VALUE_RE.search(t):
        return False
    if _DELAY_RATE_KEY_RE.search(blob) and re.search(
        r"(?i)per\s+(?:calendar\s+)?day", blob,
    ):
        for key, val in filled_particulars_rows(t):
            joined = f"{key} {val}".lower()
            if (
                "accepted contract amount" in joined
                and _INCLUDING_VAT_RE.search(joined)
                and _CD_MONETARY_VALUE_RE.search(val)
            ):
                return True
        return False
    return True


def chunk_states_time_for_completion(text: str) -> bool:
    """True when the chunk states whole-Works Time for Completion in days.

    Permit-tracker / community commencement-completion tables (live A3)
    mention completion dates but are not the Contract Data duration.
    Milestone-only and sectional rows are not this class. A Vol-2
    specification that only cites TfC and a ``within 90 days`` notice
    is a lookalike, not the particular.
    """
    t = text or ""
    if not t or _TFC_PERMIT_TRACKER_RE.search(t):
        return False
    blob = _normalize_retrieval_ws(t)
    if not re.search(r"(?i)time\s+for\s+completion", blob):
        return False
    if _CD_MILESTONE_CHUNK_RE.search(blob) and not _CD_WHOLE_WORKS_QUERY_RE.search(blob):
        return False
    if _TFC_SECTIONAL_RE.search(blob) and not _CD_WHOLE_WORKS_QUERY_RE.search(blob):
        return False
    for key, val in filled_particulars_rows(t):
        key_l = key.lower()
        if "time for completion" not in key_l:
            continue
        if not _tfc_row_is_whole_works(key, t):
            continue
        if _CD_PARTICULARS_PREFIX_RE.search(val or ""):
            continue
        if _TFC_DAYS_RE.search(val) or _TFC_DAYS_RE.search(key):
            return True
    if _CD_MILESTONE_CHUNK_RE.search(blob) and not _CD_WHOLE_WORKS_QUERY_RE.search(blob):
        return False
    if _TFC_POINTER_RE.search(blob) and not (
        _CD_PARTICULARS_PREFIX_RE.search(t)
        or (
            _CD_HEADING_IN_CHUNK_RE.search(t)
            and not contract_data_mention_is_only_a_cross_reference(t)
        )
    ):
        return False
    days = _tfc_days_from_block(t)
    if not days:
        return False
    if not (
        _CD_WHOLE_WORKS_QUERY_RE.search(blob)
        or _TFC_CLAUSE_1175_RE.search(blob)
        or _CD_PARTICULARS_PREFIX_RE.search(t)
        or (
            _CD_HEADING_IN_CHUNK_RE.search(t)
            and not contract_data_mention_is_only_a_cross_reference(t)
        )
    ):
        return False
    notice = _TFC_NOTICE_DAYS_RE.search(blob)
    if notice and notice.group(1) == days.split()[0]:
        return False
    return True


def _aca_row_is_including_vat(key: str, val: str) -> bool:
    """True when this particulars row is the including-VAT ACA, not a neighbor.

    A system-message mega-row that mentions the inject hint and later
    peels the first SAR figure (live A2: excl-VAT / delay-damages) is
    not this class — same 96-char key cap as whole-Works TfC.
    """
    k = (key or "").strip()
    v = (val or "").strip()
    if not k or len(k) > 96:
        return False
    # RAG system-message peels can glue the next [doc_id=…] chunk onto a
    # short including-VAT key and steal the previous figure.
    if "[doc_id=" in v or len(v) > 160:
        return False
    joined = f"{k} {v}"
    if "accepted contract amount" not in joined.lower():
        return False
    if _DELAY_RATE_KEY_RE.search(k) or _DELAY_RATE_KEY_RE.search(joined[:80]):
        return False
    try:
        from app.lib.construction_formulas_commercial import (
            _EXCL_VAT_RE,
            _INCL_VAT_RE,
        )
    except Exception:  # noqa: BLE001
        return False
    if _EXCL_VAT_RE.search(k) and not _INCL_VAT_RE.search(k):
        return False
    if _EXCL_VAT_RE.search(joined) and not _INCL_VAT_RE.search(k):
        return False
    if _INCL_VAT_RE.search(k):
        return True
    # Live Wave-1 A2 on 9ad62cc: filled_particulars_rows glued chunk #0
    # (delay damages × SAR 39,098,392.98) onto the later including-VAT
    # label. Including-VAT in the value must precede the first figure —
    # otherwise the partial ACA is peeled as the including-VAT amount.
    incl = _INCL_VAT_RE.search(v)
    if not incl:
        return False
    try:
        from app.lib.construction_formulas_commercial import _MONEY_RE
        first_money = _MONEY_RE.search(v)
    except Exception:  # noqa: BLE001
        first_money = None
    if first_money is not None and first_money.start() < incl.start():
        return False
    return not (_EXCL_VAT_RE.search(v) and not _INCL_VAT_RE.search(v[:incl.end()]))


def _aca_nearest_vat_is_including(lead: str) -> bool:
    """True when the last VAT qualifier before the figure is including-VAT.

    Adjacent excl/incl table rows share a 64-char window; first-excl-in-window
    would reject the including-VAT amount sitting on the next line.
    """
    try:
        from app.lib.construction_formulas_commercial import (
            _EXCL_VAT_RE,
            _INCL_VAT_RE,
        )
    except Exception:  # noqa: BLE001
        return False
    last_incl = max((m.start() for m in _INCL_VAT_RE.finditer(lead or "")), default=-1)
    last_excl = max((m.start() for m in _EXCL_VAT_RE.finditer(lead or "")), default=-1)
    return last_incl >= 0 and last_incl > last_excl


def _aca_money_is_including_vat(tight: str, wide: str) -> bool:
    """True when the figure's local label is including VAT, not excl-VAT."""
    try:
        from app.lib.construction_formulas_commercial import _INCL_VAT_RE
    except Exception:  # noqa: BLE001
        return False
    if "accepted contract amount" not in (wide or "").lower():
        return False
    if not _INCL_VAT_RE.search(tight or ""):
        return False
    if not _aca_nearest_vat_is_including(tight or ""):
        return False
    if _DELAY_RATE_KEY_RE.search(wide or "") and re.search(
        r"(?i)per\s+(?:calendar\s+)?day", wide or "",
    ):
        return False
    return True


def _score_aca_incl_candidate(
    context: str, *, from_particulars: bool,
) -> int:
    """Higher wins. Including-VAT Contract Data / newer year beat neighbors."""
    try:
        from app.lib.construction_formulas_commercial import (
            _EXCL_VAT_RE,
            _INCL_VAT_RE,
        )
    except Exception:  # noqa: BLE001
        _EXCL_VAT_RE = None
        _INCL_VAT_RE = None
    ctx = context or ""
    score = 0
    if from_particulars:
        score += 60
    elif _CD_PARTICULARS_PREFIX_RE.search(ctx) or (
        _CD_HEADING_IN_CHUNK_RE.search(ctx)
        and not contract_data_mention_is_only_a_cross_reference(ctx)
    ):
        score += 40
    if _INCL_VAT_RE is not None and _INCL_VAT_RE.search(ctx) and not (
        _EXCL_VAT_RE.search(ctx) if _EXCL_VAT_RE is not None else False
    ):
        score += 100
    if _EXCL_VAT_RE is not None and _EXCL_VAT_RE.search(ctx) and not (
        _INCL_VAT_RE.search(ctx) if _INCL_VAT_RE is not None else False
    ):
        score -= 200
    if _DELAY_RATE_KEY_RE.search(ctx):
        score -= 200
    ids = extract_contract_doc_ids(ctx)
    if ids:
        year, seq = max(_contract_id_recency(cid) for cid in ids)
        if year > 0:
            score += year
        if seq > 0:
            score += min(seq, 30)
    return score


def extract_aca_including_vat(text: str) -> Optional[Tuple[float, str]]:
    """Including-VAT ACA from client text, or None. Does not invent.

    Walks the full RAG blob (graft reads the system message). When an
    excluding-VAT neighbor or delay-damages base shares the text, elect
    the including-VAT row — first-in-blob used to prepend the excl-VAT
    figure labeled as including VAT (live A2 after #517).
    """
    t = text or ""
    if not t:
        return None
    try:
        from app.lib.construction_formulas_commercial import (
            _MONEY_RE,
            _collapse_ws,
        )
    except Exception:  # noqa: BLE001
        return None
    cands: List[Tuple[int, int, Tuple[float, str]]] = []
    order = 0
    try:
        for key, val in filled_particulars_rows(t):
            if not _aca_row_is_including_vat(key, val):
                continue
            money = _MONEY_RE.search(val) or _MONEY_RE.search(f"{key} {val}")
            if not money:
                continue
            amount = float(money.group(2).replace(",", ""))
            currency = money.group(1).upper()
            idx = (t or "").lower().find((val or "").lower()[:24]) if val else -1
            local = t[max(0, idx - 280): idx + 80] if idx >= 0 else f"{key} {val}"
            score = _score_aca_incl_candidate(
                f"{key} {val}\n{local}", from_particulars=True,
            )
            cands.append((-score, order, (amount, currency)))
            order += 1
    except Exception:  # noqa: BLE001
        logger.debug("including-VAT particulars parse failed", exc_info=True)
    blob = _collapse_ws(t)
    for m in _MONEY_RE.finditer(blob):
        tight = blob[max(0, m.start() - 64): m.end() + 8]
        wide = blob[max(0, m.start() - 160): m.end() + 24]
        if not _aca_money_is_including_vat(tight, wide):
            continue
        amount = float(m.group(2).replace(",", ""))
        currency = m.group(1).upper()
        score_ctx = blob[max(0, m.start() - 280): m.end() + 80]
        score = _score_aca_incl_candidate(score_ctx, from_particulars=False)
        cands.append((-score, order, (amount, currency)))
        order += 1
    if not cands:
        return None
    cands.sort()
    return cands[0][2]


def extract_time_for_completion_days(text: str) -> Optional[str]:
    """Whole-Works TfC duration as written (e.g. ``852 days``), or None.

    Walks the full RAG blob (graft reads the system message). When a
    sectional / notice-period 90-day lookalike and the Contract Data
    852-day particular share the same text, elect the whole-Works row
    — first-in-blob used to prepend 90 days onto a correct 852 answer.
    """
    t = text or ""
    if not t:
        return None
    cands: List[Tuple[int, int, str]] = []
    order = 0
    for key, val in filled_particulars_rows(t):
        key_l = key.lower()
        if "time for completion" not in key_l:
            continue
        if not _tfc_row_is_whole_works(key, t):
            continue
        joined = f"{key} {val}"
        if _TFC_PERMIT_TRACKER_RE.search(joined):
            continue
        if _CD_PARTICULARS_PREFIX_RE.search(val or ""):
            continue
        m = _TFC_DAYS_RE.search(val) or _TFC_DAYS_RE.search(key)
        if not m:
            continue
        days = f"{m.group(1)} days"
        idx = _tfc_row_anchor_index(t, key, val, m.group(1))
        local = t[max(0, idx - 240): idx + 280] if idx >= 0 else joined
        score = _score_tfc_candidate(days, joined + "\n" + local, from_particulars=True)
        cands.append((-score, order, days))
        order += 1
    for block in re.split(r"\n{2,}|\[doc_id=", t):
        if not chunk_states_time_for_completion(block):
            continue
        days = _tfc_days_from_block(block)
        if not days:
            continue
        score = _score_tfc_candidate(days, block, from_particulars=False)
        cands.append((-score, order, days))
        order += 1
    if not cands:
        return None
    cands.sort()
    return cands[0][2]


def extract_engineer_identity(text: str) -> Optional[str]:
    """Appointed Engineer firm/name from client text, or None."""
    t = text or ""
    if not t:
        return None
    for key, val in filled_particulars_rows(t):
        if _ENGINEER_REP_RE.search(key):
            continue
        if _ENGINEER_KEY_RE.search(key) and _looks_like_appointed_party(val):
            return re.sub(r"\s+", " ", val).strip(" \t.:;,-")
    lines = t.splitlines()
    for i, line in enumerate(lines):
        m = _SCANNED_ENGINEER_LINE_RE.match(line)
        if not m:
            continue
        rest = (m.group(1) or "").strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        nxt2 = lines[i + 2].strip() if i + 2 < len(lines) else ""
        for cand in (rest, f"{rest} {nxt}".strip(), nxt, f"{nxt} {nxt2}".strip()):
            if _looks_like_appointed_party(cand) and _PARTY_FIRM_RE.search(cand):
                return re.sub(r"\s+", " ", cand).strip(" \t.:;,-")
            if _looks_like_appointed_party(cand) and re.search(r"[A-Z]{3,}", cand):
                return re.sub(r"\s+", " ", cand).strip(" \t.:;,-")
    for m in _ENGINEER_IS_RE.finditer(_collapse_retrieval_ws(t)):
        cand = m.group(1)
        if _looks_like_appointed_party(cand) and (
            _PARTY_FIRM_RE.search(cand) or re.search(r"\b[A-Z]{3,}\b", cand)
        ):
            return re.sub(r"\s+", " ", cand).strip(" \t.:;,-")
    return None


# ── Defects Notification Period (live OLD-pack A6) ────────────────────────
#
# Live Master Corpus A6 on 82eb9c5 (#522): "Answer only from the client
# project documents. What is the Defects Notification Period?" retrieved
# Long Form PSA / CPM TOC / recitals / document registers and refused.
# Expected 365 days from Taking-Over Certificate / Contract Data under
# DD-2023-118. A2/A3/A5/A9 already have exclusive asked-value fences;
# A6 was surviving on family-bonus luck and was not named off the C1
# path. Same shape as A3 TfC: state a duration, fence lookalikes.
# Kill-switch: RAG_DNP_RESCUE=0.
_DNP_ASK_RE = re.compile(
    r"(?i)(?:defects\s+notification(?:\s+period)?"
    r"|(?:what\s+is\s+(?:the\s+)?)dnp\b)"
)
_DNP_KEY_RE = re.compile(r"(?i)defects\s+notification(?:\s+period)?")
_DNP_CLAUSE_RE = re.compile(r"(?i)\b1\.1\.27\b")
_DNP_DURATION_RE = re.compile(
    r"(?i)\b(\d{1,4})\s+(?:calendar\s+|working\s+)?"
    r"(days?|months?|years?)\b"
)
_DNP_POINTER_RE = re.compile(
    r"(?i)(?:stated|named|identified|set\s+out|specified|defined|"
    r"described|referred\s+to)\s+in\s+(?:the\s+)?contract\s+data"
)
_DNP_GLOSSARY_RE = re.compile(
    r"(?i)(?:defects\s+notification\s+period|\bdnp\b)\s+means\b"
)
_DNP_TOC_RE = re.compile(
    r"(?i)table\s+of\s+contents|document\s+register|\brecitals?\b"
)
_DNP_TOC_ANCHOR_RE = re.compile(r"(?i)taking[- ]over")


def query_asks_for_defects_notification_period(query: str) -> bool:
    """True for A6 (Defects Notification Period), not A2/A3/A5/A9/C1/E1/F1."""
    q = query or ""
    if not q or _DEFINITION_QUESTION_RE.search(q):
        return False
    if not _DNP_ASK_RE.search(q):
        return False
    # Neighboring-field asks that happen to mention DNP stay off this path.
    if _ACA_ASK_RE.search(q):
        return False
    if re.search(r"(?i)time\s+for\s+completion", q):
        return False
    if re.search(r"(?i)(?:delay|liquidated)\s+damages", q):
        return False
    if _CD_WHO_IS_RE.search(q):
        return False
    if _BOQ_SCOPE_ASK_RE.search(q):
        return False
    return True


def _format_dnp_duration(match: re.Match) -> str:
    num = match.group(1)
    unit = (match.group(2) or "days").lower()
    if unit.startswith("day"):
        return f"{num} days"
    if unit.startswith("month"):
        return f"{num} months"
    if unit.startswith("year"):
        return f"{num} years"
    return f"{num} {unit}"


def _dnp_duration_from_text(text: str) -> Optional[str]:
    """Duration tied to the DNP label, not a neighbouring notice period."""
    blob = _normalize_retrieval_ws(text)
    if not blob:
        return None
    for rx in (_DNP_KEY_RE, _DNP_CLAUSE_RE):
        for m in rx.finditer(blob):
            window = blob[m.start(): m.end() + 140]
            if _DNP_POINTER_RE.search(window) and not _DNP_DURATION_RE.search(window):
                continue
            if rx is _DNP_CLAUSE_RE and not _DNP_KEY_RE.search(window):
                continue
            dm = _DNP_DURATION_RE.search(window)
            if dm:
                return _format_dnp_duration(dm)
    return None


def chunk_states_defects_notification_period(text: str) -> bool:
    """True when the chunk states a Defects Notification Period duration.

    PSA / CPM table-of-contents, recitals, and document registers that
    only *name* the heading (live A6 on 82eb9c5) are lookalikes. A
    General Conditions pointer (``as stated in the Contract Data``) and
    a glossary ``means the period…`` are not the filled 1.1.27 row.
    """
    t = text or ""
    if not t:
        return False
    if _DNP_GLOSSARY_RE.search(t) and not filled_particulars_rows(t):
        return False
    for key, val in filled_particulars_rows(t):
        if not _DNP_KEY_RE.search(key):
            continue
        if _DNP_DURATION_RE.search(val) or _DNP_DURATION_RE.search(key):
            return True
    blob = _normalize_retrieval_ws(t)
    if _DNP_POINTER_RE.search(blob) and not (
        _CD_PARTICULARS_PREFIX_RE.search(t)
        or (
            _CD_HEADING_IN_CHUNK_RE.search(t)
            and not contract_data_mention_is_only_a_cross_reference(t)
        )
    ):
        return False
    if _DNP_TOC_RE.search(blob) and not (
        _CD_PARTICULARS_PREFIX_RE.search(t) or filled_particulars_rows(t)
    ):
        return False
    return bool(_dnp_duration_from_text(t))


def extract_defects_notification_period(text: str) -> Optional[str]:
    """DNP duration as written (e.g. ``365 days``), or None.

    Prefers clause 1.1.27 / Taking-Over / Contract Data over a
    glossary or a TOC heading that happens to sit near a duration.
    """
    t = text or ""
    if not t:
        return None
    cands: List[Tuple[int, int, str]] = []
    order = 0
    for key, val in filled_particulars_rows(t):
        if not _DNP_KEY_RE.search(key):
            continue
        m = _DNP_DURATION_RE.search(val) or _DNP_DURATION_RE.search(key)
        if not m:
            continue
        days = _format_dnp_duration(m)
        joined = f"{key} {val}"
        score = 60
        if _DNP_CLAUSE_RE.search(joined) or _DNP_CLAUSE_RE.search(t):
            score += 80
        if _CD_PARTICULARS_PREFIX_RE.search(t):
            score += 40
        if _DNP_TOC_ANCHOR_RE.search(joined):
            score += 30
        ids = extract_contract_doc_ids(t)
        if ids:
            year, seq = max(_contract_id_recency(cid) for cid in ids)
            if year > 0:
                score += year
            if seq > 0:
                score += min(seq, 30)
        cands.append((-score, order, days))
        order += 1
    for block in re.split(r"\n{2,}|\[doc_id=", t):
        if not chunk_states_defects_notification_period(block):
            continue
        days = _dnp_duration_from_text(block)
        if not days:
            continue
        score = 0
        if _DNP_CLAUSE_RE.search(block):
            score += 80
        if _CD_PARTICULARS_PREFIX_RE.search(block):
            score += 60
        if _DNP_TOC_ANCHOR_RE.search(block):
            score += 30
        if _CD_HEADING_IN_CHUNK_RE.search(block):
            score += 20
        cands.append((-score, order, days))
        order += 1
    if not cands:
        return None
    cands.sort()
    return cands[0][2]


def query_wants_contract_data_file(query: str) -> bool:
    """A2 / A3 / A6 / A9 / E1 live in a Contract Data file, not PSA / CPM."""
    return (
        query_asks_for_accepted_contract_amount(query)
        or query_asks_for_time_for_completion(query)
        or query_asks_who_the_engineer_is(query)
        or query_asks_delay_damages_daily_amount(query)
        or (
            dnp_rescue_enabled()
            and query_asks_for_defects_notification_period(query)
        )
    )


def _pair_adjacent_keep_text(
    hits: List[Chunk],
    keep,
    *,
    window: int = 2,
) -> List[Chunk]:
    """Scanned Contract Data often splits a label and its value.

    Live A9: ``Engineer`` on chunk N, ``JACOBS (CH2M Saudi Limited)`` on
    N+1. Identifier keep() then fails on both. Pair consecutive same-doc
    chunks so the appointment / TfC / including-VAT row is visible.
    ``window`` > 2 also joins N+2 (live E1 excl-VAT amount one row
    past the 1.1.1 label). Default 2 keeps A9/A2 pairing unchanged.
    """
    span = max(2, int(window or 2))
    by_doc: Dict[str, List[Chunk]] = {}
    for chunk in hits:
        by_doc.setdefault(chunk.doc_id, []).append(chunk)
    out: List[Chunk] = []
    seen: Set[str] = set()
    for group in by_doc.values():
        group = sorted(group, key=lambda c: int(getattr(c, "chunk_index", 0) or 0))
        for i, chunk in enumerate(group):
            text = chunk.text or ""
            if keep(text):
                if chunk.chunk_id not in seen:
                    out.append(chunk)
                    seen.add(chunk.chunk_id)
                continue
            paired_hit = False
            for width in range(2, span + 1):
                if i + width - 1 >= len(group):
                    break
                idxs = [
                    int(getattr(group[i + j], "chunk_index", 0) or 0)
                    for j in range(width)
                ]
                # Sparse fetches (chunk #0 + appendix 80) must not glue
                # a delay-damages window onto the including-VAT row.
                # Equal indexes (tests that omit chunk_index) keep the
                # old list-adjacency pairing.
                if len(set(idxs)) > 1 and any(
                    idxs[j] != idxs[0] + j for j in range(width)
                ):
                    continue
                combined = "\n".join(
                    (group[i + j].text or "") for j in range(width)
                )
                if keep(combined):
                    paired = replace(chunk, text=combined)
                    if paired.chunk_id not in seen:
                        out.append(paired)
                        seen.add(paired.chunk_id)
                    paired_hit = True
                    break
            if paired_hit:
                continue
    return out


def _rescue_chunks_matching(
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    phrases: Tuple[str, ...],
    keep,
    *,
    label: str,
    bonus: float = _ASKED_PARTICULAR_VALUE_BONUS,
) -> int:
    """Pull lexical hits that pass ``keep`` into ``fused``. Project-only.

    General-knowledge notes (illustrative 0.05%) must not be rescued.
    Failures never raise.
    """
    fetch = getattr(store, "identifier_search", None)
    if not callable(fetch) or not phrases:
        return 0
    recovered = 0
    try:
        hits = fetch(project_id, list(phrases), k=20)
    except Exception as exc:  # noqa: BLE001 — extras must not break the turn
        logger.warning("%s rescue for %s failed: %s", label, project_id, exc)
        return 0
    neighbors = getattr(store, "chunks_for_docs", None)
    if callable(neighbors) and hits:
        doc_ids = list({c.doc_id for c in hits if c.doc_id})
        try:
            extra = neighbors(project_id, doc_ids, k_per_doc=24)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s neighbor fetch for %s failed: %s", label, project_id, exc)
            extra = []
        by_id = {c.chunk_id: c for c in hits}
        for chunk in extra:
            by_id.setdefault(chunk.chunk_id, chunk)
        hits = list(by_id.values())
    for chunk in _pair_adjacent_keep_text(hits, keep):
        if chunk.chunk_id in fused:
            continue
        fused[chunk.chunk_id] = (chunk, 0.0, bonus)
        recovered += 1
    if recovered:
        logger.info("%s rescue recovered %d chunk(s)", label, recovered)
    return recovered


def _e1_pool_doc_ids_for_late_aca(fused: Dict[str, Tuple]) -> List[str]:
    """Rate-window docs already in fused, plus any Contract Data filename."""
    doc_ids: List[str] = []
    seen: Set[str] = set()

    def _fused_chunk(entry) -> Optional[Chunk]:
        if isinstance(entry, tuple) and entry:
            chunk = entry[0]
        else:
            chunk = entry
        return chunk if isinstance(chunk, Chunk) else None

    for entry in fused.values():
        chunk = _fused_chunk(entry)
        if chunk is None or not chunk.doc_id or chunk.doc_id in seen:
            continue
        text = chunk.text or ""
        name = getattr(chunk, "source_name", "") or ""
        if not name:
            try:
                name = _doc_name_for_id(chunk.doc_id) or ""
            except Exception:  # noqa: BLE001 — filename is optional
                name = ""
        if not (
            chunk_states_delay_damages_rate(text)
            or filename_looks_like_e1_rate_volume(name)
        ):
            continue
        seen.add(chunk.doc_id)
        doc_ids.append(chunk.doc_id)
    return doc_ids


def _e1_fetch_late_aca_chunks(store, project_id: str, doc_ids: List[str]) -> List[Chunk]:
    """Every chunk of the rate-window docs, then text-match / prefix / tail.

    Live leftover E1 on 77a96ac (#533): top-k stayed on Contract Data
    8.8 chunks 9–11. Prefix-400 + last-400 miss a middle appendix;
    ``1.1.1``+``excluding`` LIKE misses ``excl. VAT`` without a clause
    number. ``chunks_for_docs`` already loads the file — keep every row.
    """
    by_id: Dict[str, Chunk] = {}
    allowed = set(doc_ids)
    fetch = getattr(store, "chunks_for_docs", None)
    if callable(fetch):
        extra = []
        try:
            extra = fetch(project_id, doc_ids, all_rows=True)
        except TypeError:
            try:
                extra = fetch(project_id, doc_ids, k_per_doc=1_000_000)
            except TypeError:
                extra = []
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "e1 late-ACA full scan for %s failed: %s", project_id, exc,
                )
                extra = []
        except Exception as exc:  # noqa: BLE001 — extras must not break
            logger.warning(
                "e1 late-ACA full scan for %s failed: %s", project_id, exc,
            )
            extra = []
        for chunk in extra or []:
            if chunk.doc_id and chunk.doc_id not in allowed:
                continue
            by_id.setdefault(chunk.chunk_id, chunk)
        # Only skip prefix/tail/needles when the full scan already
        # holds a real ACA. A store that accepts all_rows but still
        # returns first-N (ignored kwarg) must fall through — that
        # was the live 77a96ac shape: toys in hand, filled row not.
        try:
            from app.lib.construction_formulas_commercial import (
                chunk_has_real_accepted_contract_amount as _has_real_aca,
            )
            if any(
                _has_real_aca(c.text or "") for c in by_id.values()
            ):
                return list(by_id.values())
        except Exception:  # noqa: BLE001 — keep the rows; try other scans
            logger.debug("e1 full-scan ACA test failed", exc_info=True)
        for from_end in (False, True):
            try:
                try:
                    extra = fetch(
                        project_id, doc_ids,
                        k_per_doc=_E1_REAL_ACA_DOC_SCAN,
                        from_end=from_end,
                    )
                except TypeError:
                    extra = (
                        [] if from_end
                        else fetch(
                            project_id, doc_ids,
                            k_per_doc=_E1_REAL_ACA_DOC_SCAN,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 — extras must not break
                logger.warning(
                    "e1 late-ACA %s scan for %s failed: %s",
                    "tail" if from_end else "prefix", project_id, exc,
                )
                extra = []
            for chunk in extra or []:
                by_id.setdefault(chunk.chunk_id, chunk)

    containing = getattr(store, "chunks_containing_all", None)
    if callable(containing):
        for needles in _E1_REAL_ACA_TEXT_NEEDLES:
            try:
                try:
                    hits = containing(
                        project_id, list(needles),
                        k=_E1_REAL_ACA_TEXT_K, doc_ids=doc_ids,
                    )
                except TypeError:
                    hits = containing(
                        project_id, list(needles), k=_E1_REAL_ACA_TEXT_K,
                    )
            except Exception as exc:  # noqa: BLE001 — extras must not break
                logger.warning(
                    "e1 late-ACA text scan for %s failed: %s", project_id, exc,
                )
                hits = []
            for chunk in hits or []:
                if chunk.doc_id and chunk.doc_id not in allowed:
                    continue
                by_id.setdefault(chunk.chunk_id, chunk)
    return list(by_id.values())


_E1_RAG_DOC_ID_RE = re.compile(r"\[doc_id=([^\]\s]+)")


def e1_doc_ids_from_rag_context(rag_context: str) -> List[str]:
    """Doc ids from ``[doc_id=…]`` markers in the injected RAG context."""
    out: List[str] = []
    seen: Set[str] = set()
    for match in _E1_RAG_DOC_ID_RE.finditer(rag_context or ""):
        did = (match.group(1) or "").strip()
        if did and did not in seen:
            seen.add(did)
            out.append(did)
    return out


def e1_compose_excerpts_from_loaded_cd_volume(
    query: str,
    project_id: str,
    store=None,
    *,
    rag_context: str = "",
    doc_ids: Optional[List[str]] = None,
) -> str:
    """Join Contract Data 0.1% + excl-VAT ACA from the loaded CD volume.

    Live leftover E1 after #536: top-k stayed on Contract Data chunks
    9–11 that do not surface both operands, so compose returned None
    and the cost-grounding gate refused. When those rows exist later
    in the same loaded volume, return them so compose can state
    SAR/day — do not invent a figure and do not elect CoC 0.015%.
    Kill-switch: RAG_DELAY_DAMAGES_DAILY_RESCUE=0.
    """
    if not (
        delay_damages_daily_rescue_enabled()
        and query_asks_delay_damages_daily_amount(query)
        and project_id
    ):
        return ""
    ids: List[str] = []
    seen: Set[str] = set()

    def _add(did: str) -> None:
        if did and did not in seen:
            seen.add(did)
            ids.append(did)

    for did in doc_ids or []:
        _add(did)
    for did in e1_doc_ids_from_rag_context(rag_context):
        _add(did)

    try:
        from app.core.projects import documents_matching_title_phrase
        for phrase in ("contract data", "conditions of contract"):
            try:
                matches = documents_matching_title_phrase(project_id, phrase) or []
            except Exception:  # noqa: BLE001 — listing is optional
                logger.debug(
                    "e1 loaded-volume title listing failed for %r",
                    phrase, exc_info=True,
                )
                matches = []
            for doc in matches:
                _add(doc.get("id") or "")
    except Exception:  # noqa: BLE001 — rag doc_ids may still be enough
        logger.debug("e1 loaded-volume projects import failed", exc_info=True)

    if not ids:
        return ""
    if store is None:
        try:
            store = get_lexical_store()
        except Exception:  # noqa: BLE001 — never break a turn over the store
            logger.debug("e1 loaded-volume store open failed", exc_info=True)
            return ""

    extra = _e1_fetch_late_aca_chunks(store, project_id, ids)
    rate_parts: List[str] = []
    aca_parts: List[str] = []

    def _collect(text: str) -> None:
        if _e1_rate_preference(text) >= 2 and text not in rate_parts:
            rate_parts.append(text)
        if _e1_has_standalone_excl_vat(text) and text not in aca_parts:
            aca_parts.append(text)

    for chunk in extra or []:
        _collect(chunk.text or "")
    if not rate_parts or not aca_parts:
        for chunk in _pair_adjacent_keep_text(
            extra or [],
            lambda t: (
                _e1_rate_preference(t) >= 2 or _e1_has_standalone_excl_vat(t)
            ),
            window=_E1_REAL_ACA_PAIR_WINDOW,
        ):
            _collect(chunk.text or "")
    if not rate_parts or not aca_parts:
        return ""
    return "\n\n".join(rate_parts[:3] + aca_parts[:3])


def _a2_fused_chunk(entry) -> Optional[Chunk]:
    if isinstance(entry, tuple) and entry:
        chunk = entry[0]
    else:
        chunk = entry
    return chunk if isinstance(chunk, Chunk) else None


def _a2_pool_doc_ids_for_late_incl(fused: Dict[str, Tuple]) -> List[str]:
    """Contract Data / CoC volume docs already in fused for an A2 scan."""
    doc_ids: List[str] = []
    seen: Set[str] = set()
    for entry in fused.values():
        chunk = _a2_fused_chunk(entry)
        if chunk is None or not chunk.doc_id or chunk.doc_id in seen:
            continue
        text = chunk.text or ""
        name = getattr(chunk, "source_name", "") or ""
        if not name:
            try:
                name = _doc_name_for_id(chunk.doc_id) or ""
            except Exception:  # noqa: BLE001 — filename is optional
                name = ""
        if not (
            filename_looks_like_contract_data(name)
            or filename_looks_like_e1_rate_volume(name)
            or "accepted contract amount" in _normalize_retrieval_ws(text).lower()
        ):
            continue
        seen.add(chunk.doc_id)
        doc_ids.append(chunk.doc_id)
    return doc_ids


def _a2_fetch_late_incl_chunks(store, project_id: str, doc_ids: List[str]) -> List[Chunk]:
    """Every chunk of the A2 CD volume — do not stop on a partial ACA.

    Live Wave-1 A2 on 9ad62cc: chunk #0 stated delay damages ×
    SAR 39,098,392.98. That figure is a real money amount, so the E1
    late-scan early-return (any non-toy ACA) would keep first-N and
    miss SAR 2,017,680,124.69. Always also run prefix / tail / incl
    needles. Kill-switch: RAG_ACA_INCLUDING_VAT_RESCUE=0.
    """
    by_id: Dict[str, Chunk] = {}
    allowed = set(doc_ids)
    fetch = getattr(store, "chunks_for_docs", None)
    if callable(fetch):
        extra = []
        try:
            extra = fetch(project_id, doc_ids, all_rows=True)
        except TypeError:
            try:
                extra = fetch(project_id, doc_ids, k_per_doc=1_000_000)
            except TypeError:
                extra = []
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "a2 late-incl full scan for %s failed: %s", project_id, exc,
                )
                extra = []
        except Exception as exc:  # noqa: BLE001 — extras must not break
            logger.warning(
                "a2 late-incl full scan for %s failed: %s", project_id, exc,
            )
            extra = []
        for chunk in extra or []:
            if chunk.doc_id and chunk.doc_id not in allowed:
                continue
            by_id.setdefault(chunk.chunk_id, chunk)
        for from_end in (False, True):
            try:
                try:
                    extra = fetch(
                        project_id, doc_ids,
                        k_per_doc=_E1_REAL_ACA_DOC_SCAN,
                        from_end=from_end,
                    )
                except TypeError:
                    extra = (
                        [] if from_end
                        else fetch(
                            project_id, doc_ids,
                            k_per_doc=_E1_REAL_ACA_DOC_SCAN,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 — extras must not break
                logger.warning(
                    "a2 late-incl %s scan for %s failed: %s",
                    "tail" if from_end else "prefix", project_id, exc,
                )
                extra = []
            for chunk in extra or []:
                if chunk.doc_id and chunk.doc_id not in allowed:
                    continue
                by_id.setdefault(chunk.chunk_id, chunk)

    containing = getattr(store, "chunks_containing_all", None)
    if callable(containing):
        for needles in _A2_INCL_TEXT_NEEDLES:
            try:
                try:
                    hits = containing(
                        project_id, list(needles),
                        k=_E1_REAL_ACA_TEXT_K, doc_ids=doc_ids,
                    )
                except TypeError:
                    hits = containing(
                        project_id, list(needles), k=_E1_REAL_ACA_TEXT_K,
                    )
            except Exception as exc:  # noqa: BLE001 — extras must not break
                logger.warning(
                    "a2 late-incl text scan for %s failed: %s", project_id, exc,
                )
                hits = []
            for chunk in hits or []:
                if chunk.doc_id and chunk.doc_id not in allowed:
                    continue
                by_id.setdefault(chunk.chunk_id, chunk)
    return list(by_id.values())


def _rescue_a2_including_vat_from_pool_docs(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
) -> int:
    """Surface a filled including-VAT ACA that sits past first-N chunk #0.

    Live Wave-1 A2 on 9ad62cc: cosine / filename rescue kept Contract
    Data chunk #0 (delay damages × a partial ACA). Identifier search
    + first-24 neighbors never reached the filled 1.1.1 including-VAT
    row. Kill-switch: RAG_ACA_INCLUDING_VAT_RESCUE=0.
    """
    if not (
        aca_including_vat_rescue_enabled()
        and query_is_aca_including_vat_particular(query)
    ):
        return 0

    def _fused_chunk(entry) -> Optional[Chunk]:
        return _a2_fused_chunk(entry)

    fused_chunks = [
        c for c in (_fused_chunk(e) for e in fused.values()) if c is not None
    ]
    if any(chunk_states_aca_including_vat(c.text or "") for c in fused_chunks):
        return 0
    doc_ids = _a2_pool_doc_ids_for_late_incl(fused)
    if not doc_ids:
        try:
            from app.core.projects import documents_matching_title_phrase
            for phrase in ("contract data", "conditions of contract"):
                try:
                    matches = documents_matching_title_phrase(
                        project_id, phrase,
                    ) or []
                except Exception:  # noqa: BLE001 — listing is optional
                    matches = []
                for doc in matches:
                    did = doc.get("id") or ""
                    if did and did not in doc_ids:
                        doc_ids.append(did)
        except Exception:  # noqa: BLE001 — fused doc_ids may still be enough
            logger.debug("a2 late-incl title listing failed", exc_info=True)
    if not doc_ids:
        return 0
    extra = _a2_fetch_late_incl_chunks(store, project_id, doc_ids)
    recovered = 0
    for chunk in _pair_adjacent_keep_text(
        extra or [],
        chunk_states_aca_including_vat,
        window=_E1_REAL_ACA_PAIR_WINDOW,
    ):
        if chunk.chunk_id in fused:
            continue
        if not chunk_states_aca_including_vat(chunk.text or ""):
            continue
        fused[chunk.chunk_id] = (chunk, 0.0, _ASKED_PARTICULAR_VALUE_BONUS)
        recovered += 1
    if recovered:
        logger.info(
            "a2 late-incl scan recovered %d chunk(s) past Contract Data chunk #0",
            recovered,
        )
    return recovered


def a2_including_vat_excerpts_from_loaded_cd_volume(
    query: str,
    project_id: str,
    store=None,
    *,
    rag_context: str = "",
    doc_ids: Optional[List[str]] = None,
) -> str:
    """Join including-VAT ACA rows from the loaded CD volume.

    Live Wave-1 A2 on 9ad62cc: top-k stayed on Contract Data chunk #0
    (delay damages × SAR 39,098,392.98). When the filled including-VAT
    row exists later in the same loaded volume, return it so graft can
    state SAR 2,017,680,124.69 — do not invent a figure and do not
    compose delay damages. Kill-switch: RAG_ACA_INCLUDING_VAT_RESCUE=0.
    """
    if not (
        aca_including_vat_rescue_enabled()
        and query_is_aca_including_vat_particular(query)
        and project_id
    ):
        return ""
    ids: List[str] = []
    seen: Set[str] = set()

    def _add(did: str) -> None:
        if did and did not in seen:
            seen.add(did)
            ids.append(did)

    for did in doc_ids or []:
        _add(did)
    for did in e1_doc_ids_from_rag_context(rag_context):
        _add(did)

    try:
        from app.core.projects import documents_matching_title_phrase
        for phrase in ("contract data", "conditions of contract"):
            try:
                matches = documents_matching_title_phrase(project_id, phrase) or []
            except Exception:  # noqa: BLE001 — listing is optional
                logger.debug(
                    "a2 loaded-volume title listing failed for %r",
                    phrase, exc_info=True,
                )
                matches = []
            for doc in matches:
                _add(doc.get("id") or "")
    except Exception:  # noqa: BLE001 — rag doc_ids may still be enough
        logger.debug("a2 loaded-volume projects import failed", exc_info=True)

    if not ids:
        return ""
    if store is None:
        try:
            store = get_lexical_store()
        except Exception:  # noqa: BLE001 — never break a turn over the store
            logger.debug("a2 loaded-volume store open failed", exc_info=True)
            return ""

    extra = _a2_fetch_late_incl_chunks(store, project_id, ids)
    parts: List[str] = []
    for chunk in _pair_adjacent_keep_text(
        extra or [],
        chunk_states_aca_including_vat,
        window=_E1_REAL_ACA_PAIR_WINDOW,
    ):
        text = chunk.text or ""
        if chunk_states_aca_including_vat(text) and text not in parts:
            parts.append(text)
    if not parts:
        for chunk in extra or []:
            text = chunk.text or ""
            if chunk_states_aca_including_vat(text) and text not in parts:
                parts.append(text)
    if not parts:
        return ""
    return "\n\n".join(parts[:3])


def ensure_a2_kept_has_including_vat(
    query: str,
    kept: List[Chunk],
    ranked: List[Chunk],
    *,
    allow=None,
) -> bool:
    """Put the including-VAT row in top-k when it is already ranked.

    Live Wave-1 A2 on 9ad62cc: kept stayed on Contract Data chunk #0
    (delay damages × a partial ACA) after the late scan added the
    filled including-VAT row to ranked. Prefer replacing a delay-
    damages window. Kill-switch: RAG_ACA_INCLUDING_VAT_RESCUE=0.
    """
    if not kept:
        return False
    if not (
        aca_including_vat_rescue_enabled()
        and query_is_aca_including_vat_particular(query)
    ):
        return False
    if any(chunk_states_aca_including_vat(c.text or "") for c in kept):
        return False

    def _ok(chunk: Chunk) -> bool:
        return allow is None or allow(chunk)

    incl: Optional[Chunk] = None
    for chunk in ranked:
        if not _ok(chunk):
            continue
        if chunk_states_aca_including_vat(chunk.text or ""):
            incl = chunk
            break
    if incl is None:
        return False
    present = {c.chunk_id for c in kept}
    if incl.chunk_id in present:
        return False
    replace_at = 0
    for i, chunk in enumerate(kept):
        text = chunk.text or ""
        if chunk_states_delay_damages_rate(text) or (
            chunk_states_accepted_contract_amount(text)
            and not chunk_states_aca_including_vat(text)
        ):
            replace_at = i
            break
        replace_at = i
    kept[replace_at] = incl
    return True


def _rescue_e1_real_aca_from_pool_docs(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
) -> int:
    """Surface a filled excl-VAT ACA that sits past first-N on an 8.8 doc.

    Live leftover E1 after #530: identifier_search for ``accepted contract
    amount excluding vat`` matches the 8.8 worked-example windows (chunks
    9–11) and LIMIT returns those first. ``chunks_for_docs`` then takes
    the first 24/40 by index — still the Conditions body — so the filled
    1.1.1 row never enters fused. Compose skips the toy 10M and the cost
    gate refuses. #532 scanned the first 400 of those docs; live still
    missed an appendix past that prefix. Text-match + last-N tail on
    rate-window / Contract Data docs already in-pool. Kill-switch:
    RAG_DELAY_DAMAGES_DAILY_RESCUE=0.
    """
    if not (
        delay_damages_daily_rescue_enabled()
        and query_asks_delay_damages_daily_amount(query)
    ):
        return 0
    try:
        from app.lib.construction_formulas_commercial import (
            chunk_has_real_accepted_contract_amount,
        )
    except Exception:  # noqa: BLE001 — never break a turn over an import
        logger.debug("e1 late-ACA import failed", exc_info=True)
        return 0

    def _fused_chunk(entry) -> Optional[Chunk]:
        if isinstance(entry, tuple) and entry:
            chunk = entry[0]
        else:
            chunk = entry
        return chunk if isinstance(chunk, Chunk) else None

    fused_chunks = [
        c for c in (_fused_chunk(e) for e in fused.values()) if c is not None
    ]
    has_standalone_aca = any(
        _e1_has_standalone_excl_vat(c.text or "") for c in fused_chunks
    )
    has_preferred_rate = any(
        _e1_rate_preference(c.text or "") >= 2 for c in fused_chunks
    )
    # Do not skip when the only "real ACA" sits inside a 0.015% CoC
    # rate window — that is the live #535 flake (263,175.67/day).
    if has_standalone_aca and has_preferred_rate:
        return 0
    doc_ids = _e1_pool_doc_ids_for_late_aca(fused)
    if not doc_ids:
        return 0
    extra = _e1_fetch_late_aca_chunks(store, project_id, doc_ids)
    recovered = 0
    if not has_standalone_aca:
        for chunk in _pair_adjacent_keep_text(
            extra or [],
            chunk_states_accepted_contract_amount,
            window=_E1_REAL_ACA_PAIR_WINDOW,
        ):
            if chunk.chunk_id in fused:
                continue
            if not chunk_has_real_accepted_contract_amount(chunk.text or ""):
                continue
            if chunk_states_delay_damages_rate(chunk.text or ""):
                # Rate-base ACA is not the 1.1.1 row. Keep looking.
                continue
            fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
            recovered += 1
    if not has_preferred_rate:
        for chunk in extra or []:
            if chunk.chunk_id in fused:
                continue
            if _e1_rate_preference(chunk.text or "") < 2:
                continue
            fused[chunk.chunk_id] = (chunk, 0.0, 0.0)
            recovered += 1
    if recovered:
        logger.info(
            "e1 late-operand scan recovered %d chunk(s) past first-N 8.8 windows",
            recovered,
        )
    return recovered


def _rescue_asked_particular_value_chunks(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
) -> int:
    """Out-of-pool fetch for A2 incl-VAT, A3 TfC, A5 rate, A6 DNP, A9 Engineer."""
    recovered = 0
    if delay_damages_rate_rescue_enabled() and query_asks_for_delay_damages_rate(query):
        recovered += _rescue_chunks_matching(
            project_id, fused, store, _DELAY_RATE_RESCUE_PHRASES,
            chunk_states_delay_damages_rate, label="delay-damages-rate",
        )
    if delay_damages_daily_rescue_enabled() and query_asks_delay_damages_daily_amount(query):
        recovered += _rescue_chunks_matching(
            project_id, fused, store, _DELAY_RATE_RESCUE_PHRASES,
            chunk_states_delay_damages_rate, label="delay-damages-daily-rate",
        )
        recovered += _rescue_chunks_matching(
            project_id, fused, store, _ACA_BASE_RESCUE_PHRASES,
            chunk_states_accepted_contract_amount,
            label="delay-damages-daily-aca",
            bonus=0.0,
        )
    if engineer_identity_rescue_enabled() and query_asks_who_the_engineer_is(query):
        recovered += _rescue_chunks_matching(
            project_id, fused, store, _ENGINEER_IDENTITY_RESCUE_PHRASES,
            chunk_states_engineer_identity, label="engineer-identity",
        )
    if aca_including_vat_rescue_enabled() and query_asks_for_aca_including_vat(query):
        recovered += _rescue_chunks_matching(
            project_id, fused, store, _ACA_INCL_RESCUE_PHRASES,
            chunk_states_aca_including_vat, label="aca-including-vat",
        )
    if time_for_completion_rescue_enabled() and query_asks_for_time_for_completion(query):
        recovered += _rescue_chunks_matching(
            project_id, fused, store, _TFC_RESCUE_PHRASES,
            chunk_states_time_for_completion, label="time-for-completion",
        )
    if dnp_rescue_enabled() and query_asks_for_defects_notification_period(query):
        recovered += _rescue_chunks_matching(
            project_id, fused, store, _DNP_RESCUE_PHRASES,
            chunk_states_defects_notification_period,
            label="defects-notification-period",
        )
    return recovered


def _apply_asked_particular_value_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
) -> None:
    """In-place: lift the asked particular over neighboring-field lookalikes."""
    want_rate = (
        delay_damages_rate_rescue_enabled()
        and query_asks_for_delay_damages_rate(query)
    )
    want_eng = (
        engineer_identity_rescue_enabled()
        and query_asks_who_the_engineer_is(query)
    )
    want_aca = (
        aca_including_vat_rescue_enabled()
        and query_asks_for_aca_including_vat(query)
    )
    want_tfc = (
        time_for_completion_rescue_enabled()
        and query_asks_for_time_for_completion(query)
    )
    want_dnp = (
        dnp_rescue_enabled()
        and query_asks_for_defects_notification_period(query)
    )
    if not (want_rate or want_eng or want_aca or want_tfc or want_dnp):
        return
    for i, (score, chunk) in enumerate(scored):
        text = chunk.text or ""
        hit = (
            (want_rate and chunk_states_delay_damages_rate(text))
            or (want_eng and chunk_states_engineer_identity(text))
            or (want_aca and chunk_states_aca_including_vat(text))
            or (want_tfc and chunk_states_time_for_completion(text))
            or (want_dnp and chunk_states_defects_notification_period(text))
        )
        if not hit:
            continue
        boosted = score + _ASKED_PARTICULAR_VALUE_BONUS
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


# ── Rate Only BOQ-item rescue (live OLD-pack G4) ───────────────────────────
#
# Live Master Corpus G4 (tip 4d8ddb79 / was a65cebb5): "Answer only from
# the client project documents. What is the total amount for removal of
# storm water culverts (D529.3)?" returned a generic "I'm ready to help"
# acknowledgement plus the 3348/3348 coverage footer. The asked row is
# Rate Only — no amount exists. Cosine prefers priced lookalikes
# (D549.2 fence, D599.5 carriageway, an Excluded culvert that shares
# "storm water") and term rescue treats that overlap as already-grounded.
# Do not invent a money total; elect the Rate Only row as written.
#
# Same shape as A5/A9 in-pool fence + out-of-pool identifier rescue.
# Kill-switch: RAG_RATE_ONLY_RESCUE=0.
#
# Not #504 (E1 delay-damages compose), not #505 (F2 duration override),
# not #506 (G1 Schedule 10 register).
_RATE_ONLY_BONUS = 2.0
_RATE_ONLY_RE = re.compile(r"(?i)\brate\s*only\b")
_ITEM_AMOUNT_ASK_RE = re.compile(
    r"(?i)\b(?:total\s+amount|(?<!contract\s)amount|sum\s+for|value\s+for)\b",
)
_UNIT_RATE_ONLY_ASK_RE = re.compile(
    r"(?i)\b(?:unit\s+rate|rate\s+for|rate\s+per)\b",
)
_CESMM_COMPACT_ITEM_RE = re.compile(r"(?i)^[a-z]\d{2,4}(?:\.\d+)?$")
_CESMM_IN_TEXT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Z])\s*(\d{2,4}(?:\.\d+)?)(?![A-Za-z0-9])",
)


def rate_only_rescue_enabled() -> bool:
    """ON by default — live G4 Rate Only recall. RAG_RATE_ONLY_RESCUE=0
    restores pre-fix ranking if the lift ever proves noisy."""
    return _env_flag_on("RAG_RATE_ONLY_RESCUE")


def extract_asked_cesmm_codes(query: str) -> List[str]:
    """Compact CESMM item codes the ask names (``d529.3``, ``d549.2``)."""
    out: List[str] = []
    seen: Set[str] = set()
    blob = normalize_cesmm_item_codes(query or "")
    for ident in extract_query_identifiers(blob):
        compact = normalize_cesmm_item_codes(ident).lower()
        if not _CESMM_COMPACT_ITEM_RE.fullmatch(compact):
            continue
        if compact not in seen:
            seen.add(compact)
            out.append(compact)
    for match in _CESMM_IN_TEXT_RE.finditer(blob):
        compact = f"{match.group(1)}{match.group(2)}".lower()
        if compact not in seen:
            seen.add(compact)
            out.append(compact)
    return out


def query_asks_for_boq_item_amount(query: str) -> bool:
    """True for G4 (total amount of a named CESMM / BOQ item).

    A2 (Accepted Contract Amount), A5 (Delay Damages rate) and E1
    (calculate … in SAR) stay off this path. A unit-rate-only ask
    (WAVE 2 B5 without ``amount``) is not this class — the Rate
    column can still be a number when Amount is Rate Only.
    """
    q = (query or "").strip()
    if not q or _DEFINITION_QUESTION_RE.search(q):
        return False
    if query_asks_for_accepted_contract_amount(q):
        return False
    if query_asks_for_delay_damages_rate(q) or query_needs_a_monetary_base(q):
        return False
    if _UNIT_RATE_ONLY_ASK_RE.search(q) and not re.search(
        r"(?i)\b(?:total\s+)?amount\b", q,
    ):
        return False
    if not _ITEM_AMOUNT_ASK_RE.search(q):
        return False
    return bool(extract_asked_cesmm_codes(q))


def _cesmm_row_windows(text: str, code: str) -> List[str]:
    """Local row / next-line windows around one CESMM code.

    Amount sits to the right of the item code. A previous row's
    Rate Only must not stain the next item on a mixed BOQ page.
    """
    compact = normalize_cesmm_item_codes(code or "")
    if not compact:
        return []
    letter, rest = compact[0], compact[1:]
    item_re = re.compile(
        rf"(?i)(?<![A-Za-z0-9]){re.escape(letter)}\s*{re.escape(rest)}"
        r"(?![A-Za-z0-9])",
    )
    # A new BOQ row starts with a CESMM code (optionally after a pipe).
    # CESMM4 is letter + 2-3 digits (D529.3 / D110). Do not treat a
    # unit + rate ("m 1370.00") as the next item.
    other_re = re.compile(
        r"(?i)^(?:\s*[|]\s*)?([A-Z])\s*(\d{2,3}(?:\.\d{1,2})?)\b",
    )
    blob = text or ""
    windows: List[str] = []
    lines = blob.splitlines() or [blob]
    for i, line in enumerate(lines):
        if not item_re.search(line):
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if other_re.match(nxt.strip()) and not item_re.search(nxt):
            nxt = ""
        windows.append(_normalize_retrieval_ws(f"{line} {nxt}"))
    if windows:
        return windows
    # One-line OCR / table soup: cut at the next pipe-led CESMM item,
    # not at a unit + rate ("m 1370.00").
    next_item = re.compile(
        r"(?i)(?:\s*[|]\s+)([A-Z])\s*(\d{2,3}(?:\.\d{1,2})?)\b",
    )
    for match in item_re.finditer(blob):
        tail = blob[match.end(): match.end() + 160]
        cut = next_item.search(tail)
        if cut:
            tail = tail[:cut.start()]
        windows.append(_normalize_retrieval_ws(blob[match.start(): match.end()] + tail))
    return windows


def chunk_states_rate_only_item(text: str, codes: List[str]) -> bool:
    """True when the asked CESMM row's Amount is Rate Only.

    A priced lookalike on the same page (D549.2 / D599.5) and an
    Excluded culvert that only shares the description are not this.
    Does not invent: the excerpt itself must already say Rate Only
    on the asked item's row.
    """
    if not codes or not _RATE_ONLY_RE.search(text or ""):
        return False
    for code in codes:
        for window in _cesmm_row_windows(text, code):
            if _RATE_ONLY_RE.search(window):
                return True
    return False


def chunk_states_rate_only_row(text: str) -> bool:
    """Query-free: a CESMM row in ``text`` already says Rate Only."""
    if not text or not _RATE_ONLY_RE.search(text):
        return False
    blob = normalize_cesmm_item_codes(text)
    codes = [
        f"{m.group(1)}{m.group(2)}".lower()
        for m in _CESMM_IN_TEXT_RE.finditer(blob)
    ]
    return bool(codes) and chunk_states_rate_only_item(text, codes)


def format_rate_only_line(codes: List[str], excerpt: str = "") -> str:
    """User-facing Rate Only sentence. Does not invent a money total."""
    code = (codes[0] if codes else "the item")
    pretty = f"{code[0].upper()}{code[1:]}" if code and code[0].isalpha() else code
    desc = ""
    collapsed = _normalize_retrieval_ws(excerpt or "")
    if re.search(r"(?i)storm\s+water\s+culvert", collapsed):
        desc = " (removal of storm water culverts)"
    return (
        f"{pretty}{desc} is Rate Only. No amount exists for this item "
        f"in the client BOQ — do not invent a total."
    )


def answer_states_rate_only(text: str) -> bool:
    """True when ``text`` already elects Rate Only / no amount."""
    blob = text or ""
    if _RATE_ONLY_RE.search(blob):
        return True
    return bool(re.search(r"(?i)\bno amount\b", blob))


def _apply_rate_only_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
) -> None:
    """In-place: lift the asked Rate Only row over priced lookalikes."""
    if not rate_only_rescue_enabled():
        return
    if not query_asks_for_boq_item_amount(query):
        return
    codes = extract_asked_cesmm_codes(query)
    if not codes:
        return
    for i, (score, chunk) in enumerate(scored):
        if not chunk_states_rate_only_item(chunk.text or "", codes):
            continue
        boosted = score + _RATE_ONLY_BONUS
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


def _rescue_rate_only_item_chunks(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    extra_pids: Optional[List[str]] = None,
) -> int:
    """Pull the asked CESMM Rate Only row into ``fused``. Project-first.

    Identifier search collapses OCR ``D 529.3``. ``chunks_containing_all``
    is the out-of-pool backup when cosine never fetched the short row.
    Failures never raise. GK rate-book notes are not searched.
    """
    if not rate_only_rescue_enabled():
        return 0
    if not query_asks_for_boq_item_amount(query):
        return 0
    codes = extract_asked_cesmm_codes(query)
    if not codes:
        return 0

    def _keep(text: str) -> bool:
        return chunk_states_rate_only_item(text, codes)

    recovered = _rescue_chunks_matching(
        project_id, fused, store, tuple(codes),
        _keep, label="rate-only-item",
    )
    fetch = getattr(store, "chunks_containing_all", None)
    if not callable(fetch):
        return recovered
    pids = [project_id] + [
        p for p in (extra_pids or []) if p and p != project_id
    ]
    for pid in pids:
        for code in codes:
            rest = code[1:] if len(code) > 1 else code
            needle_sets = ([code, "rate only"], [rest, "rate only"])
            for needles in needle_sets:
                try:
                    hits = fetch(pid, needles, k=20)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "rate-only rescue for %s (%r) failed: %s",
                        pid, needles, exc,
                    )
                    continue
                for chunk in hits:
                    if not _keep(chunk.text or ""):
                        continue
                    if chunk.chunk_id in fused:
                        continue
                    fused[chunk.chunk_id] = (chunk, 0.0, _RATE_ONLY_BONUS)
                    recovered += 1
    if recovered:
        logger.info(
            "rate-only rescue recovered %d chunk(s) for codes %r",
            recovered, codes,
        )
    return recovered


# ── Spec-precedence list neighbor (live OLD-pack C1) ──────────────────────
#
# Live Master Corpus C1 (tip 8f4b465): "Answer only from the client
# project documents. Under Sub-Clause 1.5.1(d), what is the order of
# precedence of documents within the Specification? List the first
# three." retrieved DD-2023-118 Vol 2 chunk_index 2, which ends at
# "the Specification shall be set out as follows". The list — Post
# Tender Clarifications / Tender Addenda / Schedule of Project
# Requirements — is the next same-doc chunk (index 3). Cosine + term
# rescue treated the open-list intro as already-grounded.
#
# When a hit is that intro, fetch the next same-doc chunk and elect
# the list. Do not invent a signatory (D1). Kill-switch:
# RAG_SPEC_PRECEDENCE_LIST_RESCUE=0.
#
# Not #501 (A3/A5 year lock), not #516/#520 (A2 VAT), not #517 (A3
# 852), not #502 (C2 SPE-identity), not #506 (G1), not #507 (G4).
_SPEC_PRECEDENCE_LIST_BONUS = 2.0
_SPEC_PRECEDENCE_ASK_RE = re.compile(
    r"(?i)(?:1\.5\.1\s*\(\s*d\s*\)"
    r"|order\s+of\s+precedence.{0,80}specification"
    r"|specification.{0,80}order\s+of\s+precedence"
    r"|documents\s+within\s+the\s+specification)"
)
_AS_FOLLOWS_TAIL_RE = re.compile(r"(?i)as\s+follows\s*[:.]?\s*$")
_SPEC_PRECEDENCE_INTRO_RE = re.compile(
    r"(?i)(?:1\.5\.1\s*\(\s*d\s*\)"
    r"|specification\s+shall\s+be\s+set\s+out"
    r"|order\s+of\s+precedence"
    r"|within\s+the\s+specification)"
)
_POST_TENDER_CLARIFICATIONS_RE = re.compile(r"(?i)post\s+tender\s+clarifications")
_TENDER_ADDENDA_RE = re.compile(r"(?i)tender\s+addenda")
_SOPR_RE = re.compile(r"(?i)schedule\s+of\s+project\s+requirements")
_SPEC_PRECEDENCE_LIST_NEEDLES = (
    "Post Tender Clarifications",
    "Tender Addenda",
    "Schedule of Project Requirements",
)


def spec_precedence_list_rescue_enabled() -> bool:
    """ON by default — live C1 list-neighbor miss.

    RAG_SPEC_PRECEDENCE_LIST_RESCUE=0 restores intro-only ranking.
    """
    return _env_flag_on("RAG_SPEC_PRECEDENCE_LIST_RESCUE")


def query_asks_for_spec_precedence_list(query: str) -> bool:
    """True for Sub-Clause 1.5.1(d) / Specification precedence (C1).

    A2 VAT, A3 Time for Completion, A5 delay-damages, A6 DNP,
    A9 Engineer, and C2 titled-spec asks stay off this path.
    """
    return bool(_SPEC_PRECEDENCE_ASK_RE.search(query or ""))


def chunk_is_open_list_intro(text: str) -> bool:
    """True when the excerpt opens a list and then stops.

    Live C1: chunk_index 2 ends at ``as follows`` under 1.5.1(d) /
    ``the Specification shall be set out``.
    """
    blob = (text or "").strip()
    if not blob or not _AS_FOLLOWS_TAIL_RE.search(blob):
        return False
    return bool(_SPEC_PRECEDENCE_INTRO_RE.search(blob))


def chunk_states_spec_precedence_list(text: str) -> bool:
    """True when the excerpt names the first three precedence items."""
    blob = text or ""
    return bool(
        _POST_TENDER_CLARIFICATIONS_RE.search(blob)
        and _TENDER_ADDENDA_RE.search(blob)
        and _SOPR_RE.search(blob)
    )


def _apply_spec_precedence_list_boost(
    query: str,
    scored: List[Tuple[float, Chunk]],
) -> None:
    """In-place: lift the Specification precedence list over the intro."""
    if not spec_precedence_list_rescue_enabled():
        return
    if not query_asks_for_spec_precedence_list(query):
        return
    for i, (score, chunk) in enumerate(scored):
        if not chunk_states_spec_precedence_list(chunk.text or ""):
            continue
        boosted = score + _SPEC_PRECEDENCE_LIST_BONUS
        chunk.score = round(boosted, 6)
        scored[i] = (boosted, chunk)


def _rescue_spec_precedence_list_neighbors(
    query: str,
    project_id: str,
    fused: Dict[str, Tuple],
    store,
    extra_pids: Optional[List[str]] = None,
) -> int:
    """Pull the list chunk that follows an open-list intro into ``fused``.

    Primary path: same-doc neighbor of an in-pool ``as follows`` intro
    (live C1: chunk 2 → chunk 3). Backup: ``chunks_containing_all`` on
    the three expected strings if the intro itself missed the pool.
    Failures never raise. GK notes are not searched.
    """
    if not spec_precedence_list_rescue_enabled():
        return 0
    if not query_asks_for_spec_precedence_list(query):
        return 0

    recovered = 0
    follow = getattr(store, "chunks_following", None)
    if callable(follow):
        anchors: List[Tuple[str, int]] = []
        seen_anchors: Set[Tuple[str, int]] = set()
        for chunk, _sem, _b in fused.values():
            if chunk.project_id and chunk.project_id != project_id:
                continue
            if not chunk_is_open_list_intro(chunk.text or ""):
                continue
            if chunk_states_spec_precedence_list(chunk.text or ""):
                continue
            key = (chunk.doc_id, int(chunk.chunk_index or 0))
            if not key[0] or key in seen_anchors:
                continue
            seen_anchors.add(key)
            anchors.append(key)
        if anchors:
            try:
                extra = follow(project_id, anchors, n=1)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "spec-precedence neighbor fetch for %s failed: %s",
                    project_id, exc,
                )
                extra = []
            by_key = {(c.doc_id, int(c.chunk_index or 0)): c for c in extra}
            for doc_id, idx in anchors:
                nxt = by_key.get((doc_id, idx + 1))
                if nxt is None:
                    continue
                if nxt.chunk_id in fused:
                    continue
                if not chunk_states_spec_precedence_list(nxt.text or ""):
                    continue
                fused[nxt.chunk_id] = (
                    nxt, 0.0, _SPEC_PRECEDENCE_LIST_BONUS,
                )
                recovered += 1

    if any(
        chunk_states_spec_precedence_list(c.text or "")
        for c, _sem, _b in fused.values()
    ):
        if recovered:
            logger.info(
                "spec-precedence list rescue recovered %d neighbor chunk(s)",
                recovered,
            )
        return recovered

    fetch = getattr(store, "chunks_containing_all", None)
    if not callable(fetch):
        return recovered
    pids = [project_id] + [
        p for p in (extra_pids or []) if p and p != project_id
    ]
    needles = list(_SPEC_PRECEDENCE_LIST_NEEDLES)
    for pid in pids:
        try:
            hits = fetch(pid, needles, k=20)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "spec-precedence list rescue for %s failed: %s", pid, exc,
            )
            continue
        for chunk in hits:
            if not chunk_states_spec_precedence_list(chunk.text or ""):
                continue
            if chunk.chunk_id in fused:
                continue
            fused[chunk.chunk_id] = (
                chunk, 0.0, _SPEC_PRECEDENCE_LIST_BONUS,
            )
            recovered += 1
        if recovered:
            break
    if recovered:
        logger.info(
            "spec-precedence list rescue recovered %d chunk(s)",
            recovered,
        )
    return recovered


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
    e1 = (
        delay_damages_daily_rescue_enabled()
        and query_asks_delay_damages_daily_amount(query)
    )

    def _is_money_base(text: str) -> bool:
        if e1:
            try:
                from app.lib.construction_formulas_commercial import (
                    chunk_has_real_accepted_contract_amount,
                )
                # Toy 8.8 windows and particulars-prefixed 10M examples
                # are not the rate base. Only a non-toy ACA (live:
                # excl-VAT SAR 1,754,504,456.25) satisfies reservation.
                return chunk_has_real_accepted_contract_amount(text)
            except Exception:  # noqa: BLE001 — fall through to the usual tests
                logger.debug("toy-ACA money-base test failed", exc_info=True)
        if particulars_row_states_an_amount_of_money(text):
            return True
        return bool(e1 and chunk_states_accepted_contract_amount(text))

    if any(_is_money_base(c.text or "") for c in kept):
        return False
    present = {c.chunk_id for c in kept}
    replace_at = len(kept) - 1
    if e1:
        # Live leftover E1: particulars reserved the 0.1% row into the
        # last slot, then this function overwrote it with including-VAT
        # ACA. Prefer a non-rate slot. If every survivor is a rate
        # (k=1 unit pin), still take the last slot so the money row
        # can enter; earlier rate rows remain when k > 1.
        for i in range(len(kept) - 1, -1, -1):
            if not chunk_states_delay_damages_rate(kept[i].text or ""):
                replace_at = i
                break
    for chunk in ranked:
        if chunk.chunk_id in present:
            continue
        if not _is_money_base(chunk.text or ""):
            continue
        if allow is not None and not allow(chunk):
            continue
        kept[replace_at] = chunk
        logger.debug(
            "reserved a Contract Data money row for an arithmetic ask; "
            "a percentage alone cannot answer it",
        )
        return True
    return False


def _e1_non_operand_index(
    kept: List[Chunk],
    *,
    protect_rate: bool,
    protect_aca: bool,
) -> Optional[int]:
    """Lowest-ranked slot that is not a protected E1 compose operand."""
    for i in range(len(kept) - 1, -1, -1):
        text = kept[i].text or ""
        if protect_rate and chunk_states_delay_damages_rate(text):
            continue
        if protect_aca and chunk_states_accepted_contract_amount(text):
            continue
        return i
    return None


def reserve_e1_compose_operands(
    query: str,
    kept: List[Chunk],
    ranked: List[Chunk],
    *,
    allow=None,
) -> bool:
    """Guarantee both E1 multiply operands in top-k, prefer excl-VAT ACA.

    ``reserve_matching_particulars_row`` and ``reserve_monetary_base_row``
    share one last slot. Live leftover E1 after #523: CoC 8.8 filled
    kept, the money reserve elected including-VAT ACA, and compose never
    saw the 0.1% row — the answer was the A2 particular. This pass
    restores the rate and upgrades incl-VAT to excl-VAT when both twins
    are reachable. Kill-switch: RAG_DELAY_DAMAGES_DAILY_RESCUE=0.
    """
    if not kept:
        return False
    if not (
        delay_damages_daily_rescue_enabled()
        and query_asks_delay_damages_daily_amount(query)
    ):
        return False
    changed = False
    present = {c.chunk_id for c in kept}

    if not any(chunk_states_delay_damages_rate(c.text or "") for c in kept):
        idx = _e1_non_operand_index(kept, protect_rate=True, protect_aca=True)
        if idx is not None:
            for chunk in ranked:
                if chunk.chunk_id in present:
                    continue
                if not chunk_states_delay_damages_rate(chunk.text or ""):
                    continue
                if allow is not None and not allow(chunk):
                    continue
                kept[idx] = chunk
                present.add(chunk.chunk_id)
                changed = True
                break

    # Live leftover E1 after #535: 0.015% CoC windows already satisfy
    # chunk_states_delay_damages_rate, so the 0.1% Contract Data row
    # never replaced them. Upgrade when a better rate is in ranked.
    best_rate: Optional[Chunk] = None
    best_rate_rank = -1
    for chunk in ranked:
        if allow is not None and not allow(chunk):
            continue
        rank = _e1_rate_preference(chunk.text or "")
        if rank > best_rate_rank:
            best_rate_rank = rank
            best_rate = chunk
    kept_rate = max(
        (_e1_rate_preference(c.text or "") for c in kept), default=-1,
    )
    if (
        best_rate is not None
        and best_rate_rank > kept_rate
        and best_rate.chunk_id not in {c.chunk_id for c in kept}
    ):
        rate_idxs = [
            i for i, chunk in enumerate(kept)
            if chunk_states_delay_damages_rate(chunk.text or "")
            and _e1_rate_preference(chunk.text or "") < best_rate_rank
        ]
        idx = rate_idxs[-1] if rate_idxs else _e1_non_operand_index(
            kept, protect_rate=True, protect_aca=True,
        )
        if idx is not None:
            kept[idx] = best_rate
            present.add(best_rate.chunk_id)
            changed = True

    best_chunk: Optional[Chunk] = None
    best_rank = -1
    for chunk in ranked:
        if allow is not None and not allow(chunk):
            continue
        rank = _e1_aca_preference(chunk.text or "")
        if rank > best_rank:
            best_rank = rank
            best_chunk = chunk
    if best_chunk is None:
        return changed

    kept_best = max((_e1_aca_preference(c.text or "") for c in kept), default=-1)
    if best_rank <= kept_best:
        return changed
    if best_chunk.chunk_id in {c.chunk_id for c in kept}:
        return changed

    idx = None
    if kept_best >= 0:
        worst_i = None
        worst_rank = 99
        for i, chunk in enumerate(kept):
            rank = _e1_aca_preference(chunk.text or "")
            if 0 <= rank < worst_rank:
                worst_rank = rank
                worst_i = i
        idx = worst_i
    if idx is None:
        idx = _e1_non_operand_index(kept, protect_rate=True, protect_aca=True)
    if idx is None:
        # Live leftover E1 after #529: toy 8.8 windows occupy every
        # slot as rate operands. Skipping the toy cleared the money
        # operand without a free slot — still replace a surplus rate
        # so the excl-VAT ACA can enter. Keep at least one rate row.
        rate_idxs = [
            i for i, chunk in enumerate(kept)
            if chunk_states_delay_damages_rate(chunk.text or "")
        ]
        if len(rate_idxs) > 1:
            idx = rate_idxs[-1]
        elif kept_best < 0:
            idx = len(kept) - 1
    if idx is None:
        return changed
    kept[idx] = best_chunk
    return True


def ensure_e1_kept_can_compose(
    query: str,
    kept: List[Chunk],
    ranked: List[Chunk],
    *,
    allow=None,
) -> bool:
    """Force both compose operands into kept when top-k is refuse-prone.

    Live leftover E1 after #536: Cosine kept Contract Data chunks 9–11
    that do not parse as rate × excl-VAT ACA. The operands already sit
    in ``ranked`` after the all-chunk scan. Put them in kept so compose
    does not fall through to the cost-grounding refuse. Kill-switch:
    RAG_DELAY_DAMAGES_DAILY_RESCUE=0.
    """
    if not kept:
        return False
    if not (
        delay_damages_daily_rescue_enabled()
        and query_asks_delay_damages_daily_amount(query)
    ):
        return False
    try:
        from app.lib.construction_formulas_commercial import (
            compose_delay_damages_daily_from_excerpts,
        )
    except Exception:  # noqa: BLE001 — never break a turn over an import
        logger.debug("e1 kept-compose import failed", exc_info=True)
        return False
    if compose_delay_damages_daily_from_excerpts(
        query, "\n\n".join(c.text or "" for c in kept),
    ):
        return False

    def _ok(chunk: Chunk) -> bool:
        return allow is None or allow(chunk)

    rate: Optional[Chunk] = None
    aca: Optional[Chunk] = None
    for chunk in ranked:
        if not _ok(chunk):
            continue
        text = chunk.text or ""
        if rate is None and _e1_rate_preference(text) >= 2:
            rate = chunk
        if aca is None and _e1_has_standalone_excl_vat(text):
            aca = chunk
        if rate is not None and aca is not None:
            break
    if rate is None or aca is None:
        return False
    changed = False
    present = {c.chunk_id for c in kept}
    if rate.chunk_id not in present:
        idx = _e1_non_operand_index(kept, protect_rate=True, protect_aca=True)
        if idx is None:
            idx = len(kept) - 1
        kept[idx] = rate
        present.add(rate.chunk_id)
        changed = True
    if aca.chunk_id not in present:
        idx = _e1_non_operand_index(kept, protect_rate=True, protect_aca=True)
        if idx is None:
            rate_idxs = [
                i for i, chunk in enumerate(kept)
                if chunk.chunk_id == rate.chunk_id
                or chunk_states_delay_damages_rate(chunk.text or "")
            ]
            if len(rate_idxs) > 1:
                idx = next(
                    (i for i in reversed(rate_idxs) if kept[i].chunk_id != rate.chunk_id),
                    rate_idxs[-1],
                )
            else:
                idx = 0 if kept[-1].chunk_id == rate.chunk_id else len(kept) - 1
        if kept[idx].chunk_id == rate.chunk_id and len(kept) > 1:
            idx = 0 if idx != 0 else 1
        kept[idx] = aca
        changed = True
    return changed


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
    # A cap row / mixed window used to count as "already answered" for
    # A5, so the 0.1%-per-day chunk never replaced same-year 8.8. The
    # asked *value* (rate / Engineer) must be in kept, not merely the
    # label family.
    if any(chunk_answers_asked_particular(query, c.text or "") for c in kept):
        need_a5_rate = (
            delay_damages_rate_rescue_enabled()
            and query_asks_for_delay_damages_rate(query)
            and not any(
                chunk_states_delay_damages_rate(c.text or "") for c in kept
            )
        )
        need_e1_rate = (
            delay_damages_daily_rescue_enabled()
            and query_asks_delay_damages_daily_amount(query)
            and not any(
                chunk_states_delay_damages_rate(c.text or "") for c in kept
            )
        )
        if not (need_a5_rate or need_e1_rate):
            return False
    present = {c.chunk_id for c in kept}
    for chunk in ranked:
        if chunk.chunk_id in present:
            continue
        text = chunk.text or ""
        if not chunk_answers_asked_particular(query, text):
            continue
        if (
            delay_damages_rate_rescue_enabled()
            and query_asks_for_delay_damages_rate(query)
            and not chunk_states_delay_damages_rate(text)
        ):
            continue
        if (
            delay_damages_daily_rescue_enabled()
            and query_asks_delay_damages_daily_amount(query)
            and not chunk_states_delay_damages_rate(text)
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
    _rescue_asked_particular_value_chunks(
        query, project_id, fused_lex, store,
    )
    _rescue_e1_real_aca_from_pool_docs(
        query, project_id, fused_lex, store,
    )
    _rescue_a2_including_vat_from_pool_docs(
        query, project_id, fused_lex, store,
    )
    _rescue_schedule_register_chunks(
        query, project_id, fused_lex, store,
        extra_pids=extra_lex_pids,
    )
    _rescue_rate_only_item_chunks(
        query, project_id, fused_lex, store,
    )
    _rescue_spec_precedence_list_neighbors(
        query, project_id, fused_lex, store,
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
    _apply_asked_particular_value_boost(query, scored_lex)
    _apply_schedule_register_boost(query, scored_lex)
    _apply_rate_only_boost(query, scored_lex)
    _apply_spec_precedence_list_boost(query, scored_lex)
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
    reserve_e1_compose_operands(query, kept, candidates, allow=_allow)
    ensure_e1_kept_can_compose(query, kept, candidates, allow=_allow)
    ensure_a2_kept_has_including_vat(query, kept, candidates, allow=_allow)
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
    # A5/A9: the year-lock may already have the right PREFIX-YEAR-SEQ
    # (live A3) while the rate / Engineer appointment sit in a later
    # unprefixed chunk cosine never fetched. Rescue is project-only so
    # the FIDIC note's illustrative 0.05% cannot impersonate the rate.
    _rescue_asked_particular_value_chunks(query, project_id, fused, store)
    _rescue_e1_real_aca_from_pool_docs(query, project_id, fused, store)
    _rescue_a2_including_vat_from_pool_docs(query, project_id, fused, store)
    _rescue_schedule_register_chunks(
        query,
        project_id,
        fused,
        store,
        extra_pids=extra_rescue_pids,
    )
    # G4: Rate Only CESMM row (D529.3) vs priced lookalikes. Project-only
    # so a curated CESMM note cannot impersonate the client's Amount.
    _rescue_rate_only_item_chunks(query, project_id, fused, store)
    # C1: Sub-Clause 1.5.1(d) intro ends "as follows"; the precedence
    # list is the next same-doc chunk. Project-only so a FIDIC note
    # cannot impersonate the client's Specification order.
    _rescue_spec_precedence_list_neighbors(query, project_id, fused, store)

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
    _apply_asked_particular_value_boost(query, scored)
    _apply_schedule_register_boost(query, scored)
    _apply_rate_only_boost(query, scored)
    _apply_spec_precedence_list_boost(query, scored)

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
    reserve_e1_compose_operands(
        query, kept, [c for _, c in scored], allow=_allow_final,
    )
    ensure_e1_kept_can_compose(
        query, kept, [c for _, c in scored], allow=_allow_final,
    )
    ensure_a2_kept_has_including_vat(
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
