"""Per-PACKAGE gap audit (in addition to per-discipline).

A "package" here = the top-level project folder under G:\My Drive (e.g.,
"DG2 Infra Pack 1", "DG2 Infra Pack 2", "Master Folder", etc.).

Prints a matrix: rows = packages, columns = disciplines, cell values = (covered/indexed).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from generate_scenarios_drive_archive_v2 import classify_discipline, parse_source  # noqa: E402


SCENARIO_FILES = [
    "data/learning/training_scenarios_drive_archive.jsonl",
    "data/learning/training_scenarios_drive_archive_clean.jsonl",
    "data/learning/training_scenarios_drive_archive_v2.jsonl",
] + [f"data/learning/training_scenarios_v3_shard_{i:02d}.jsonl" for i in range(10)] + [
    f"data/learning/training_scenarios_v4_shard_{i:02d}.jsonl" for i in range(10)
]


def package_of(source_path: str) -> str:
    """
    Extract the top-level package name from a Drive path like
    'G:\\My Drive\\Master Folder\\DG2 Infra Pack 1\\Design\\drawings\\foo.pdf'.

    Strategy: take the first folder under 'My Drive' that's NOT 'Master Folder'.
    Falls back to the first folder under 'My Drive' if 'Master Folder' isn't there.
    """
    src = source_path.replace("\\", "/")
    # Match 'G:/My Drive/<something>'
    m = re.search(r"My Drive/([^/]+)(?:/([^/]+))?", src)
    if not m:
        return "<unknown>"
    first = m.group(1)
    second = m.group(2) or ""
    if first.lower() == "master folder" and second:
        return second
    return first


def load_covered_with_pkg() -> dict[tuple[str, int], tuple[str, str]]:
    """key=(doc_id, chunk_idx) -> (package, discipline)"""
    cov: dict[tuple[str, int], tuple[str, str]] = {}
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
                cov[(doc_id, cidx)] = (pkg, disc)
    return cov


def main() -> int:
    print("[1] scanning indexed chunks for package + discipline...", flush=True)
    db = os.path.join(ROOT, "data", "rag", "vectors.db")
    con = sqlite3.connect(db)
    cur = con.cursor()

    indexed_by_pkg_disc: dict[tuple[str, str], int] = defaultdict(int)
    indexed_by_pkg: dict[str, int] = defaultdict(int)
    indexed_chunk_keys: dict[tuple[str, int], tuple[str, str]] = {}
    for doc_id, chunk_idx, text in cur.execute(
        "SELECT doc_id, chunk_index, text FROM chunks WHERE text LIKE '[source:%'"
    ):
        src = parse_source(text or "")
        if not src.startswith("G:"):
            continue
        pkg = package_of(src)
        disc = classify_discipline(src)
        indexed_by_pkg_disc[(pkg, disc)] += 1
        indexed_by_pkg[pkg] += 1
        indexed_chunk_keys[(doc_id, chunk_idx)] = (pkg, disc)
    con.close()
    print(f"    indexed chunks: {sum(indexed_by_pkg.values())}", flush=True)
    print(f"    packages: {len(indexed_by_pkg)}", flush=True)

    print("\n[2] reading covered chunks from training scenarios...", flush=True)
    covered = load_covered_with_pkg()
    print(f"    covered chunks: {len(covered)}", flush=True)

    covered_by_pkg_disc: dict[tuple[str, str], int] = defaultdict(int)
    covered_by_pkg: dict[str, int] = defaultdict(int)
    for key, (pkg, disc) in covered.items():
        if key not in indexed_chunk_keys:
            continue  # covered chunks no longer in index
        # Use indexed pkg/disc for consistency
        real_pkg, real_disc = indexed_chunk_keys[key]
        covered_by_pkg_disc[(real_pkg, real_disc)] += 1
        covered_by_pkg[real_pkg] += 1

    # PER-PACKAGE summary
    print("\n[3] PER-PACKAGE coverage gap\n", flush=True)
    print(
        f"  {'package':<35} {'indexed':>9} {'covered':>9} {'gap':>9} {'%cov':>7}",
        flush=True,
    )
    print(f"  {'-'*35} {'-'*9} {'-'*9} {'-'*9} {'-'*7}", flush=True)
    pkg_rows = []
    for pkg, n_idx in sorted(indexed_by_pkg.items(), key=lambda kv: -kv[1]):
        n_cov = covered_by_pkg.get(pkg, 0)
        pkg_rows.append((pkg, n_idx, n_cov, n_idx - n_cov, n_cov / n_idx * 100.0))
    for pkg, n_idx, n_cov, gap, pct in pkg_rows:
        pkg_short = pkg if len(pkg) <= 35 else pkg[:32] + "..."
        print(f"  {pkg_short:<35} {n_idx:9d} {n_cov:9d} {gap:9d} {pct:6.1f}%", flush=True)
    grand_idx = sum(r[1] for r in pkg_rows)
    grand_cov = sum(r[2] for r in pkg_rows)
    print(
        f"\n  TOTAL: indexed={grand_idx}  covered={grand_cov}  "
        f"gap={grand_idx-grand_cov}  coverage={grand_cov/grand_idx*100:.1f}%",
        flush=True,
    )

    # PER-DISCIPLINE summary inside each TOP package (top 6 by volume)
    print("\n[4] DISCIPLINE breakdown WITHIN top-6 packages\n", flush=True)
    top6 = [pkg for pkg, _, _, _, _ in pkg_rows[:6]]
    for pkg in top6:
        print(f"\n  ── {pkg} ──", flush=True)
        print(
            f"    {'discipline':<14} {'indexed':>9} {'covered':>9} {'gap':>9} {'%cov':>7}",
            flush=True,
        )
        for (p, d), n_idx in sorted(
            indexed_by_pkg_disc.items(), key=lambda kv: -kv[1]
        ):
            if p != pkg:
                continue
            n_cov = covered_by_pkg_disc.get((pkg, d), 0)
            gap = n_idx - n_cov
            pct = n_cov / n_idx * 100.0 if n_idx else 0.0
            print(f"    {d:<14} {n_idx:9d} {n_cov:9d} {gap:9d} {pct:6.1f}%", flush=True)

    # Save audit
    out = os.path.join(ROOT, "data", "logs", "scenario_coverage_audit_by_package.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "indexed_total": grand_idx,
                "covered_total": grand_cov,
                "per_package": [
                    {
                        "package": p,
                        "indexed": ni,
                        "covered": nc,
                        "gap": g,
                        "pct": pct,
                    }
                    for p, ni, nc, g, pct in pkg_rows
                ],
                "per_package_discipline": [
                    {
                        "package": p,
                        "discipline": d,
                        "indexed": n_idx,
                        "covered": covered_by_pkg_disc.get((p, d), 0),
                    }
                    for (p, d), n_idx in sorted(
                        indexed_by_pkg_disc.items(), key=lambda kv: (kv[0][0], -kv[1])
                    )
                ],
            },
            f,
            indent=2,
        )
    print(f"\n  audit saved -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
