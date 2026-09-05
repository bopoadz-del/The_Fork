"""Shared helpers for RAG injection: token cap, chunk formatter, the
main ``rag_inject`` entry point used by the agent runtime and the
chat block.

Kept in its own module so:
* Phase 2's runtime change is small and confined to a hook call.
* Tests can drive the helpers directly without spinning up an agent.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.rag.vector_store import Chunk

_LOG = logging.getLogger(__name__)


# A marker is repeated per excerpt, so a long engineering filename
# ("IP-INF-053-0000-JCB-SPC-IF-000013-B_SOPR.pdf") would cost real budget
# on every one. Long enough to keep a trailing "(exp. 17Jul25).pdf".
_MAX_SOURCE_NAME_CHARS = 60


def _truncate_source_name(name: str) -> str:
    """Fit ``name`` into the per-excerpt src= budget.

    The tail is kept (expiry dates / revision letters live there). When
    the filename also carries a PREFIX-YEAR-SEQ contract id, that id is
    preserved at the front so a contract answer can name the cited
    contract — tail-only truncation used to drop ``DD-2023-118`` from
    long Infrastructure Package filenames.
    """
    if not name:
        return ""
    if len(name) <= _MAX_SOURCE_NAME_CHARS:
        return name
    from app.core.rag.retriever import extract_contract_doc_ids
    ids = extract_contract_doc_ids(name)
    if not ids:
        return "…" + name[-(_MAX_SOURCE_NAME_CHARS - 1):]
    name_l = name.lower()
    prefix_parts: List[str] = []
    for cid in ids:
        idx = name_l.find(cid)
        if idx >= 0:
            prefix_parts.append(name[idx:idx + len(cid)])
    prefix = " ".join(prefix_parts) if prefix_parts else ids[0]
    ellip = "…"
    budget = _MAX_SOURCE_NAME_CHARS - len(prefix) - len(ellip)
    if budget < 8:
        return prefix[:_MAX_SOURCE_NAME_CHARS]
    return prefix + ellip + name[-budget:]


def _estimate_tokens(text: str) -> int:
    """Cheap proxy: 4 chars per token. Good enough for the cap; not
    used for billing or model context sizing."""
    return max(1, len(text) // 4)


def apply_token_cap(chunks: List[Chunk]) -> Tuple[List[Chunk], int]:
    """Drop whole chunks from the bottom (lowest score) until total
    estimated tokens are <= MAX_RAG_TOKENS.

    Never truncates mid-chunk; a chunk is included or excluded whole.
    Returns ``(kept_chunks, total_estimated_tokens)``.
    """
    # Default sized for the CURRENT chunker output. Live failure 2026-08-15
    # (F20): doc-reindex emits ~3,000-char chunks (~750-950 est. tokens), so
    # the old 1500 default -- calibrated for the 512-char chunker era -- kept
    # exactly ONE of the five retrieved chunks. Retrieval had the answer at
    # rank 3 and the model truthfully reported "not in context" because the
    # cap dropped it whole. 6000 fits a full k=5 of today's chunks with
    # headroom while still bounding a runaway injection.
    cap = int(os.getenv("MAX_RAG_TOKENS", "6000"))
    # Sort by score desc so we drop the weakest matches first when over cap.
    ordered = sorted(chunks, key=lambda c: -(c.score or 0))
    total = 0
    kept: List[Chunk] = []
    for c in ordered:
        t = _estimate_tokens(c.text)
        if total + t > cap:
            continue
        kept.append(c)
        total += t
    return kept, total


def _source_class(chunk) -> str:
    """The excerpt's source class, never raising into the answer path."""
    try:
        from app.core.rag.source_class import classify_chunk

        return classify_chunk(chunk)
    except Exception:  # noqa: BLE001 - a classifier must not break retrieval
        _LOG.warning("source_class: classification failed", exc_info=True)
        from app.core.rag.source_class import DEFAULT_CLASS

        return DEFAULT_CLASS


def _audit_chunk(c) -> Dict[str, Any]:
    """One injected chunk for the audit / Sources panel.

    ``source_class`` is #468's tag, consumed at the glass — not retagged here.
    ``source_name`` lets a later reader re-derive the class if an older audit
    row is missing the field.
    """
    return {
        "doc_id": c.doc_id,
        "chunk_index": c.chunk_index,
        "chunk_id": c.chunk_id,
        "project_id": c.project_id,
        "score": c.score,
        "layer": getattr(c, "layer", "own"),
        "knowledge_layer": getattr(c, "knowledge_layer", None),
        "source_name": getattr(c, "source_name", "") or "",
        "source_class": _source_class(c),
    }


