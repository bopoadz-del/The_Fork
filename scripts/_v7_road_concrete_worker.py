"""v7 worker — reads from data/the_fork.db (production) for Road Works + Concrete chunks.

These chunks were added by the freshly re-run indexer with corrected keyword
filters. They live in the_fork.db (the unified schema production retrieves
from), not the older vectors.db that v4/v5/v6 read.

Usage:
    python scripts/_v7_road_concrete_worker.py --shard 0 --total 3
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from generate_scenarios_drive_archive_v2 import (  # noqa: E402
    call_ollama,
    classify_discipline,
    parse_source,
    parse_qa,
    PRIMARY_MODEL,
    FALLBACK_MODEL,
)

THE_FORK_DB = os.path.join(ROOT, "data", "the_fork.db")

# Doc IDs from the re-indexing — Road Works (3 files) + Concrete Problems (3 files).
# These will be enumerated dynamically by walking the_fork.db for source paths
# starting with the target prefixes.

TARGET_PATH_PREFIXES = [
    "G:\\My Drive\\Scaned Files - Road Works",
    "G:\\My Drive\\Scaned Files -Concrete Problems",
]


def fetch_target_chunks() -> list[dict]:
    """Pull (doc_id, chunk_index, text, source_path) for every chunk under
    either target path prefix from the_fork.db."""
    con = sqlite3.connect(THE_FORK_DB)
    cur = con.cursor()
    rows: list[dict] = []
    for prefix in TARGET_PATH_PREFIXES:
        # the_fork.db stores chunks with [source: ...] prefix in text, matching v2 format.
        like = f"[source: {prefix}%"
        for doc_id, chunk_idx, text in cur.execute(
            "SELECT doc_id, chunk_index, text FROM chunks WHERE text LIKE ?",
            (like,),
        ):
            src = parse_source(text or "")
            if not src.startswith("G:"):
                continue
            rows.append(
                {
                    "doc_id": doc_id,
                    "chunk_index": chunk_idx,
                    "text": text,
                    "source_path": src,
                    "discipline": classify_discipline(src),
                    "package": (
                        "Scaned Files - Road Works"
                        if "Road Works" in src
                        else "Scaned Files - Concrete Problems"
                    ),
                }
            )
    con.close()
    rows.sort(key=lambda r: (r["doc_id"], r["chunk_index"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--total", type=int, default=3)
    ap.add_argument("--target", type=int, default=400)
    ap.add_argument("--budget-sec", type=int, default=3600)
    args = ap.parse_args()

    shard = args.shard
    total = args.total
    out_path = os.path.join(
        ROOT, "data", "learning", f"training_scenarios_v7_shard_{shard:02d}.jsonl"
    )
    log_prefix = f"[v7 shard {shard:02d}]"

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # Resume from existing output
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
    print(f"{log_prefix} starting, {len(done_keys)} already done", flush=True)

    all_rows = fetch_target_chunks()
    print(f"{log_prefix} total target chunks across both packages: {len(all_rows)}", flush=True)

    my_items = [r for i, r in enumerate(all_rows) if i % total == shard]
    print(f"{log_prefix} my partition: {len(my_items)}", flush=True)

    written = 0
    model = PRIMARY_MODEL
    primary_failures = 0
    dropped_parse = 0
    dropped_qual = 0
    t0 = time.time()
    per_pkg: dict[str, int] = {}

    for c in my_items:
        elapsed = time.time() - t0
        if elapsed > args.budget_sec:
            print(f"{log_prefix} budget hit -- stopping", flush=True)
            break
        if written >= args.target:
            print(f"{log_prefix} target hit -- stopping", flush=True)
            break

        key = f"{c['doc_id']}:{c['chunk_index']}"
        if key in done_keys:
            continue

        raw = call_ollama(model, c["text"], c["source_path"])
        if raw is None:
            primary_failures += 1
            if model == PRIMARY_MODEL and primary_failures >= 5:
                print(f"{log_prefix} switching to {FALLBACK_MODEL}", flush=True)
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
            "package": c["package"],
            "shard": shard,
            "from_db": "the_fork.db",
        }
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        done_keys.add(key)
        written += 1
        per_pkg[c["package"]] = per_pkg.get(c["package"], 0) + 1

        if written % 20 == 0:
            elapsed = time.time() - t0
            rate = written / max(1.0, elapsed)
            print(
                f"{log_prefix} {written} written | {rate:.2f} rows/s | "
                f"elapsed={int(elapsed)}s | per_pkg={per_pkg}",
                flush=True,
            )

    elapsed = time.time() - t0
    print(
        f"{log_prefix} DONE | written={written} | dropped_parse={dropped_parse} | "
        f"dropped_qual={dropped_qual} | elapsed={int(elapsed)}s | per_pkg={per_pkg}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
