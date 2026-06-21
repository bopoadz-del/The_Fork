"""Sharded worker for v3 training-scenario generation.

Usage:
    python scripts/_v3_sharded_worker.py --shard 0 --total 10 [--target 900] [--budget-sec 5400]

Each worker:
  - Reuses v2's `build_processing_order()` to build the balanced queue (~9.2k chunks).
  - Takes only items where `idx % total == shard`.
  - Skips chunks already present in v2 jsonl OR in this shard's own output.
  - Hits 127.0.0.1:11434 with `qwen3-coder:480b-cloud` (operator hard rule: Ollama only,
    NO DeepSeek/Anthropic/Groq).
  - Writes shard-specific JSONL + state JSON so 10 workers can run in parallel without colliding.

Output:
    data/learning/training_scenarios_v3_shard_<NN>.jsonl
    data/logs/scenario_gen_state_v3_shard_<NN>.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from generate_scenarios_drive_archive_v2 import (  # noqa: E402
    build_processing_order,
    call_ollama,
    parse_qa,
    PRIMARY_MODEL,
    FALLBACK_MODEL,
)

V2_JSONL = os.path.join(ROOT, "data", "learning", "training_scenarios_drive_archive_v2.jsonl")


def load_done_keys(paths):
    done = set()
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    src = r.get("source", "")
                    if src.startswith("drive_archive:"):
                        done.add(src.split("drive_archive:", 1)[1])
                except Exception:
                    pass
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--total", type=int, default=10)
    ap.add_argument("--target", type=int, default=900, help="max rows for this shard")
    ap.add_argument("--budget-sec", type=int, default=5400, help="wall-clock cap for this shard")
    args = ap.parse_args()

    shard = args.shard
    total = args.total
    out_path = os.path.join(ROOT, "data", "learning", f"training_scenarios_v3_shard_{shard:02d}.jsonl")
    state_path = os.path.join(ROOT, "data", "logs", f"scenario_gen_state_v3_shard_{shard:02d}.json")
    log_prefix = f"[shard {shard:02d}]"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)

    print(f"{log_prefix} starting (out={out_path})", flush=True)

    done_keys = load_done_keys([V2_JSONL, out_path])
    print(f"{log_prefix} {len(done_keys)} keys already done across v2 + own shard", flush=True)

    samples = build_processing_order()
    my_items = [(i, c) for i, c in enumerate(samples) if i % total == shard]
    print(f"{log_prefix} my partition size: {len(my_items)} / {len(samples)} total", flush=True)

    written = 0
    primary_failures = 0
    model = PRIMARY_MODEL
    dropped_parse = 0
    dropped_qual = 0
    t0 = time.time()

    for queue_idx, c in my_items:
        elapsed = time.time() - t0
        if elapsed > args.budget_sec:
            print(f"{log_prefix} budget hit ({args.budget_sec}s) — stopping at written={written}", flush=True)
            break
        if written >= args.target:
            print(f"{log_prefix} target hit ({args.target}) — stopping", flush=True)
            break

        key = f"{c['doc_id']}:{c['chunk_index']}"
        if key in done_keys:
            continue

        raw = call_ollama(model, c["text"], c["source_path"] or c["doc_id"])
        if raw is None:
            primary_failures += 1
            if model == PRIMARY_MODEL and primary_failures >= 5:
                print(f"{log_prefix} switching to {FALLBACK_MODEL} after 5 primary failures", flush=True)
                model = FALLBACK_MODEL
            continue
        primary_failures = 0

        qa = parse_qa(raw)
        if qa is None:
            if raw and ("{" in raw or "instruction" in raw.lower()):
                dropped_qual += 1
            else:
                dropped_parse += 1
            continue

        row = {
            "instruction": qa["instruction"],
            "context": c["text"][:600],
            "response": qa["response"],
            "source": f"drive_archive:{c['doc_id']}:{c['chunk_index']}",
            "source_doc_path": c["source_path"],
            "discipline": c["discipline"],
            "shard": shard,
        }
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        done_keys.add(key)
        written += 1

        if written % 25 == 0:
            elapsed = time.time() - t0
            rate = written / max(1.0, elapsed)
            print(
                f"{log_prefix} {written}/{args.target} written | "
                f"queue_idx={queue_idx} | {rate:.2f} rows/s | elapsed={int(elapsed)}s",
                flush=True,
            )
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump({"written": written, "elapsed": int(elapsed), "model": model}, f)

    elapsed = time.time() - t0
    print(
        f"{log_prefix} DONE | written={written} | dropped_parse={dropped_parse} | "
        f"dropped_qual={dropped_qual} | elapsed={int(elapsed)}s | "
        f"rate={written/max(1.0,elapsed):.2f} rows/s | model_at_end={model}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
