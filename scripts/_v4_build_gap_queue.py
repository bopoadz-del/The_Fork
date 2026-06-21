"""Build a deterministic GAP queue for v4 training-scenario generation.

Reads:
  - data/rag/vectors.db (all indexed drive_archive chunks)
  - every existing training_scenarios_*.jsonl (to determine what's covered)

Writes:
  data/logs/v4_gap_queue.jsonl    (one row per uncovered chunk; round-robined
                                   across disciplines so head-of-queue is balanced)

Each row in the queue: {doc_id, chunk_index, text, source_path, discipline}.

CLI:
  --disciplines drawings,schedule    (default: drawings,schedule)
  --include-all                      (override and include every discipline)
"""
from __future__ import annotations

import argparse
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


SCENARIO_FILES = [
    "data/learning/training_scenarios_drive_archive.jsonl",
    "data/learning/training_scenarios_drive_archive_clean.jsonl",
    "data/learning/training_scenarios_drive_archive_v2.jsonl",
] + [f"data/learning/training_scenarios_v3_shard_{i:02d}.jsonl" for i in range(10)]


def load_covered() -> set[tuple[str, int]]:
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
    return covered


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--disciplines",
        default="drawings,schedule",
        help="Comma-separated disciplines to include.",
    )
    ap.add_argument(
        "--include-all",
        action="store_true",
        help="Override and include every discipline.",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(ROOT, "data", "logs", "v4_gap_queue.jsonl"),
    )
    args = ap.parse_args()

    target_disciplines = (
        None
        if args.include_all
        else {d.strip() for d in args.disciplines.split(",") if d.strip()}
    )
    if target_disciplines:
        print(f"[plan] targeting disciplines: {sorted(target_disciplines)}", flush=True)
    else:
        print("[plan] targeting ALL disciplines", flush=True)

    covered = load_covered()
    print(f"[load] {len(covered)} chunks already covered across all scenario files", flush=True)

    db = os.path.join(ROOT, "data", "rag", "vectors.db")
    con = sqlite3.connect(db)
    cur = con.cursor()

    # Bucket per discipline -> per source-folder -> list of (doc_id, chunk_idx, text, source_path)
    per_disc_folder: dict[str, dict[str, list[tuple[str, int, str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    scanned = 0
    kept = 0
    for doc_id, chunk_idx, text in cur.execute(
        "SELECT doc_id, chunk_index, text FROM chunks WHERE text LIKE '[source:%'"
    ):
        scanned += 1
        src = parse_source(text or "")
        if not src.startswith("G:"):
            continue
        disc = classify_discipline(src)
        if target_disciplines and disc not in target_disciplines:
            continue
        if (doc_id, chunk_idx) in covered:
            continue
        folder = source_folder(src)
        per_disc_folder[disc][folder].append((doc_id, chunk_idx, text, src))
        kept += 1
    con.close()
    print(f"[scan] {scanned} chunks scanned, {kept} gap-eligible kept", flush=True)

    for disc, folders in sorted(per_disc_folder.items()):
        n_total = sum(len(c) for c in folders.values())
        print(f"  {disc:<14} gap chunks: {n_total} across {len(folders)} folders", flush=True)

    # Within each discipline, round-robin across source-folders (balanced sampling).
    per_disc_ordered: dict[str, list[dict]] = {}
    for disc, folders in per_disc_folder.items():
        folder_keys = sorted(folders.keys())
        queues = {f: deque(sorted(folders[f], key=lambda x: (x[0], x[1]))) for f in folder_keys}
        flat: list[dict] = []
        while any(queues[f] for f in folder_keys):
            for f in folder_keys:
                if queues[f]:
                    doc_id, chunk_idx, text, src = queues[f].popleft()
                    flat.append(
                        {
                            "doc_id": doc_id,
                            "chunk_index": chunk_idx,
                            "text": text,
                            "source_path": src,
                            "discipline": disc,
                        }
                    )
        per_disc_ordered[disc] = flat

    # Cross-discipline round-robin to interleave the final queue.
    disc_keys = sorted(per_disc_ordered.keys())
    queues_disc = {d: deque(per_disc_ordered[d]) for d in disc_keys}
    final: list[dict] = []
    while any(queues_disc[d] for d in disc_keys):
        for d in disc_keys:
            if queues_disc[d]:
                final.append(queues_disc[d].popleft())

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for row in final:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        f"\n[done] queue written: {len(final)} rows -> {args.out}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