def format_chunks_as_system_message(
    chunks: List[Chunk],
    total_candidates: int,
) -> Dict[str, str]:
    """Build the system message that goes into the LLM context."""
    if not chunks:
        return {"role": "system", "content": ""}
    scores = [c.score or 0.0 for c in chunks]
    header = (
        "AUTHORITATIVE REFERENCE CONTEXT — the material below was retrieved from "
        "the project corpus and curated knowledge base for THIS question. Treat it "
        "as ground truth and answer using it. When it states specific facts "
        "(numbers, deadlines, clause references, named principles, definitions), "
        "reproduce them EXACTLY and do NOT substitute or override them with your own "
        "prior knowledge — your training data on this topic may be wrong or generic. "
        "If a detail the user asks for is not in this context, say you don't have it "
        "rather than inventing a plausible answer.\n"
        # Scoped-absence rule. These excerpts are a top-K sample of a corpus that
        # can run to thousands of chunks — they are evidence of what IS present,
        # never evidence of what is absent. Live failure (2026-08-17): asked about
        # the Saudi Building Code, the model received five unrelated excerpts and
        # answered that the corpus "does not mention the Saudi Building Code at
        # all" — two turns after it had itself quoted SBC 304 from that same
        # corpus. The retrieval miss is fixed in rag/retriever.py; this stops a
        # miss from being reported to the user as a fact about the project.
        "SCOPE OF ABSENCE — you are seeing a small sample of the corpus selected "
        "by search, NOT the whole of it. If something is missing from the excerpts "
        "below, the ONLY claim you may make is that it is not in the retrieved "
        "excerpts for this question. NEVER state or imply that the project, the "
        "corpus, or the documents do not contain it, that it is 'not mentioned "
        "anywhere', or that it 'does not apply' — you cannot see far enough to "
        "know that. Say what you did not find, then offer to search again with "
        "the exact document name, code, or section number.\n"
        # Without this the model treats src= as decoration. Construction
        # filenames routinely carry the answer -- expiry dates, revision
        # letters, discipline codes, status -- where the text layer does not.
        "FILENAMES ARE EVIDENCE - each excerpt is tagged with the document it "
        "came from (src=...). A filename may state a fact the extracted text "
        "does not, such as an expiry date, revision letter, or document "
        "status. You may cite it, saying you are reading it from the document "
        "name. Where the filename and the text disagree, give BOTH and say "
        "which is which rather than silently preferring one.\n"
        f"(top {len(chunks)} of {total_candidates} matches; cosine in "
        f"[{min(scores):.3f}, {max(scores):.3f}])\n"
    )
    # Revision currency (§5.2): when an excerpt carries a parsed revision, expose
    # it in the marker (``rev=…``) so the model can attribute the answer to a
    # revision ("per Rev C"). If any surviving excerpt is from a SUPERSEDED
    # document (it only survives as a last resort — nothing current matched),
    # tell the model to flag that rather than answer as if it were current.
    if any(getattr(c, "superseded", False) for c in chunks):
        header += (
            "NOTE: some excerpts below are from a document whose filename marks "
            "it SUPERSEDED/obsolete — no current-revision source was found. State "
            "that the revision may be out of date and advise confirming the "
            "current revision; do not present it as the latest.\n"
        )

    # SOURCE CLASS PRECEDENCE (owner's numbered item 2). Two battery
    # failures had one cause: nothing in the context said which excerpt was
    # the project's own record and which was reference material or a blank
    # form. G1 quoted contract TEMPLATE wording as the contract's Schedule
    # 10 (the project's own says "Not Used"); A5 reproduced the FIDIC
    # knowledge-base note instead of the project's own 0.1% at 8.8.1.
    #
    # Emitted only when the excerpts are actually mixed. On a single-class
    # set the rule cannot change any answer, and an instruction that never
    # applies is noise that costs context and dilutes the ones that do.
    classes = {_source_class(c) for c in chunks}
    if len(classes) > 1:
        header += (
            "SOURCE CLASS — each excerpt below is tagged class=... . "
            "project_corpus is THIS project's own record; knowledge_base is "
            "curated cross-project reference material; template is a blank "
            "or standard form; master_corpus is a disclosed fallback corpus "
            "that is NOT this project.\n"
            "PRECEDENCE — when a project_corpus excerpt and an excerpt of "
            "any other class can both answer, the project_corpus excerpt IS "
            "the answer. Never present knowledge_base, template or "
            "master_corpus wording as what THIS contract, drawing or bill "
            "says; generic or standard wording never overrides this "
            "project's own document. If a project_corpus excerpt states that "
            "a schedule, appendix, section or clause is Not Used, blank, or "
            "not applicable, that IS the answer — do not describe what such "
            "a section contains in a standard form.\n"
        )

    # OLD-pack G1: even on a single-class set, a retrieved register row
    # that says Not Used is the answer. Without this the model restated
    # "answer only from the documents" and never named the row. Do not
    # invent contents — only fire when an excerpt already says Not Used.
    from app.core.rag.retriever import (
        chunk_states_schedule_not_used,
        extract_contract_doc_ids,
    )
    if any(chunk_states_schedule_not_used(c.text or "") for c in chunks):
        header += (
            "SCHEDULE REGISTER — an excerpt below states that a numbered "
            "contract schedule is Not Used. That IS the answer. State that "
            "the schedule is Not Used. Do not give a generic acknowledgement, "
            "and do not describe what such a schedule contains in a standard "
            "form.\n"
        )

    cited_contract_ids: List[str] = []
    seen_cids = set()
    for c in chunks:
        for cid in extract_contract_doc_ids(getattr(c, "source_name", "") or ""):
            if cid not in seen_cids:
                seen_cids.add(cid)
                cited_contract_ids.append(cid)
    if cited_contract_ids:
        named = ", ".join(cid.upper() for cid in cited_contract_ids)
        header += (
            "CONTRACT ATTRIBUTION — these excerpts are from "
            f"{named}. Name that contract/doc id in the answer. Do not cite "
            "a different year's or different contract's documents.\n"
        )

    def _marker(c) -> str:
        rev = getattr(c, "revision", "") or ""
        rev_s = f" rev={rev}" if rev else ""
        sup_s = " SUPERSEDED" if getattr(c, "superseded", False) else ""
        # The FILENAME is evidence in its own right on construction documents:
        # expiry dates, revision letters, discipline codes and status live
        # there and frequently never reach the text layer. Live case — asked
        # when the Street Lighting NOC expires, retrieval returned the right
        # document, but "17 July 2025" exists only in
        # "AM Rev Design NOC ... (exp. 17Jul25).pdf". The extracted text held
        # only an OCR-mangled "Issue Date 13\02\2025" and "Time Duration
        # 6 months", so the model did arithmetic on noise to reach a date the
        # document never states. Nothing about retrieval could fix that; the
        # model simply never saw the name.
        #
        # Truncated because a marker is repeated per excerpt and long
        # engineering filenames would otherwise crowd the token budget. The
        # tail is kept rather than the head: that is where revision letters and
        # parenthesised dates sit.
        name = _truncate_source_name((getattr(c, "source_name", "") or "").strip())
        src_s = f" src={name}" if name else ""
        # class= sits immediately AFTER score= and never between chunk= and
        # score=: the internal-leak detector and _EXCERPT_SPLIT_RE in
        # runtime.py both anchor on "chunk=N score=" being adjacent, and an
        # attribute inserted there would silently switch the leak guard off.
        cls_s = f" class={_source_class(c)}"
        return (f"[doc_id={c.doc_id} chunk={c.chunk_index} "
                f"score={(c.score or 0):.3f}{cls_s}{rev_s}{sup_s}{src_s}] {c.text}")

    body_parts = [_marker(c) for c in chunks]
    return {"role": "system", "content": header + "\n" + "\n\n".join(body_parts)}


