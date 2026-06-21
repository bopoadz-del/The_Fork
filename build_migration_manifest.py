"""Build per-document migration manifest from the local SQLite corpus.

Read-only: opens data/the_fork.db, extracts each doc's source path from its
chunk_index=0 provenance header, classifies into training_material /
master_folder / unclassified, writes JSON to C:/tmp/migration_manifest.json.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter

DB_PATH = r"C:\Users\shimm\The_Fork\data\the_fork.db"
OUT_PATH = r"C:\tmp\migration_manifest.json"
PROJECT_ID = "drive_archive"

TRAINING_FOLDERS = {
    "200-Project Controls Procedures",
    "300-Delivery Management Procedures",
    "400-Construction Management Procedures",
    "500-Design Management",
    "600-Procurement & Contracts",
    "Scaned Files - High Rise Building",
    "Scaned Files - Road Works",
    "Scaned Files -Concrete Problems",
}
MASTER_FOLDER = "Master Folder"

SOURCE_RE = re.compile(r"\[source: ([^\]]+)\]")
MY_DRIVE_RE = re.compile(r"My Drive\\(.+)$")


def classify(top_folder: str | None) -> str:
    if top_folder is None:
        return "unclassified"
    if top_folder == MASTER_FOLDER:
        return "master_folder"
    if top_folder in TRAINING_FOLDERS:
        return "training_material"
    return "unclassified"


def extract_path(text: str) -> str | None:
    m = SOURCE_RE.search(text)
    if not m:
        return None
    return m.group(1).strip()


def top_folder_from_path(path: str) -> str | None:
    """Split on 'My Drive\\' then take the first \\-segment after it."""
    m = MY_DRIVE_RE.search(path)
    if not m:
        return None
    rest = m.group(1)
    first = rest.split("\\", 1)[0]
    return first or None


def main() -> int:
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA query_only = ON;")
    cur = con.cursor()

    total_docs_expected = cur.execute(
        "SELECT COUNT(DISTINCT doc_id) FROM chunks WHERE project_id=?",
        (PROJECT_ID,),
    ).fetchone()[0]
    total_chunks_expected = cur.execute(
        "SELECT COUNT(*) FROM chunks WHERE project_id=?",
        (PROJECT_ID,),
    ).fetchone()[0]

    # Per-doc chunk counts (one round-trip rather than N).
    chunk_counts: dict[str, int] = {}
    for doc_id, n in cur.execute(
        "SELECT doc_id, COUNT(*) FROM chunks WHERE project_id=? GROUP BY doc_id",
        (PROJECT_ID,),
    ):
        chunk_counts[doc_id] = n

    # chunk_index=0 rows -> provenance.
    documents: list[dict] = []
    missing_source: list[str] = []
    unclassified: list[dict] = []
    dest_counter: Counter[str] = Counter()
    dest_chunk_counter: Counter[str] = Counter()

    rows = cur.execute(
        "SELECT doc_id, text FROM chunks "
        "WHERE project_id=? AND chunk_index=0 "
        "ORDER BY doc_id",
        (PROJECT_ID,),
    ).fetchall()

    for doc_id, text in rows:
        n_chunks = chunk_counts.get(doc_id, 0)
        path = extract_path(text)
        if path is None:
            missing_source.append(doc_id)
            top = None
            original_name = None
        else:
            top = top_folder_from_path(path)
            original_name = path.rsplit("\\", 1)[-1] if "\\" in path else path

        dest = classify(top)
        entry = {
            "doc_id": doc_id,
            "original_path": path,
            "original_name": original_name,
            "top_folder": top,
            "dest_project_id": dest,
            "n_chunks": n_chunks,
        }
        documents.append(entry)
        dest_counter[dest] += 1
        dest_chunk_counter[dest] += n_chunks
        if dest == "unclassified":
            unclassified.append(entry)

    # Find any docs whose chunks have 0 (shouldn't happen given the join, but assert).
    zero_chunk_docs = [d["doc_id"] for d in documents if d["n_chunks"] == 0]

    summary = {
        "total_docs": len(documents),
        "total_chunks": sum(chunk_counts.values()),
        "training_material": {
            "docs": dest_counter.get("training_material", 0),
            "chunks": dest_chunk_counter.get("training_material", 0),
        },
        "master_folder": {
            "docs": dest_counter.get("master_folder", 0),
            "chunks": dest_chunk_counter.get("master_folder", 0),
        },
        "unclassified": {
            "docs": dest_counter.get("unclassified", 0),
            "chunks": dest_chunk_counter.get("unclassified", 0),
        },
        "missing_source_header_docs": len(missing_source),
        "zero_chunk_docs": len(zero_chunk_docs),
        "expected_docs_from_db": total_docs_expected,
        "expected_chunks_from_db": total_chunks_expected,
    }

    payload = {"summary": summary, "documents": documents}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Diagnostic stdout.
    print(json.dumps(summary, indent=2))
    if missing_source:
        print(f"\nMISSING SOURCE HEADER ({len(missing_source)} docs):")
        for d in missing_source[:20]:
            print(" ", d)
    if zero_chunk_docs:
        print(f"\nZERO-CHUNK DOCS ({len(zero_chunk_docs)} docs):")
        for d in zero_chunk_docs[:20]:
            print(" ", d)
    if unclassified:
        print(f"\nUNCLASSIFIED ({len(unclassified)} docs) -- top_folder histogram:")
        hist = Counter((u["top_folder"] or "<no-source>") for u in unclassified)
        for k, v in hist.most_common():
            print(f"  {v:5d}  {k}")
        print("  -- sample paths --")
        for u in unclassified[:10]:
            print(f"    {u['doc_id']}  {u['original_path']}")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
