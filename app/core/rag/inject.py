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
        f"Relevant project context (top {len(chunks)} of {total_candidates} "
        f"matches; cosine in [{min(scores):.3f}, {max(scores):.3f}]):\n"
    )
    body_parts = [
        f"[doc_id={c.doc_id} chunk={c.chunk_index} score={(c.score or 0):.3f}] {c.text}"
        for c in chunks
    ]
    return {"role": "system", "content": header + "\n" + "\n\n".join(body_parts)}


from app.core.rag.retriever import retrieve_with_filter
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
    1. If agent_name != "project-assistant" or project_id is falsy: returns
       (None, {}). No audit. The runtime won't write anything for that case.
    2. Otherwise: snapshot the budget for today, derive ``effective_k`` (5
       normally, 2 if budget_degraded), call ``retrieve_with_filter``.
    3. If retrieved top_score < THRESHOLD or no chunks at all: return
       (None, audit_record) with ``threshold_fired=true`` so the caller can
       still write the audit log and prepend its fallback prefix.
    4. Apply MAX_RAG_TOKENS cap (whole-chunk drops). Format the kept chunks
       as the system message.
    5. ``budget.consume(injected_tokens)`` BEFORE returning so concurrent
       turns see the updated counter.
    """
    if agent_name != "project-assistant" or not project_id:
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
    }

    if not chunks or top_score < threshold:
        audit_rec.update({
            "injected_k": 0,
            "injected_tokens": 0,
            "threshold_fired": True,
            "chunks": [
                {"doc_id": c.doc_id, "chunk_index": c.chunk_index,
                 "score": c.score} for c in chunks
            ],
        })
        _audit.write(audit_rec)
        return None, audit_rec

    kept, total_tokens = apply_token_cap(chunks)
    sys_msg = format_chunks_as_system_message(kept, total_candidates=len(chunks))

    audit_rec.update({
        "injected_k": len(kept),
        "injected_tokens": total_tokens,
        "threshold_fired": False,
        "chunks": [
            {"doc_id": c.doc_id, "chunk_index": c.chunk_index,
             "score": c.score} for c in kept
        ],
    })
    _audit.write(audit_rec)
    _budget.consume(day=today, tokens=total_tokens)
    return sys_msg, audit_rec
