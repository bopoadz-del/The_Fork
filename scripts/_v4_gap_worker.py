"""v4 gap worker — generates training scenarios for the chunks NOT yet covered.

Reads pre-built gap queue (data/logs/v4_gap_queue.jsonl), takes the rows where
`idx % total == shard`, generates Q&A via Ollama, writes shard-specific JSONL.

Operator hard rule: every LLM call routes through local Ollama
(127.0.0.1:11434) -- NOT DeepSeek/Anthropic/Groq. Primary model
qwen3-coder:480b-cloud, fallback qwen2.5:7b-instruct.

Usage:
    python scripts/_v4_gap_worker.py --shard 0 --total 10 \
        [--target 1500] [--budget-sec 14400]
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
    call_ollama,
    parse_qa,
    PRIMARY_MODEL,
    FALLBACK_MODEL,
)

DEFAULT_QUEUE = os.path.join(ROOT, "data", "logs", "v4_gap_queue.jsonl")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--total", type=int, default=10)
    ap.add_argument("--target", type=int, default=1500, help="max rows this shard")
    ap.add_argument("--budget-sec", type=int, default=14400, help="wall-clock cap")
    ap.add_argument(
        "--queue",
        default=DEFAULT_QUEUE,
        help="Queue JSONL to read (default: v4_gap_queue.jsonl)",
    )
    ap.add_argument(
        "--tag",
        default="v4",
        help="Output file tag (e.g. v4 or v5)",
    )
    args = ap.parse_args()

    shard = args.shard
    total = args.total
    tag = args.tag
    out_path = os.path.join(
        ROOT, "data", "learning", f"training_scenarios_{tag}_shard_{shard:02d}.jsonl"
    )
    state_path = os.path.join(
        ROOT, "data", "logs", f"scenario_gen_state_{tag}_shard_{shard:02d}.json"
    )
    log_prefix = f"[{tag} shard {shard:02d}]"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)

    # Build done_keys from THIS shard's existing output (resume support).
    done_keys: set[str] = set()
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    src = r.get("source", "")
                    if src.startswith("drive_archive:"):
                        done_keys.add(src.split("drive_archive:", 1)[1])
                except Exception:
                    pass
    print(f"{log_prefix} starting, {len(done_keys)} already in this shard's output", flush=True)

    # Read gap queue, take only my partition.
    queue_path = args.queue
    if not os.path.exists(queue_path):
        print(f"{log_prefix} FATAL: queue not found at {queue_path}", flush=True)
        return 1
    my_items: list[dict] = []
    with open(queue_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i % total != shard:
                continue
            try:
                my_items.append(json.loads(line))
            except Exception:
                pass
    print(f"{log_prefix} partition size: {len(my_items)}", flush=True)

    written = 0
    primary_failures = 0
    model = PRIMARY_MODEL
    dropped_parse = 0
    dropped_qual = 0
    t0 = time.time()
    per_disc_written: dict[str, int] = {}

    for queue_idx, c in enumerate(my_items):
        elapsed = time.time() - t0
        if elapsed > args.budget_sec:
            print(
                f"{log_prefix} budget hit ({args.budget_sec}s) -- stopping at written={written}",
                flush=True,
            )
            break
        if written >= args.target:
            print(f"{log_prefix} target hit ({args.target}) -- stopping", flush=True)
            break

        key = f"{c['doc_id']}:{c['chunk_index']}"
        if key in done_keys:
            continue

        raw = call_ollama(model, c["text"], c["source_path"] or c["doc_id"])
        if raw is None:
            primary_failures += 1
            if model == PRIMARY_MODEL and primary_failures >= 5:
                print(
                    f"{log_prefix} switching to {FALLBACK_MODEL} after 5 primary failures",
                    flush=True,
                )
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
            "gap": True,
        }
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        done_keys.add(key)
        written += 1
        per_disc_written[c["discipline"]] = per_disc_written.get(c["discipline"], 0) + 1

        if written % 25 == 0:
            elapsed = time.time() - t0
            rate = written / max(1.0, elapsed)
            print(
                f"{log_prefix} {written}/{args.target} written | "
                f"queue_idx={queue_idx} | {rate:.2f} rows/s | "
                f"elapsed={int(elapsed)}s | per_disc={per_disc_written}",
                flush=True,
            )
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "written": written,
                        "elapsed": int(elapsed),
                        "model": model,
                        "per_disc": per_disc_written,
                    },
                    f,
                )

    elapsed = time.time() - t0
    print(
        f"{log_prefix} DONE | written={written} | dropped_parse={dropped_parse} | "
        f"dropped_qual={dropped_qual} | elapsed={int(elapsed)}s | "
        f"rate={written/max(1.0,elapsed):.2f} rows/s | model_at_end={model} | "
        f"per_disc={per_disc_written}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