from app.core.rag.retriever import (
    extract_contract_doc_ids,
    extract_query_identifiers,
    filename_matches_named_contracts,
    identifier_present_in_text,
    retrieve_with_filter,
)
from app.core.rag import audit as _audit
from app.core.rag import budget as _budget


# ── follow-up query context (RAG_FOLLOWUP_CONTEXT, default OFF) ──────────────
#
# Retrieval has always run on the CURRENT user message alone. That breaks the
# most natural thing a user does — asking a short follow-up:
#
#     user: "what is the backfilling specs"      -> good chunks
#     user: "layers thickness ?"                 -> searches the whole corpus
#                                                   on two words
#
# Live failure 2026-08-02: the second turn retrieved generic front-matter
# (drawing disclaimers, ITP requirements, Variation Order paperwork) and the
# model correctly reported it had no thickness in context — while the answer
# ("SAND AS BACKFILL MATERIAL PLACED ... IN 150mm") sat in a drawing that a
# keyword-rich query surfaced instantly.
#
# Deliberately DETERMINISTIC, not an LLM rewrite: an extra provider call on
# every turn adds latency to a path with a chat-deadline history, adds a new
# failure mode, and cannot be asserted byte-for-byte in tests. This just
# prepends recent user turns when the current message is too thin to retrieve
# on by itself.

