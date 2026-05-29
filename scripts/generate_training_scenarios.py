#!/usr/bin/env python3
"""Generate (question, expert-answer) training pairs from a project's
indexed documents (PR 25).

Reads chunks from the doc_index for a given ``project_id``, sends each
chunk to the chat block (LLM) with a prompt asking for synthetic Q&A
in the construction expert voice, and writes the pairs as JSONL ready
for ``scripts/finetune_router.py``.

Pipeline:

    1. Hydration ingests Drive / local folders / dropbox into the
       platform (see app/core/learning/hydration.py — Drive ingest is
       now recursive in this PR).
    2. doc_index extracts text + chunks per document.
    3. THIS SCRIPT walks the chunks, calls the LLM per chunk,
       parses + filters the response, writes JSONL.
    4. finetune_router.py consumes the JSONL.

Why generate synthetic Q&A instead of using documents directly?

  Documents are declarative: "Concrete cover for slabs in moderate
  exposure shall be 30mm minimum per ACI 318." The fine-tune target
  is conversational: "what's the cover for a slab in moderate
  exposure?" → "30mm minimum per ACI 318." Same fact, different
  shape. Direct document chunks would teach the model to reproduce
  the SOP, not to answer questions about it.

CLI:
    python scripts/generate_training_scenarios.py \\
        --project-id <project_id> \\
        --out data/learning/training_scenarios.jsonl \\
        [--questions-per-chunk 3] \\
        [--min-chunk-chars 200] \\
        [--max-chunks 1000] \\
        [--provider deepseek|local_lora|offline_template]

Honest non-verification: when the chat block returns the offline
template (no LLM reachable), this script silently degrades to zero
output rather than producing garbage Q&A. The provider check in the
filtering step catches that.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Dict, Iterator, List, Optional

# Resolve the `app` package when invoked directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# Prompt template — separate constant so tests can introspect it and so
# operators can swap it via --prompt-file without code edits.
_DEFAULT_PROMPT = """You are a senior construction project manager reviewing internal SOPs and project documents.

Below is an excerpt from {source}. Generate {n} question-answer pairs that a
project engineer or graduate engineer might genuinely ask about this material.

Constraints:
- Each question must be specific and answerable from the excerpt.
- Each answer must be 2-4 sentences, factually grounded in the excerpt, in
  the voice of an experienced PM (no preamble like "Based on the text...").
