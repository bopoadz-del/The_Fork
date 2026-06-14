#!/usr/bin/env python3
"""Three-way eval for the 20260613-174725 pilot adapter against drive_archive,
using a drive_archive-anchored eval set (NOT the historical _QUERIES which were
tuned for PRC-501/CESMM4/EVM/L2-schedule content).

Reuses helpers from `_eval_pilot_3way` and `eval_adapter_in_distribution` so the
scoring is identical. Queries come from
`data/learning/adapters/20260613-174725/drive_archive_eval_set.json`.

Legs:
  A. RAG-only           -> Ollama qwen3-coder:480b-cloud with retrieved context
                           (production system message), no adapter.
  B. RAG + pilot        -> pilot Tinker adapter on the same RAG context
                           (training-shape Context:/Question: user message).
  C. adapter-only       -> pilot Tinker adapter with empty Context block.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("RAG_EMBEDDING_MODEL", "minishlab/potion-base-8M")
os.environ.setdefault("RAG_HYBRID_SEARCH", "true")

from eval_adapter_in_distribution import (  # noqa: E402
    _score,
    InQuery,
)
from _eval_pilot_3way import (  # noqa: E402
    _retrieve,
    _format_system_for_ollama,
    _format_user_for_adapter,
    _format_user_empty_context,
    _ollama_chat,
    _sample_adapter,
    OLLAMA_URL,
    PROJECT,
)
from eval_adapter_in_distribution import _ensure_sampler_path  # noqa: E402

logger = logging.getLogger("eval3way_da")

EVAL_SET = Path("data/learning/adapters/20260613-174725/drive_archive_eval_set.json")


def _load_queries(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["queries"]


def _entry_to_inquery(entry: dict) -> InQuery:
    return InQuery(
        query=entry["query"],
        expected=list(entry["expected"]),
        forbidden=list(entry.get("forbidden") or []),
        source=f"drive_archive/{entry['category']}/{entry['id']}",
        notes=f"source_doc_id={entry['source_doc_id']} chunk={entry['source_chunk_index']}",
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tinker-path",
        default="tinker://a8988d2d-5c45-5b77-ad67-15b21bed89a0:train:0/weights/checkpoints-Qwen_Qwen3.5-4B-20260613-174725",
    )
    parser.add_argument("--ollama-model", default="qwen3-coder:480b-cloud")
    parser.add_argument("--eval-set", type=Path, default=EVAL_SET)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--max-new-tokens", type=int, default=300)
    parser.add_argument("--temperature", type=float, default=0.2)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if "TINKER_API_KEY" not in os.environ:
        raise RuntimeError("TINKER_API_KEY not set.")

    entries = _load_queries(args.eval_set)
    queries = [(_entry_to_inquery(e), e) for e in entries]
    logger.info("loaded %d queries from %s", len(queries), args.eval_set)

    import tinker
    service = tinker.ServiceClient()
    sampler_path = _ensure_sampler_path(service, args.tinker_path)
    logger.info("adapter sampler-weights: %s", sampler_path)
    adapter_client = service.create_sampling_client(model_path=sampler_path)
    adapter_tokenizer = adapter_client.get_tokenizer()

    overall_t0 = time.time()
    leg_times = {"A": 0.0, "B": 0.0, "C": 0.0}

    rows = []
    for i, (q, entry) in enumerate(queries, 1):
        logger.info("[%d/%d] %s %s", i, len(queries), entry["id"], q.query[:70])
        retrieved = _retrieve(q.query, k=args.k)
        logger.info(
            "  retrieved %d chunks, top_score=%.4f, docs=%s",
            retrieved.n, retrieved.top_score,
            ",".join(d[:8] for d in retrieved.docs_hit) if retrieved.docs_hit else "--",
        )

        # Leg A: RAG-only via Ollama
        sys_msg = _format_system_for_ollama(retrieved)
        t0 = time.time()
        try:
            text_a = _ollama_chat(sys_msg, q.query, model=args.ollama_model) if sys_msg else _ollama_chat("", q.query, model=args.ollama_model)
        except Exception as e:
            logger.warning("Ollama call failed: %s", e)
            text_a = f"(ollama error: {e})"
        leg_times["A"] += time.time() - t0
        score_a = _score(text_a, q)

        # Leg B: RAG + pilot adapter
        user_b = _format_user_for_adapter(q.query, retrieved)
        t0 = time.time()
        sample_b = _sample_adapter(
            adapter_client, adapter_tokenizer, user_b, args.max_new_tokens, args.temperature,
        )
        leg_times["B"] += time.time() - t0
        score_b = _score(sample_b.text, q)

        # Leg C: adapter-only with empty Context block
        user_c = _format_user_empty_context(q.query)
        t0 = time.time()
        sample_c = _sample_adapter(
            adapter_client, adapter_tokenizer, user_c, args.max_new_tokens, args.temperature,
        )
        leg_times["C"] += time.time() - t0
        score_c = _score(sample_c.text, q)

        rows.append({
            "entry": entry,
            "q": q,
            "retrieved": retrieved,
            "a_text": text_a, "a_score": score_a,
            "b_text": sample_b.text, "b_score": score_b,
            "c_text": sample_c.text, "c_score": score_c,
        })
        logger.info(
            "  %s A=%s B=%s C=%s",
            entry["id"],
            "PASS" if score_a["passed"] else "FAIL",
            "PASS" if score_b["passed"] else "FAIL",
            "PASS" if score_c["passed"] else "FAIL",
        )

    overall_secs = time.time() - overall_t0

    n = len(rows)
    pass_a = sum(1 for r in rows if r["a_score"]["passed"])
    pass_b = sum(1 for r in rows if r["b_score"]["passed"])
    pass_c = sum(1 for r in rows if r["c_score"]["passed"])

    ids_a = [r["entry"]["id"] for r in rows if r["a_score"]["passed"]]
    ids_b = [r["entry"]["id"] for r in rows if r["b_score"]["passed"]]
    ids_c = [r["entry"]["id"] for r in rows if r["c_score"]["passed"]]
    b_minus_a = [i for i in ids_b if i not in ids_a]
    a_minus_b = [i for i in ids_a if i not in ids_b]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("# Three-way pilot eval -- drive_archive-matched query set\n\n")
        f.write(f"Corpus: `{PROJECT}` (139,949 chunks, 2,924 docs)\n")
        f.write(f"Query set: `{args.eval_set.name}` (N={n} queries, sourced from real drive_archive chunks)\n")
        f.write(f"Pilot tinker_path: `{sampler_path}`\n")
        f.write(f"Retrieval: hybrid BM25+vector RRF (RAG_HYBRID_SEARCH=true), k={args.k}\n")
        f.write(f"Ollama: `{args.ollama_model}` via `{OLLAMA_URL}`\n")
        f.write(f"temperature={args.temperature}, max_new_tokens={args.max_new_tokens}\n")
        f.write(f"generated: {_dt.datetime.utcnow().isoformat()}Z\n")
        f.write(f"wall clock: {overall_secs:.1f}s (A={leg_times['A']:.1f}s, B={leg_times['B']:.1f}s, C={leg_times['C']:.1f}s)\n\n")

        f.write("## Summary\n\n")
        f.write("| # | ID | Category | Query (truncated) | RAG-only | RAG+pilot | adapter-only |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(rows, 1):
            e = r["entry"]
            q = r["q"]
            a = "PASS" if r["a_score"]["passed"] else "FAIL"
            b = "PASS" if r["b_score"]["passed"] else "FAIL"
            c = "PASS" if r["c_score"]["passed"] else "FAIL"
            q_short = q.query.replace("|", "\\|")[:80]
            f.write(f"| {i} | {e['id']} | {e['category']} | {q_short} | {a} | {b} | {c} |\n")

        f.write("\n## Totals\n\n")
        f.write(f"  RAG-only:     {pass_a} / {n} ({100*pass_a/n:.1f}%)\n")
        f.write(f"  RAG+pilot:    {pass_b} / {n} ({100*pass_b/n:.1f}%)\n")
        f.write(f"  adapter-only: {pass_c} / {n} ({100*pass_c/n:.1f}%)\n\n")

        f.write("## Pass-set analysis\n\n")
        f.write(f"  A passes (RAG-only):     {ids_a}\n")
        f.write(f"  B passes (RAG+pilot):    {ids_b}\n")
        f.write(f"  C passes (adapter-only): {ids_c}\n")
        f.write(f"  B - A (adapter lift over RAG): {b_minus_a}\n")
        f.write(f"  A - B (regression when adapter added on top of RAG): {a_minus_b}\n\n")

        zero_chunk = [r["entry"]["id"] for r in rows if r["retrieved"].n == 0]
        weak_top = [(r["entry"]["id"], r["retrieved"].top_score) for r in rows
                    if r["retrieved"].n > 0 and r["retrieved"].top_score < 0.4]
        f.write("## Retrieval flags\n\n")
        f.write(f"  Zero-chunk queries (legs A/B collapse to leg C shape): {zero_chunk if zero_chunk else 'none'}\n")
        f.write(f"  Low top_score (<0.4) queries: {weak_top if weak_top else 'none'}\n\n")

        f.write("---\n\n## Per-question detail\n\n")
        for i, r in enumerate(rows, 1):
            e = r["entry"]
            q = r["q"]
            ret = r["retrieved"]
            f.write(f"### Q{i} {e['id']} -- `{e['category']}`\n\n")
            f.write(f"**Query:** {q.query}\n\n")
            f.write(f"**Expected:** {q.expected}  **Forbidden:** {q.forbidden}\n\n")
            f.write(f"**Source chunk:** doc_id=`{e['source_doc_id']}` chunk={e['source_chunk_index']}\n\n")
            f.write(f"**Retrieved:** {ret.n} chunks, top_score={ret.top_score:.4f}\n\n")
            if ret.chunks:
                f.write("Top-3 retrieved (doc_id : chunk_index : score):\n")
                for c in ret.chunks[:3]:
                    f.write(f"- `{c.doc_id}` : {c.chunk_index} : {(c.score or 0):.4f}\n")
                f.write("\n")

            for leg_name, key in (("A. RAG-only", "a"), ("B. RAG+pilot", "b"), ("C. adapter-only", "c")):
                sc = r[f"{key}_score"]
                text = r[f"{key}_text"]
                f.write(f"#### {leg_name} -- **{'PASS' if sc['passed'] else 'FAIL'}**\n\n")
                f.write("```\n" + text[:300] + ("\n... (truncated)" if len(text) > 300 else "") + "\n```\n\n")
                f.write(f"- found: {sc['found_expected']}\n")
                f.write(f"- missing: {sc['missing_expected']}\n")
                f.write(f"- forbidden_hits: {sc['forbidden_hits']}\n\n")
            f.write("---\n\n")

    summary_json = {
        "rag_only_pct": round(100 * pass_a / n, 1),
        "rag_plus_pilot_pct": round(100 * pass_b / n, 1),
        "adapter_only_pct": round(100 * pass_c / n, 1),
        "n_queries": n,
        "b_minus_a_ids": b_minus_a,
        "a_minus_b_ids": a_minus_b,
    }
    logger.info(
        "totals: A=%d/%d B=%d/%d C=%d/%d wall=%.1fs",
        pass_a, n, pass_b, n, pass_c, n, overall_secs,
    )
    print(str(args.out))
    print(json.dumps(summary_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
