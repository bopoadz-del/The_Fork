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
