"""Consolidate every training_scenarios_* JSONL file into one deduplicated
training file at data/learning/training_scenarios.jsonl (the Tinker driver's
default input path).

Dedup key = (doc_id, chunk_index, instruction[:200]). The same chunk getting two
different Q&A pairs (one in v3, one in v5) counts as TWO rows. The same row
duplicated literally counts as ONE.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCES = (
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
OUT = "data/learning/training_scenarios.jsonl"


def main() -> int:
    seen: set[tuple[str, int, str]] = set()
    out_path = os.path.join(ROOT, OUT)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    n_in = 0
    n_out = 0
    n_skipped_dup = 0
    n_skipped_bad = 0
    per_source: dict[str, int] = {}
    with open(out_path, "w", encoding="utf-8") as g:
        for rel in SOURCES:
            p = os.path.join(ROOT, rel)
            if not os.path.exists(p):
                continue
            per_source[rel] = 0
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    n_in += 1
                    try:
                        row = json.loads(line)
                    except Exception:
                        n_skipped_bad += 1
                        continue
                    instr = (row.get("instruction") or "").strip()
                    resp = (row.get("response") or "").strip()
                    if not instr or not resp:
                        n_skipped_bad += 1
                        continue
                    src = row.get("source") or ""
                    if src.startswith("drive_archive:"):
                        tail = src.split("drive_archive:", 1)[1]
                        if ":" in tail:
                            doc_id, chunk_str = tail.rsplit(":", 1)
                            try:
                                cidx = int(chunk_str)
                            except ValueError:
                                cidx = -1
                        else:
                            doc_id, cidx = tail, -1
                    else:
                        doc_id, cidx = src, -1
                    key = (doc_id, cidx, instr[:200])
                    if key in seen:
                        n_skipped_dup += 1
                        continue
                    seen.add(key)
                    # Keep only the standard fields the Tinker driver expects
                    g.write(
                        json.dumps(
                            {
                                "instruction": instr,
                                "context": (row.get("context") or "").strip(),
                                "response": resp,
                                "source": src,
                                "discipline": row.get("discipline"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    n_out += 1
                    per_source[rel] += 1

    print(f"[consolidate] read {n_in} rows from {len(per_source)} files")
    print(f"[consolidate] wrote {n_out} unique rows -> {OUT}")
    print(f"[consolidate] skipped {n_skipped_dup} duplicates, {n_skipped_bad} malformed")
    print("\nPer-file kept counts:")
    for r, n in per_source.items():
        if n:
            print(f"  {n:>5}  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
