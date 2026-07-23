#!/usr/bin/env python3
"""In-distribution eval for a RAG-grounded adapter — same 10 queries
as ``eval_adapter_in_distribution.py``, but the user message is built
the same way the grounded training set was:

    Context:
    <top-K retrieved chunks from globalkb>

    Question: <original instruction>

This matches what the adapter was trained to consume, so the eval
tests "does the adapter use retrieved context correctly" instead of
"does the adapter recall facts from weights."

Substring scoring is the same as the regular in-distribution eval so
the PASS/FAIL counts are directly comparable.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("RAG_EMBEDDING_MODEL", "minishlab/potion-base-8M")

from eval_adapter_in_distribution import _QUERIES, _score, Sample, _format_chat_prompt, _decode_response, _ensure_sampler_path  # noqa: E402
from app.core.rag.retriever import retrieve_with_filter  # noqa: E402

logger = logging.getLogger(__name__)

PROJECT = "globalkb"


def _format_user_with_context(query: str, k: int = 3, threshold: float = 0.4) -> tuple:
    """Mirror the grounded-training format. Returns
    ``(user_content_string, n_chunks_used, top_score, docs_hit)``."""
    chunks, _ = retrieve_with_filter(query, PROJECT, k=k)
    if not chunks:
        return query, 0, 0.0, []
    top = max(c.score or 0 for c in chunks)
    if top < threshold:
        return query, 0, top, []
    parts = []
    for c in chunks[:k]:
        parts.append(
            f"[doc_id={c.doc_id} chunk={c.chunk_index} score={(c.score or 0):.3f}]\n{c.text.strip()}"
        )
    context_block = "\n\n".join(parts)
    user_content = f"Context:\n{context_block}\n\nQuestion: {query}"
    return user_content, len(parts), top, sorted({c.doc_id for c in chunks[:k]})


def _sample(client, tokenizer, user_text: str, max_new_tokens: int, temperature: float):
    import tinker
    prompt, prompt_ids = _format_chat_prompt(tokenizer, user_text)
    params = tinker.SamplingParams(max_tokens=max_new_tokens, temperature=temperature, top_p=0.9)
    fut = client.sample(prompt=prompt, num_samples=1, sampling_params=params)
    resp = fut.result()
    seqs = resp.sequences
    if not seqs:
        return Sample(text="(no sequence returned)", stop_reason="empty")
    return _decode_response(tokenizer, prompt_ids, seqs[0])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tinker-path", required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=300)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.4)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if "TINKER_API_KEY" not in os.environ:
        raise RuntimeError("TINKER_API_KEY not set.")

    import tinker
    service = tinker.ServiceClient()
    sampler_path = _ensure_sampler_path(service, args.tinker_path)

    logger.info("adapter client (%s)", sampler_path)
    client = service.create_sampling_client(model_path=sampler_path)
    tokenizer = client.get_tokenizer()

    rows = []
    for i, q in enumerate(_QUERIES, 1):
        logger.info("[%d/%d] %s", i, len(_QUERIES), q.query[:80])
        user_content, n_chunks, top, docs = _format_user_with_context(
            q.query, k=args.k, threshold=args.threshold
        )
        sample = _sample(client, tokenizer, user_content, args.max_new_tokens, args.temperature)
        score = _score(sample.text, q)
        rows.append((q, sample, score, n_chunks, top, docs))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in rows if r[2]["passed"])

    with args.out.open("w", encoding="utf-8") as f:
        f.write("# RAG-grounded adapter in-distribution eval\n\n")
        f.write(f"- adapter (sampler-weights): `{sampler_path}`\n")
        f.write(f"- base model: `{args.base_model}`\n")
        f.write(f"- RAG project: `{PROJECT}` (top_k={args.k}, threshold={args.threshold})\n")
        f.write(f"- temperature={args.temperature}, max_new_tokens={args.max_new_tokens}\n")
        f.write(f"- generated: {_dt.datetime.utcnow().isoformat()}Z\n\n")
        f.write(f"## Summary\n\n- **Adapter pass: {passed} / {len(rows)}**\n")
        f.write("- (RAG-only baseline was 7/10; best LoRA-without-RAG was 3/10)\n\n")
        f.write("## Per-question\n\n| # | Source | Verdict | Top score | Docs hit |\n|---|---|---|---|---|\n")
        for i, (q, s, sc, n_chunks, top, docs) in enumerate(rows, 1):
            v = "PASS" if sc["passed"] else "FAIL"
            docs_str = ",".join(docs) if docs else "—"
            f.write(f"| {i} | `{q.source}` | {v} | {top:.3f} | {docs_str} |\n")
        f.write("\n---\n\n")
        for i, (q, s, sc, n_chunks, top, docs) in enumerate(rows, 1):
            f.write(f"## Q{i} — `{q.source}`\n\n")
            f.write(f"**Query:** {q.query}\n\n")
            f.write(f"**Notes (ground truth):** {q.notes}\n\n")
            f.write(f"**Expected:** {q.expected}    **Forbidden:** {q.forbidden}\n\n")
            f.write(f"**Retrieved:** {n_chunks} chunks, top_score={top:.3f}, docs={docs}\n\n")
            f.write(f"### Adapter — **{'PASS' if sc['passed'] else 'FAIL'}**\n\n")
            f.write("```\n" + s.text + "\n```\n\n")
            f.write(f"- found: {sc['found_expected']}\n")
            f.write(f"- missing: {sc['missing_expected']}\n")
            f.write(f"- forbidden_hits: {sc['forbidden_hits']}\n\n")
            f.write("---\n\n")

    logger.info("grounded-adapter pass: %d / %d -> %s", passed, len(rows), args.out)
    print(str(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
