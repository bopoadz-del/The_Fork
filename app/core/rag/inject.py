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
    cap = int(os.getenv("MAX_RAG_TOKENS", "1500"))
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

    def _marker(c) -> str:
        rev = getattr(c, "revision", "") or ""
        rev_s = f" rev={rev}" if rev else ""
        sup_s = " SUPERSEDED" if getattr(c, "superseded", False) else ""
        return (f"[doc_id={c.doc_id} chunk={c.chunk_index} "
                f"score={(c.score or 0):.3f}{rev_s}{sup_s}] {c.text}")

    body_parts = [_marker(c) for c in chunks]
    return {"role": "system", "content": header + "\n" + "\n\n".join(body_parts)}


from app.core.rag.retriever import retrieve_with_filter, extract_query_identifiers
from app.core.rag import audit as _audit
from app.core.rag import budget as _budget


def rag_inject(
    user_message: str,
    project_id: Optional[str],
    conversation_id: Optional[str],
    user_id: Optional[str],
    agent_name: str,
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

    chunks, noise_filtered = retrieve_with_filter(
        user_message, project_id, k=effective_k,
    )
    top_score = (max(c.score or 0 for c in chunks) if chunks else 0.0)

    # Identifier precision gate: if the user asks for a specific reference
    # and none of the retrieved chunks contain that reference, treat the
    # retrieval as a miss. This stops the model from hallucinating an exact
    # lookup answer from semantically-similar but irrelevant boilerplate.
    identifiers = extract_query_identifiers(user_message or "")

    def _identifier_present_in_text(ident: str, text: str) -> bool:
        """A reference is present if every token of the identifier appears
        in the chunk text. This tolerates intervening label words such as
        "Ref" / "No" / "#" (e.g. "VO 99" matches "VO Ref: 99").
        """
        text_tokens = set(re.split(r"[^a-z0-9]+", text.lower())) - {"", "ref", "no", "#"}
        ident_tokens = [t for t in re.split(r"[^a-z0-9]+", ident.lower()) if t]
        return bool(ident_tokens) and all(t in text_tokens for t in ident_tokens)

    identifier_miss = False
    if identifiers and chunks:
        id_lower = [i.lower() for i in identifiers]
        if not any(
            _identifier_present_in_text(ident, c.text or "")
            for c in chunks
            for ident in id_lower
        ):
            identifier_miss = True

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
        "requested_k": requested_k,
        "noise_filtered_count": noise_filtered,
        "top_score": top_score,
        "budget_remaining": budget_state["remaining"],
        "budget_degraded": budget_state["degraded"],
        "fallback_used": fallback_used,
    }

    if not chunks or top_score < threshold or identifier_miss:
        audit_rec.update({
            "injected_k": 0,
            "injected_tokens": 0,
            "threshold_fired": True,
            "identifier_miss": identifier_miss,
            "extracted_identifiers": identifiers,
            "chunks": [
                {"doc_id": c.doc_id, "chunk_index": c.chunk_index,
                 "chunk_id": c.chunk_id, "project_id": c.project_id,
                 "score": c.score, "layer": getattr(c, "layer", "own"),
                 "knowledge_layer": getattr(c, "knowledge_layer", None)}
                for c in chunks
            ],
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
        "chunks": [
            {"doc_id": c.doc_id, "chunk_index": c.chunk_index,
             "chunk_id": c.chunk_id, "project_id": c.project_id,
             "score": c.score, "layer": getattr(c, "layer", "own"),
             "knowledge_layer": getattr(c, "knowledge_layer", None)}
            for c in kept
        ],
    })
    _audit.write(audit_rec)
    _budget.consume(day=today, tokens=total_tokens)
    return sys_msg, audit_rec
