"""Build v5 queue honoring per-discipline and per-package targets.

User's targets (2026-06-15):
  Discipline:
    contract: cap 9000 (absolute)
    report:   50% of indexed (= ~3,377)
    hse:      50% (~949)
    spec:     50% (~406)
  Package (50% of each):
    Ha Long Xanh
    Hon Mot Island RFP
    Demo Contract
  Skip: mep, lighting, structural, roads, other, drawings, schedule
    (drawings + schedule are handled by v4)

Rules:
  - A chunk is "wanted" if its discipline is wanted OR its package is wanted
    (excluding chunks with skip-disciplines from the package targets).
  - A chunk counts toward ALL applicable buckets when added (discipline + package).
  - A chunk is SKIPPED at queue-build time if any applicable bucket would exceed cap.
  - Already-covered chunks are pre-counted against the cap (so we don't over-fill).

Output:
  data/logs/v5_targets_queue.jsonl  -- one row per planned chunk, round-robined.
"""
from __future__ import annotations

import json
import os
import re
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

# Inputs / outputs
SCENARIO_FILES = [
    "data/learning/training_scenarios_drive_archive.jsonl",
    "data/learning/training_scenarios_drive_archive_clean.jsonl",
    "data/learning/training_scenarios_drive_archive_v2.jsonl",
] + [f"data/learning/training_scenarios_v3_shard_{i:02d}.jsonl" for i in range(10)] + [
    f"data/learning/training_scenarios_v4_shard_{i:02d}.jsonl" for i in range(10)
]
OUT_QUEUE = os.path.join(ROOT, "data", "logs", "v5_targets_queue.jsonl")

# Targets — discipline (cap is absolute count)
DISCIPLINE_TARGETS_CAP: dict[str, int] = {"contract": 9000}
DISCIPLINE_TARGETS_PCT: dict[str, float] = {
    "report": 0.50,
    "hse": 0.50,
    "spec": 0.50,
}
# Targets — package (percentage of indexed chunks for that package)
PACKAGE_TARGETS_PCT: dict[str, float] = {
    "Ha Long Xanh": 0.50,
    "Hon Mot Island": 0.50,  # prefix match
    "Demo Contract": 0.50,
}
# Disciplines explicitly skipped — for both discipline and package targets
SKIP_DISCIPLINES = {"mep", "lighting", "structural", "roads", "other", "drawings", "schedule"}


def package_of(source_path: str) -> str:
    src = source_path.replace("\\", "/")
    m = re.search(r"My Drive/([^/]+)(?:/([^/]+))?", src)
    if not m:
        return "<unknown>"
    first = m.group(1)
    second = m.group(2) or ""
    if first.lower() == "master folder" and second:
        return second
    return first


def match_target_package(pkg: str) -> str | None:
    for target in PACKAGE_TARGETS_PCT:
        if pkg.lower().startswith(target.lower()):
            return target
    return None


