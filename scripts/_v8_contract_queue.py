"""Build v8 queue: contract-discipline chunks not yet covered, ordered round-robin
by source folder for balance. Target: close the gap toward the 9k cap.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict, deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from generate_scenarios_drive_archive_v2 import (  # noqa: E402
    classify_discipline,
    parse_source,
    source_folder,
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
OUT = os.path.join(ROOT, "data", "logs", "v8_contract_queue.jsonl")
TARGET_NEW = 2200  # ~50 over the gap to give workers room


def main() -> int:
    covered: set[tuple[str, int]] = set()
    for rel in SCENARIO_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                src = r.get("source", "")
                if not src.startswith("drive_archive:"):
                    continue
                tail = src.split("drive_archive:", 1)[1]
                if ":" in tail:
                    doc_id, cs = tail.rsplit(":", 1)
                    try:
                        covered.add((doc_id, int(cs)))
                    except ValueError:
                        pass
    print(f"[load] covered={len(covered)}", flush=True)

    db = os.path.join(ROOT, "data", "rag", "vectors.db")
    con = sqlite3.connect(db)
    cur = con.cursor()

    folder_q: dict[str, deque] = defaultdict(deque)
    for doc_id, chunk_idx, text in cur.execute(
        "SELECT doc_id, chunk_index, text FROM chunks WHERE text LIKE '[source:%'"
    ):
        src = parse_source(text or "")
        if not src.startswith("G:"):
            continue
        if classify_discipline(src) != "contract":
            continue
        if (doc_id, chunk_idx) in covered:
            continue
        folder = source_folder(src)
        folder_q[folder].append(
            {
                "doc_id": doc_id,
                "chunk_index": chunk_idx,
                "text": text,
                "source_path": src,
                "discipline": "contract",
            }
        )
    con.close()

    print(f"[scan] contract folders: {len(folder_q)}  total gap chunks: {sum(len(q) for q in folder_q.values())}", flush=True)
    keys = sorted(folder_q.keys())

    final: list[dict] = []
    while any(folder_q[k] for k in keys) and len(final) < TARGET_NEW:
        for k in keys:
            if folder_q[k]:
                final.append(folder_q[k].popleft())
                if len(final) >= TARGET_NEW:
                    break

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in final:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[done] queued {len(final)} -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
