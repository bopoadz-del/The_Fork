#!/usr/bin/env python3
"""Three-way eval for the 20260613-174725 pilot adapter against the
drive_archive corpus. Reuses _QUERIES and _score from
eval_adapter_in_distribution so the scoring is identical to the original
pilot run.

Legs:
  A. RAG-only          -> Ollama qwen3-coder:480b-cloud with retrieved
                          context (system message), no adapter.
  B. RAG + pilot       -> pilot Tinker adapter with retrieved context
                          (user-message Context: block, matches training
                          format).
  C. adapter-only      -> pilot Tinker adapter with EMPTY Context: block
                          (same prompt shape as B, no chunks).

Retrieval project: drive_archive (139,949 chunks, hybrid BM25+vector
RRF). k=5 for both A and B so the only A-vs-B difference is the
model / prompt shape, not retrieval depth. No threshold gating — the
score scale in drive_archive mixes BM25 raw and vector cosine, and the
0.4 default used by the grounded harness would zero out half the
queries.

NO modifications to production code or to the existing harness; this is
a thin standalone runner.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("RAG_EMBEDDING_MODEL", "minishlab/potion-base-8M")

from eval_adapter_in_distribution import (  # noqa: E402
    _QUERIES,
    _score,
    Sample,
    _format_chat_prompt,
    _decode_response,
    _ensure_sampler_path,
)
from app.core.rag.retriever import retrieve_with_filter  # noqa: E402
from app.core.rag.inject import format_chunks_as_system_message, apply_token_cap  # noqa: E402

logger = logging.getLogger("eval3way")

PROJECT = "drive_archive"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------


@dataclass
class Retrieved:
    """Common retrieval payload for both legs A and B."""

    chunks: list  # list[RetrievedChunk]-ish
    top_score: float
    docs_hit: List[str]

    @property
    def n(self) -> int:
        return len(self.chunks)


def _retrieve(query: str, k: int = 5) -> Retrieved:
    chunks, _ = retrieve_with_filter(query, PROJECT, k=k)
    if not chunks:
        return Retrieved(chunks=[], top_score=0.0, docs_hit=[])
    top = max(c.score or 0 for c in chunks)
    docs = sorted({c.doc_id for c in chunks})
    return Retrieved(chunks=chunks, top_score=top, docs_hit=docs)


def _format_system_for_ollama(r: Retrieved) -> str:
    """Leg A: production-shaped system message (matches inject.py)."""
    if not r.chunks:
        return ""
    kept, _ = apply_token_cap(r.chunks)
    msg = format_chunks_as_system_message(kept, total_candidates=len(r.chunks))
    return msg["content"] if isinstance(msg, dict) else str(msg)


def _format_user_for_adapter(query: str, r: Retrieved) -> str:
    """Leg B: training-shaped Context: + Question: user message."""
    if not r.chunks:
        # Same shape as leg C (empty Context block) so a query that
        # happens to retrieve nothing still hits the trained format.
        return f"Context:\n\nQuestion: {query}"
    parts = []
    for c in r.chunks:
        parts.append(
            f"[doc_id={c.doc_id} chunk={c.chunk_index} score={(c.score or 0):.3f}]\n{c.text.strip()}"
        )
    context_block = "\n\n".join(parts)
    return f"Context:\n{context_block}\n\nQuestion: {query}"


def _format_user_empty_context(query: str) -> str:
    """Leg C: empty Context: block — trained shape, no retrieved chunks."""
    return f"Context:\n\nQuestion: {query}"


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------


def _ollama_chat(system_msg: str, user_msg: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": (
            [{"role": "system", "content": system_msg}] if system_msg else []
        )
        + [{"role": "user", "content": user_msg}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 400},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "").strip()


def _sample_adapter(client, tokenizer, user_text: str, max_new_tokens: int, temperature: float) -> Sample:
    import tinker
    prompt, prompt_ids = _format_chat_prompt(tokenizer, user_text)
    params = tinker.SamplingParams(max_tokens=max_new_tokens, temperature=temperature, top_p=0.9)
    fut = client.sample(prompt=prompt, num_samples=1, sampling_params=params)
    resp = fut.result()
    seqs = resp.sequences
    if not seqs:
        return Sample(text="(no sequence returned)", stop_reason="empty")
    return _decode_response(tokenizer, prompt_ids, seqs[0])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tinker-path",
        default="tinker://a8988d2d-5c45-5b77-ad67-15b21bed89a0:train:0/weights/checkpoints-Qwen_Qwen3.5-4B-20260613-174725",
    )
    parser.add_argument("--ollama-model", default="qwen3-coder:480b-cloud")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--max-new-tokens", type=int, default=300)
    parser.add_argument("--temperature", type=float, default=0.2)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if "TINKER_API_KEY" not in os.environ:
        raise RuntimeError("TINKER_API_KEY not set.")

    import tinker
    service = tinker.ServiceClient()
    sampler_path = _ensure_sampler_path(service, args.tinker_path)
    logger.info("adapter sampler-weights: %s", sampler_path)
    adapter_client = service.create_sampling_client(model_path=sampler_path)
    adapter_tokenizer = adapter_client.get_tokenizer()

    overall_t0 = time.time()
    leg_times = {"A": 0.0, "B": 0.0, "C": 0.0}

    rows = []  # one per query: dict of leg-A, leg-B, leg-C results + retrieval meta
    for i, q in enumerate(_QUERIES, 1):
        logger.info("[%d/%d] %s", i, len(_QUERIES), q.query[:80])
        retrieved = _retrieve(q.query, k=args.k)
        logger.info(
            "  retrieved %d chunks, top_score=%.4f, docs=%s",
            retrieved.n,
            retrieved.top_score,
            retrieved.docs_hit,
        )

        # Leg A: RAG-only via Ollama
        sys_msg = _format_system_for_ollama(retrieved)
        t0 = time.time()
        try:
            if not sys_msg:
                text_a = _ollama_chat("", q.query, model=args.ollama_model)
            else:
                text_a = _ollama_chat(sys_msg, q.query, model=args.ollama_model)
        except Exception as e:
            logger.warning("Ollama call failed: %s", e)
            text_a = f"(ollama error: {e})"
        leg_times["A"] += time.time() - t0
        score_a = _score(text_a, q)

        # Leg B: RAG + pilot adapter
        user_b = _format_user_for_adapter(q.query, retrieved)
        t0 = time.time()
        sample_b = _sample_adapter(
            adapter_client, adapter_tokenizer, user_b, args.max_new_tokens, args.temperature
        )
        leg_times["B"] += time.time() - t0
        score_b = _score(sample_b.text, q)

        # Leg C: adapter-only with empty Context block
        user_c = _format_user_empty_context(q.query)
        t0 = time.time()
        sample_c = _sample_adapter(
            adapter_client, adapter_tokenizer, user_c, args.max_new_tokens, args.temperature
        )
        leg_times["C"] += time.time() - t0
        score_c = _score(sample_c.text, q)

        rows.append(
            {
                "q": q,
                "retrieved": retrieved,
                "a_text": text_a,
                "a_score": score_a,
                "b_text": sample_b.text,
                "b_score": score_b,
                "c_text": sample_c.text,
                "c_score": score_c,
            }
        )
        logger.info(
            "  A=%s B=%s C=%s",
            "PASS" if score_a["passed"] else "FAIL",
            "PASS" if score_b["passed"] else "FAIL",
            "PASS" if score_c["passed"] else "FAIL",
        )

    overall_secs = time.time() - overall_t0

    n = len(rows)
    pass_a = sum(1 for r in rows if r["a_score"]["passed"])
    pass_b = sum(1 for r in rows if r["b_score"]["passed"])
    pass_c = sum(1 for r in rows if r["c_score"]["passed"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("# Three-way eval -- pilot 20260613-174725 against drive_archive\n\n")
        f.write(f"- Corpus: `{PROJECT}` (139,949 chunks, 2,924 docs, hybrid BM25+vector RRF)\n")
        f.write(f"- Queries: {n} (from eval_adapter_in_distribution._QUERIES)\n")
        f.write(f"- Adapter sampler-weights: `{sampler_path}`\n")
        f.write(f"- Ollama model: `{args.ollama_model}` via `{OLLAMA_URL}`\n")
        f.write(f"- k={args.k}, temperature={args.temperature}, max_new_tokens={args.max_new_tokens}\n")
        f.write(f"- threshold: NONE (score scale mixes BM25 raw and vector cosine; gating would zero out half the queries)\n")
        f.write(f"- generated: {_dt.datetime.utcnow().isoformat()}Z\n")
        f.write(f"- wall clock: {overall_secs:.1f}s (A={leg_times['A']:.1f}s, B={leg_times['B']:.1f}s, C={leg_times['C']:.1f}s)\n\n")

        f.write("## Summary\n\n")
        f.write(f"| Leg | Pass | % |\n|---|---|---|\n")
        f.write(f"| A. RAG-only (Ollama, no adapter) | {pass_a} / {n} | {100*pass_a/n:.1f}% |\n")
        f.write(f"| B. RAG + pilot adapter | {pass_b} / {n} | {100*pass_b/n:.1f}% |\n")
        f.write(f"| C. adapter-only (empty Context) | {pass_c} / {n} | {100*pass_c/n:.1f}% |\n\n")

        f.write("## Per-question verdicts\n\n")
        f.write("| # | Source | Query | RAG-only | RAG+pilot | adapter-only | Top score | Chunks | Docs |\n")
        f.write("|---|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            q = r["q"]
            ret = r["retrieved"]
            a = "PASS" if r["a_score"]["passed"] else "FAIL"
            b = "PASS" if r["b_score"]["passed"] else "FAIL"
            c = "PASS" if r["c_score"]["passed"] else "FAIL"
            q_short = q.query.replace("|", "\\|")[:60]
            docs_str = ",".join(d[:8] for d in ret.docs_hit) if ret.docs_hit else "--"
            f.write(
                f"| {i} | `{q.source}` | {q_short} | {a} | {b} | {c} | "
                f"{ret.top_score:.3f} | {ret.n} | {docs_str} |\n"
            )

        f.write("\n## Retrieval flags\n\n")
        zero_chunk = [i for i, r in enumerate(rows, 1) if r["retrieved"].n == 0]
        weak_top = [
            (i, r["retrieved"].top_score)
            for i, r in enumerate(rows, 1)
            if r["retrieved"].n > 0 and r["retrieved"].top_score < 0.3
        ]
        f.write(f"- Zero-chunk queries (legs A and B got no retrieval -- shape collapses toward leg C): "
                f"{zero_chunk if zero_chunk else 'none'}\n")
        f.write(f"- Low top_score (<0.3) queries (retrieval surfaced something but plausibly off-topic): "
                f"{weak_top if weak_top else 'none'}\n\n")

        f.write("---\n\n## Per-question detail\n\n")
        for i, r in enumerate(rows, 1):
            q = r["q"]
            ret = r["retrieved"]
            f.write(f"### Q{i} -- `{q.source}`\n\n")
            f.write(f"**Query:** {q.query}\n\n")
            f.write(f"**Notes (ground truth):** {q.notes}\n\n")
            f.write(f"**Expected:** {q.expected}    **Forbidden:** {q.forbidden}\n\n")
            f.write(f"**Retrieved:** {ret.n} chunks, top_score={ret.top_score:.4f}\n\n")
            if ret.chunks:
                f.write("Top chunks (doc_id : chunk_index : score):\n")
                for c in ret.chunks[: args.k]:
                    f.write(f"- `{c.doc_id}` : {c.chunk_index} : {(c.score or 0):.4f}\n")
                f.write("\n")

            for leg_name, key in (("A. RAG-only", "a"), ("B. RAG+pilot", "b"), ("C. adapter-only", "c")):
                sc = r[f"{key}_score"]
                text = r[f"{key}_text"]
                f.write(f"#### {leg_name} -- **{'PASS' if sc['passed'] else 'FAIL'}**\n\n")
                f.write("```\n" + text[:1200] + ("\n... (truncated)" if len(text) > 1200 else "") + "\n```\n\n")
                f.write(f"- found: {sc['found_expected']}\n")
                f.write(f"- missing: {sc['missing_expected']}\n")
                f.write(f"- forbidden_hits: {sc['forbidden_hits']}\n\n")
            f.write("---\n\n")

    # JSON one-liner at the very end (machine-readable)
    summary_json = {
        "rag_only_pct": round(100 * pass_a / n, 1),
        "rag_plus_pilot_pct": round(100 * pass_b / n, 1),
        "adapter_only_pct": round(100 * pass_c / n, 1),
        "n_queries": n,
    }
    logger.info("totals: A=%d/%d B=%d/%d C=%d/%d -> %s", pass_a, n, pass_b, n, pass_c, n, args.out)
    print(str(args.out))
    print(json.dumps(summary_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
