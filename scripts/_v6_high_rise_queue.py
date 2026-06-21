"""Build v6 queue: 100% of 'Scaned Files - High Rise Building' package.

Take every chunk in that package that isn't already covered by any prior
scenario file.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from generate_scenarios_drive_archive_v2 import classify_discipline, parse_source  # noqa: E402

PACKAGE = "Scaned Files - High Rise Building"
SCENARIO_FILES = [
    "data/learning/training_scenarios_drive_archive.jsonl",
    "data/learning/training_scenarios_drive_archive_clean.jsonl",
    "data/learning/training_scenarios_drive_archive_v2.jsonl",
] + [f"data/learning/training_scenarios_v3_shard_{i:02d}.jsonl" for i in range(10)] + [
    f"data/learning/training_scenarios_v4_shard_{i:02d}.jsonl" for i in range(10)
] + [
    f"data/learning/training_scenarios_v5_shard_{i:02d}.jsonl" for i in range(10)
]
OUT_QUEUE = os.path.join(ROOT, "data", "logs", "v6_high_rise_queue.jsonl")


def package_of(source_path: str) -> str:
    src = source_path.replace("\\", "/")
    m = re.search(r"My Drive/([^/]+)", src)
    return m.group(1) if m else "<unknown>"


def main() -> int:
    covered: set[tuple[str, int]] = set()
    for rel in SCENARIO_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
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
                    covered.add((doc_id, int(chunk_str)))
                except ValueError:
                    pass
    print(f"[load] {len(covered)} covered keys", flush=True)

    db = os.path.join(ROOT, "data", "rag", "vectors.db")
    con = sqlite3.connect(db)
    cur = con.cursor()

    queue: list[dict] = []
    for doc_id, chunk_idx, text in cur.execute(
        "SELECT doc_id, chunk_index, text FROM chunks WHERE text LIKE '[source:%'"
    ):
        src = parse_source(text or "")
        if not src.startswith("G:"):
            continue
        if package_of(src) != PACKAGE:
            continue
        if (doc_id, chunk_idx) in covered:
            continue
        disc = classify_discipline(src)
        queue.append(
            {
                "doc_id": doc_id,
                "chunk_index": chunk_idx,
                "text": text,
                "source_path": src,
                "discipline": disc,
                "package": PACKAGE,
                "pkg_target": "High Rise Building",
            }
        )
    con.close()

    queue.sort(key=lambda r: (r["doc_id"], r["chunk_index"]))
    os.makedirs(os.path.dirname(OUT_QUEUE), exist_ok=True)
    with open(OUT_QUEUE, "w", encoding="utf-8") as f:
        for r in queue:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[done] {len(queue)} rows -> {OUT_QUEUE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
