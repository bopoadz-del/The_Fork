"""Audit: scenarios coverage vs indexed corpus, per discipline.

Reads the indexed vector DB to count chunks per discipline (using the same
classify_discipline logic as the v2 generator), then walks every existing
training scenarios JSONL to count chunks ACTUALLY covered per discipline.

Reports the gap so we know what to generate.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from generate_scenarios_drive_archive_v2 import (  # noqa: E402
    classify_discipline,
    parse_source,
)


SCENARIO_FILES = (
    [
        "data/learning/training_scenarios_drive_archive.jsonl",
        "data/learning/training_scenarios_drive_archive_clean.jsonl",
        "data/learning/training_scenarios_drive_archive_v2.jsonl",
    ]
    + [f"data/learning/training_scenarios_v3_shard_{i:02d}.jsonl" for i in range(10)]
    + [f"data/learning/training_scenarios_v4_shard_{i:02d}.jsonl" for i in range(10)]
    + [f"data/learning/training_scenarios_v5_shard_{i:02d}.jsonl" for i in range(10)]
    + [f"data/learning/training_scenarios_v6_shard_{i:02d}.jsonl" for i in range(10)]
    + [f"data/learning/training_scenarios_v7_shard_{i:02d}.jsonl" for i in range(10)]
)


def main() -> int:
    db = os.path.join(ROOT, "data", "rag", "vectors.db")
    con = sqlite3.connect(db)
    cur = con.cursor()

    # Per-discipline indexed counts
    print("[1] counting indexed chunks per discipline...", flush=True)
    indexed: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for doc_id, chunk_index, text in cur.execute(
        "SELECT doc_id, chunk_index, text FROM chunks WHERE text LIKE '[source:%'"
    ):
        src = parse_source(text or "")
        if not src.startswith("G:"):
            continue
        disc = classify_discipline(src)
        indexed[disc].add((doc_id, chunk_index))
    con.close()

    indexed_total = sum(len(v) for v in indexed.values())
    print(f"    indexed chunks (drive_archive): {indexed_total}", flush=True)

    # Per-discipline covered counts (chunks already in any training scenario)
    print("[2] counting covered chunks per discipline (across all scenario files)...", flush=True)
    covered_by_disc: dict[str, set[tuple[str, int]]] = defaultdict(set)
    total_rows = 0
    seen_keys: set[tuple[str, int]] = set()
    for rel in SCENARIO_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        rows_here = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                src = r.get("source", "")
                if not src.startswith("drive_archive:"):
                    continue
                tail = src.split("drive_archive:", 1)[1]
                if ":" not in tail:
                    continue
                doc_id, chunk_str = tail.rsplit(":", 1)
                try:
                    chunk_idx = int(chunk_str)
                except ValueError:
                    continue
                key = (doc_id, chunk_idx)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                disc = r.get("discipline") or "other"
                covered_by_disc[disc].add(key)
                rows_here += 1
        total_rows += rows_here
        print(f"    {rel}: {rows_here} chunk-unique rows", flush=True)

    covered_total = sum(len(v) for v in covered_by_disc.values())
    print(f"    total unique covered chunks: {covered_total} (from {total_rows} total rows)", flush=True)

    # Gap report
    print("\n[3] PER-DISCIPLINE COVERAGE GAP\n", flush=True)
    print(
        f"  {'discipline':<14} {'indexed':>9} {'covered':>9} {'gap':>9} {'%cov':>7}",
        flush=True,
    )
    print(f"  {'-'*14} {'-'*9} {'-'*9} {'-'*9} {'-'*7}", flush=True)

    rows = []
    for disc in sorted(set(indexed.keys()) | set(covered_by_disc.keys())):
        n_idx = len(indexed.get(disc, set()))
        n_cov = len(covered_by_disc.get(disc, set()))
        gap = n_idx - n_cov
        pct = (n_cov / n_idx * 100.0) if n_idx else 0.0
        rows.append((disc, n_idx, n_cov, gap, pct))

    for disc, n_idx, n_cov, gap, pct in sorted(rows, key=lambda r: -r[3]):
        print(f"  {disc:<14} {n_idx:9d} {n_cov:9d} {gap:9d} {pct:6.1f}%", flush=True)

    grand_gap = sum(r[3] for r in rows)
    print(f"\n  TOTAL GAP: {grand_gap} chunks indexed but never used for training", flush=True)
    print(
        f"  Indexed: {indexed_total}  Covered: {covered_total}  "
        f"Coverage: {covered_total/indexed_total*100:.1f}%",
        flush=True,
    )

    # Save the gap as JSON
    out = os.path.join(ROOT, "data", "logs", "scenario_coverage_audit.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "indexed_total": indexed_total,
                "covered_total": covered_total,
                "per_discipline": [
                    {"discipline": d, "indexed": ni, "covered": nc, "gap": g, "pct": p}
                    for d, ni, nc, g, p in rows
                ],
            },
            f,
            indent=2,
        )
    print(f"\n  audit saved -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