- Skip generic questions ("what does this say about safety?") in favor of
  concrete ones ("what's the minimum concrete cover for slabs in marine
  exposure?").
- Output STRICT JSONL — one JSON object per line, no surrounding prose,
  no markdown fences. Schema: {{"instruction": "...", "response": "..."}}

Excerpt:
\"\"\"
{chunk}
\"\"\"

JSONL:"""


def iter_chunks_for_project(
    project_id: str,
    min_chars: int = 200,
    max_chunks: Optional[int] = None,
) -> Iterator[Dict[str, str]]:
    """Yield chunk dicts ``{text, source}`` from the doc_index for a project.

    ``source`` is the document's filename (e.g.
    ``"600-Procurement & Contracts/standard_form_subcontract.pdf"``) when
    the doc_index recorded one, otherwise the document_id.
    """
    from app.core import doc_index

    index = doc_index._load_index(project_id)
    if not index:
        return
    docs = index.get("documents", [])
    count = 0
    for doc in docs:
        filename = doc.get("filename") or doc.get("document_id") or "<unknown>"
        for chunk in doc.get("chunks", []):
            if isinstance(chunk, dict):
                text = chunk.get("text") or ""
            else:
                text = str(chunk)
            text = text.strip()
            if len(text) < min_chars:
                continue
            yield {"text": text, "source": filename}
            count += 1
            if max_chunks and count >= max_chunks:
                return


async def _generate_for_chunk(
    chunk: Dict[str, str],
    questions_per_chunk: int,
    provider_hint: str,
) -> List[Dict[str, str]]:
    """Send one chunk to the chat block, parse JSONL out of the response.

    Returns an empty list when:
    - the chat block returned the offline_template (no LLM available)
    - the response wasn't parseable as JSONL
    - the response had no valid {instruction, response} rows
    """
    from app.blocks import BLOCK_REGISTRY

    cls = BLOCK_REGISTRY.get("chat")
    if cls is None:
        logger.warning("chat block not in registry — cannot generate scenarios")
        return []

    block = cls()
    prompt = _DEFAULT_PROMPT.format(
        source=chunk["source"], n=questions_per_chunk, chunk=chunk["text"]
    )
    envelope = await block.execute({"text": prompt}, {
        "max_tokens": 1500,
        "temperature": 0.7,
    })
    inner = envelope.get("result") if isinstance(envelope, dict) else {}
    if not isinstance(inner, dict):
        return []

    # Refuse the offline template path — the deterministic fallback can't
    # actually generate Q&A. Better to skip than write garbage.
    provider = inner.get("provider", "")
    if provider in ("offline_template", "", None):
        return []
    if provider_hint and provider_hint != "any" and provider != provider_hint:
        # Operator forced a specific provider; skip this chunk if we got
        # something else. Lets them re-run cleanly after configuring the
        # provider they wanted.
        return []

    raw = (inner.get("response") or inner.get("text") or "").strip()
    if not raw:
        return []

    pairs: List[Dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip code-fence noise the LLM might wrap output in despite the prompt
        if line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        instruction = (obj.get("instruction") or "").strip()
        response = (obj.get("response") or "").strip()
        # Light quality filters: skip refusals / one-liners
        if len(instruction) < 10 or len(response) < 30:
            continue
        pairs.append({
            "instruction": instruction,
            "response": response,
            "source": chunk["source"],
        })
    return pairs


async def _run(
    project_id: str,
    out_path: str,
    questions_per_chunk: int,
    min_chunk_chars: int,
    max_chunks: Optional[int],
    provider_hint: str,
) -> int:
    chunks = list(iter_chunks_for_project(
        project_id, min_chars=min_chunk_chars, max_chunks=max_chunks
    ))
    if not chunks:
        logger.error(
            "no chunks found for project %s — has the project been hydrated?",
            project_id,
        )
        return 1

    logger.info("found %d chunks ≥ %d chars for project %s",
                len(chunks), min_chunk_chars, project_id)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    total_pairs = 0
    skipped = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for i, chunk in enumerate(chunks, start=1):
            pairs = await _generate_for_chunk(chunk, questions_per_chunk, provider_hint)
            if not pairs:
                skipped += 1
                continue
            for p in pairs:
                out.write(json.dumps(p, ensure_ascii=False) + "\n")
                total_pairs += 1
            if i % 20 == 0:
                logger.info("processed %d/%d chunks → %d pairs (%d skipped)",
                            i, len(chunks), total_pairs, skipped)

    logger.info(
        "wrote %d pairs to %s (%d chunks processed, %d skipped)",
        total_pairs, out_path, len(chunks), skipped,
    )
    if total_pairs == 0:
        logger.error(
            "ZERO pairs generated. Check that the chat block has a working "
            "LLM provider (DEEPSEEK_API_KEY set, or a local model configured)."
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True, help="Project to source chunks from")
    parser.add_argument(
        "--out",
        default=os.path.join(
            os.getenv("DATA_DIR", "./data"), "learning", "training_scenarios.jsonl"
        ),
    )
    parser.add_argument("--questions-per-chunk", type=int, default=3)
    parser.add_argument("--min-chunk-chars", type=int, default=200)
    parser.add_argument(
        "--max-chunks", type=int, default=None,
        help="Stop after this many chunks (default: process all)",
    )
    parser.add_argument(
        "--provider", default="any",
        choices=("any", "deepseek", "local_ollama", "local_llama_cpp", "local_lora"),
        help="Require this provider on chat responses (default: any non-offline)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    return asyncio.run(_run(
        project_id=args.project_id,
        out_path=args.out,
        questions_per_chunk=args.questions_per_chunk,
        min_chunk_chars=args.min_chunk_chars,
        max_chunks=args.max_chunks,
        provider_hint=args.provider,
    ))


if __name__ == "__main__":
    sys.exit(main())