_STOPWORDS = frozenset("""
a an the is are was were be been being of for to in on at by with from as and or
but if then than that this these those it its it's what which who whom whose how
why when where do does did doing done have has had having can could should would
will shall may might must i you he she we they me him her us them my your his
their our not no yes please tell show give me about any some more most other
""".split())

_THIN_QUERY_MAX_TERMS = 4
_CONTEXT_TURNS = 2
_CONTEXT_MAX_CHARS = 400


def _content_terms(text: str) -> List[str]:
    """Meaningful search terms in ``text`` — stopwords and punctuation removed."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/\.]*", text or "")
    return [w for w in words if len(w) > 2 and w.lower() not in _STOPWORDS]


def followup_context_enabled() -> bool:
    """ON by default; ``RAG_FOLLOWUP_CONTEXT=0`` is the kill switch.

    This shipped dormant (default OFF) after closing the 2026-08-02 failure
    where "layers thickness ?" lost its subject and retrieved front-matter. A
    dormant fix does not fix anything: on 2026-08-17 the same failure mode
    recurred across a whole session of thin follow-ups — "Backfilling above
    foundations ?", "geotechnical standards what does it contain ??", "Saudi
    buiding code" — each retrieving on two or three words with no subject, and
    each producing an answer that contradicted an earlier turn in the SAME
    conversation.

    A two-word follow-up genuinely cannot be retrieved on: the corpus has
    thousands of chunks and the query carries almost no signal. Leaving the
    expansion off means those turns are decided by noise, which is precisely
    the instability that reads to a user as the assistant contradicting itself.

    Only explicit falsy values disable it now — an unrecognised value keeps the
    safe (enabled) state rather than silently reverting to the broken one.
    """
    raw = (os.getenv("RAG_FOLLOWUP_CONTEXT", "") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def build_retrieval_query(
    user_message: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """The text retrieval should actually search for.

    Returns ``user_message`` UNCHANGED unless every one of these holds:
      * ``RAG_FOLLOWUP_CONTEXT`` is on,
      * the message is THIN (< 4 content terms — i.e. it cannot stand alone
        as a search query), and
      * there is at least one earlier USER turn to borrow context from.

    Only prior *user* turns are used. Assistant turns are excluded on purpose:
    they are model output, and feeding a previous answer back into retrieval
    is how a wrong answer entrenches itself across a conversation.

    A rich message is never modified, so the common case is byte-identical to
    the pre-flag behaviour even when the flag is ON.
    """
    message = user_message or ""
    if not followup_context_enabled():
        return message
    if len(_content_terms(message)) >= _THIN_QUERY_MAX_TERMS:
        return message
    if not history:
        return message

    prior: List[str] = []
    for turn in reversed(history):
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        text = (turn.get("content") or "").strip()
        if not text or text == message.strip():
            continue
        terms = _content_terms(text)
        if terms:
            prior.append(" ".join(terms))
        if len(prior) >= _CONTEXT_TURNS:
            break

    if not prior:
        return message
    # oldest-first so the query reads naturally, then the live message last
    context = " ".join(reversed(prior))[:_CONTEXT_MAX_CHARS]
    return f"{context} {message}".strip()


def rag_inject(
    user_message: str,
    project_id: Optional[str],
    conversation_id: Optional[str],
    user_id: Optional[str],
    agent_name: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[Dict[str, str]], Dict[str, Any]]:
    """Per-turn RAG entry point.

    Returns ``(system_message_or_None, audit_record_dict)``.

    Behaviour:
    1. If ``project_id`` is falsy: returns (None, {}). No audit. The runtime
       won't write anything for that case.
    2. RAG injection now runs for any project-scoped chat turn, regardless of
       which agent handles the turn. This keeps project grounding intact even
       when the smart orchestrator routes a message to ``heavy-reasoning`` or
       another agent. ``agent_name`` is still recorded in the audit log for
       observability only.
    3. Otherwise: snapshot the budget for today, derive ``effective_k`` (5
       normally, 2 if budget_degraded), call ``retrieve_with_filter``.
    4. If retrieved top_score < THRESHOLD or no chunks at all: return
       (None, audit_record) with ``threshold_fired=true`` so the caller can
       still write the audit log and prepend its fallback prefix.
    5. Apply MAX_RAG_TOKENS cap (whole-chunk drops). Format the kept chunks
       as the system message.
    6. ``budget.consume(injected_tokens)`` BEFORE returning so concurrent
       turns see the updated counter.
    """
    if not project_id:
        return None, {}

    now = _dt.datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    threshold = float(os.getenv("RAG_CONFIDENCE_THRESHOLD", "0.4"))
    requested_k = int(os.getenv("RAG_K", "5"))

    budget_state = _budget.snapshot(day=today)
    effective_k = 2 if budget_state["degraded"] else requested_k

    # A short follow-up ("layers thickness ?") cannot retrieve on its own —
    # see build_retrieval_query. Flag-gated; returns user_message untouched
    # when RAG_FOLLOWUP_CONTEXT is off, so the default pipeline is unchanged.
    retrieval_query = build_retrieval_query(user_message, history)

    chunks, noise_filtered = retrieve_with_filter(
        retrieval_query, project_id, k=effective_k,
    )
    top_score = (max(c.score or 0 for c in chunks) if chunks else 0.0)

    # Identifier precision gate: if the user asks for a specific reference
    # and none of the retrieved chunks contain that reference, treat the
    # retrieval as a miss. This stops the model from hallucinating an exact
    # lookup answer from semantically-similar but irrelevant boilerplate.
    identifiers = extract_query_identifiers(user_message or "")

    identifier_miss = False
    if identifiers and chunks:
        id_lower = [i.lower() for i in identifiers]
        if not any(
            identifier_present_in_text(ident, c.text or "")
            for c in chunks
            for ident in id_lower
        ):
            identifier_miss = True

    # A3: a named contract/doc id is scoped to that id's files. Token-soup
    # identifier matching can accept a DD-2022 chunk for a DD-2023 query
    # (prefix + a year in a date + a clause number). Filename is authority.
    named_contracts = extract_contract_doc_ids(user_message or "")
    if named_contracts and chunks:
        from app.core.rag.retriever import _doc_name_for_id

        def _chunk_filename(c) -> str:
            return (getattr(c, "source_name", "") or "") or _doc_name_for_id(
                getattr(c, "doc_id", "") or ""
            )

        scoped = [
            c for c in chunks
            if filename_matches_named_contracts(
                _chunk_filename(c),
                named_contracts,
                chunk_text=c.text or "",
            )
        ]
        if not scoped:
            identifier_miss = True
        else:
            chunks = scoped
            top_score = max(c.score or 0 for c in chunks)

    # STEP 0b — did the answer fall back to the Master Corpus because this
    # project has no usable documents of its own? Any chunk tagged
    # ``master_corpus`` by the retriever means yes; the runtime discloses it.
    fallback_used = any(getattr(c, "layer", "own") == "master_corpus" for c in chunks)

    audit_rec: Dict[str, Any] = {
        "timestamp": now.isoformat() + "Z",
        "project_id": project_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "agent_name": agent_name,
        "user_message_preview": (user_message or "")[:200],
        # Only differs from the message when the follow-up expansion fired —
        # so the audit log shows exactly what was searched for, and whether
        # the expansion is what found (or missed) the chunks.
        "retrieval_query_preview": (retrieval_query or "")[:200],
        "followup_context_applied": retrieval_query != (user_message or ""),
        "requested_k": requested_k,
        "noise_filtered_count": noise_filtered,
        "top_score": top_score,
        "budget_remaining": budget_state["remaining"],
        "budget_degraded": budget_state["degraded"],
        "fallback_used": fallback_used,
        "extracted_contract_ids": named_contracts,
    }

    if not chunks or top_score < threshold or identifier_miss:
        audit_rec.update({
            "injected_k": 0,
            "injected_tokens": 0,
            "threshold_fired": True,
            "identifier_miss": identifier_miss,
            "extracted_identifiers": identifiers,
            "chunks": [_audit_chunk(c) for c in chunks],
        })
        _audit.write(audit_rec)
        return None, audit_rec

    kept, total_tokens = apply_token_cap(chunks)
    sys_msg = format_chunks_as_system_message(kept, total_candidates=len(chunks))

    # Recompute against the injected set: the token cap may have dropped the
    # master-corpus chunk, in which case the answer is NOT a fallback answer.
    kept_fallback_used = any(
        getattr(c, "layer", "own") == "master_corpus" for c in kept
    )
    audit_rec.update({
        "injected_k": len(kept),
        "injected_tokens": total_tokens,
        "threshold_fired": False,
        "fallback_used": kept_fallback_used,
        "chunks": [_audit_chunk(c) for c in kept],
    })
    _audit.write(audit_rec)
    _budget.consume(day=today, tokens=total_tokens)
    return sys_msg, audit_rec