def main() -> int:
    print("[1] loading covered chunks (with package + discipline)...", flush=True)
    covered: dict[tuple[str, int], tuple[str, str]] = {}
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
                    cidx = int(chunk_str)
                except ValueError:
                    continue
                pkg = package_of(r.get("source_doc_path") or "")
                disc = r.get("discipline") or "other"
                covered[(doc_id, cidx)] = (pkg, disc)
    print(f"    {len(covered)} covered chunks loaded", flush=True)

    print("[2] scanning indexed corpus + computing caps...", flush=True)
    db = os.path.join(ROOT, "data", "rag", "vectors.db")
    con = sqlite3.connect(db)
    cur = con.cursor()

    # Bucket totals from indexed corpus
    indexed_by_disc: dict[str, int] = defaultdict(int)
    indexed_by_pkg: dict[str, int] = defaultdict(int)
    candidate_chunks: dict[tuple[str, int], dict] = {}  # only "wanted" + uncovered

    for doc_id, chunk_idx, text in cur.execute(
        "SELECT doc_id, chunk_index, text FROM chunks WHERE text LIKE '[source:%'"
    ):
        src_path = parse_source(text or "")
        if not src_path.startswith("G:"):
            continue
        disc = classify_discipline(src_path)
        pkg = package_of(src_path)
        indexed_by_disc[disc] += 1
        indexed_by_pkg[pkg] += 1

        if (doc_id, chunk_idx) in covered:
            continue

        # Is this chunk wanted?
        disc_wanted = (disc in DISCIPLINE_TARGETS_CAP) or (disc in DISCIPLINE_TARGETS_PCT)
        pkg_target = match_target_package(pkg)
        # Within the named target packages, take EVERY chunk (the user picked these
        # packages explicitly for diversity; the discipline skip list does not
        # apply inside them).  Outside those packages, the skip list still applies.
        pkg_wanted = pkg_target is not None

        if not (disc_wanted or pkg_wanted):
            continue

        candidate_chunks[(doc_id, chunk_idx)] = {
            "doc_id": doc_id,
            "chunk_index": chunk_idx,
            "text": text,
            "source_path": src_path,
            "discipline": disc,
            "package": pkg,
            "pkg_target": pkg_target,
        }
    con.close()
    print(f"    candidate (wanted + uncovered) chunks: {len(candidate_chunks)}", flush=True)

    # Compute absolute caps
    caps_disc: dict[str, int] = {}
    for d, cap in DISCIPLINE_TARGETS_CAP.items():
        caps_disc[d] = cap
    for d, pct in DISCIPLINE_TARGETS_PCT.items():
        caps_disc[d] = int(indexed_by_disc.get(d, 0) * pct)
    caps_pkg: dict[str, int] = {}
    for ptarget, pct in PACKAGE_TARGETS_PCT.items():
        # Find matching package name(s) in indexed_by_pkg
        for actual_pkg, n in indexed_by_pkg.items():
            if actual_pkg.lower().startswith(ptarget.lower()):
                caps_pkg[ptarget] = int(n * pct)
                break

    # Pre-count covered against caps
    used_disc: dict[str, int] = defaultdict(int)
    used_pkg: dict[str, int] = defaultdict(int)
    for _, (pkg, disc) in covered.items():
        if disc in caps_disc:
            used_disc[disc] += 1
        pt = match_target_package(pkg)
        if pt:
            used_pkg[pt] += 1

    print("\n[3] caps + pre-used:", flush=True)
    print(f"  {'bucket':<25} {'cap':>6} {'used':>6} {'avail':>6}", flush=True)
    for d, cap in caps_disc.items():
        used = used_disc[d]
        print(f"  disc::{d:<19} {cap:6d} {used:6d} {cap-used:6d}", flush=True)
    for pt, cap in caps_pkg.items():
        used = used_pkg[pt]
        print(f"  pkg::{pt:<20} {cap:6d} {used:6d} {cap-used:6d}", flush=True)

    # Build queue: round-robin across bucket queues
    print("\n[4] building queue, enforcing caps...", flush=True)

    # Build per-bucket candidate queues
    by_bucket: dict[str, deque] = defaultdict(deque)
    for k, c in candidate_chunks.items():
        # Each chunk goes into ONE bucket (priority: package target if applicable, else discipline)
        # But it COUNTS against all matching buckets at write time.
        if c["pkg_target"]:
            by_bucket[f"pkg::{c['pkg_target']}"].append(c)
        elif c["discipline"] in caps_disc:
            by_bucket[f"disc::{c['discipline']}"].append(c)

    print(f"  bucket queues built: {len(by_bucket)}", flush=True)
    for b, q in sorted(by_bucket.items()):
        print(f"    {b}: {len(q)} candidates", flush=True)

    final: list[dict] = []
    written_disc: dict[str, int] = defaultdict(int)
    written_pkg: dict[str, int] = defaultdict(int)
    bucket_keys = sorted(by_bucket.keys())

    while any(by_bucket[b] for b in bucket_keys):
        progressed = False
        for b in bucket_keys:
            if not by_bucket[b]:
                continue
            # Look at head
            c = by_bucket[b][0]
            disc = c["discipline"]
            pt = c["pkg_target"]

            # Check caps. used_disc and used_pkg are running counters (covered + written).
            if disc in caps_disc:
                if (used_disc[disc] + written_disc[disc]) >= caps_disc[disc]:
                    by_bucket[b].popleft()
                    continue
            if pt and pt in caps_pkg:
                if (used_pkg[pt] + written_pkg[pt]) >= caps_pkg[pt]:
                    by_bucket[b].popleft()
                    continue

            # Accept
            by_bucket[b].popleft()
            final.append(c)
            if disc in caps_disc:
                written_disc[disc] += 1
            if pt:
                written_pkg[pt] += 1
            progressed = True
        if not progressed:
            break

    # Write
    os.makedirs(os.path.dirname(OUT_QUEUE), exist_ok=True)
    with open(OUT_QUEUE, "w", encoding="utf-8") as f:
        for c in final:
            f.write(
                json.dumps(
                    {
                        "doc_id": c["doc_id"],
                        "chunk_index": c["chunk_index"],
                        "text": c["text"],
                        "source_path": c["source_path"],
                        "discipline": c["discipline"],
                        "package": c["package"],
                        "pkg_target": c["pkg_target"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"\n[done] queue: {len(final)} rows -> {OUT_QUEUE}", flush=True)
    print("  per-bucket NEW rows planned:", flush=True)
    for d in sorted(set(list(caps_disc.keys()) + list(written_disc.keys()))):
        print(f"    disc::{d:<14} {written_disc[d]}", flush=True)
    for pt in sorted(caps_pkg.keys()):
        print(f"    pkg::{pt:<14} {written_pkg[pt]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
