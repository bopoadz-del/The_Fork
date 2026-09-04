#!/usr/bin/env python3
"""F-ING step 3: reconcile the ledger's content identity against live Drive.

Resume in ``p1b_ingest_drive_server.py`` currently skips a file whenever its
document row has any chunks at all. That proves the file was indexed once,
never that the copy on Drive today is the copy that was indexed. REVISED-3's
ruling is the resume check this script exists to make possible:

    resume skip only when id matches AND drive_md5 == Drive md5
    AND status INDEXED AND chunk_count>0

Drive publishes ``md5Checksum``; this codebase stores ``sha256(raw_bytes)``
as archive identity (``content_sha256``). Those never compare equal, so
``drive_md5`` (migration 0016) exists purely as Drive's own content token,
compared md5-to-md5. ``content_sha256`` keeps its original meaning and is
never touched here.

This script only WALKS Drive, COMPARES, and LABELS. It never deletes a
vector and never touches ``chunks_v2`` rows directly -- matching REVISED-3's
forbidden list. Three outcomes, all reversible:

  VERIFIED    drive_md5 was NULL -- first time this row could be compared.
              Trust-on-first-verify: today's chunk_count/status is taken as
              ground truth and drive_md5 is stamped so the NEXT run can
              detect drift. No status change.
  CHANGED     drive_md5 was set and no longer matches Drive. The file's
              content moved since it was indexed. ingest_status resets to
              UNVERIFIED (chunk_count kept, now known stale) so the live
              pipeline's resume check re-indexes it on the next run.
  TOMBSTONED  a document's drive_file_id no longer appears anywhere in the
              current tier walk. Label only -- chunks stay queryable.
              Filtering TOMBSTONED chunks out of live search is explicitly
              OUT OF SCOPE here: that touches VectorStore.search(), the hot
              path for every user query, and needs its own review.

Orphan chunks (chunks_v2 rows whose doc_id has no documents row -- a prior
ingest bug, not covered by any walk) are reported but NOT written unless
``--quarantine-orphans`` is passed. That flag INSERTS a placeholder
documents row per orphan doc_id (status QUARANTINED) so the group becomes
visible and attributable; it never deletes the orphaned chunks themselves.
This is a judgment call flagged for review before step 3 merges -- it is
schema-adjacent (synthesizing rows) rather than pure labeling of a row that
already exists.

Dry run by default; ``--apply`` writes. Two runs are the acceptance test:
the second run, against an unchanged Drive tree, must report zero CHANGED
and zero new TOMBSTONED -- see INGEST_PROOF.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_DRIVE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL not set. Pass the Neon DSN explicitly -- never the "
            "retired Render dpg- host."
        )
    return re.sub(r"^postgresql\+psycopg://", "postgresql://", url)


def _load_tier_folders(manifest_path: Path, tier: int) -> list[dict[str, Any]]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    tier_block = data.get("tiers", {}).get(str(tier))
    if not tier_block:
        raise SystemExit(f"tier {tier} not found in {manifest_path}")
    return tier_block.get("folders", [])


def _drive_file_id(metadata: dict | None) -> str | None:
    """The document row's Drive identity, whichever key it was written under.

    Mirrors ``app/core/projects.py``'s ``_DRIVE_FILE_ID_KEYS`` tolerance --
    rows have been written by several ingest generations.
    """
    meta = metadata or {}
    for key in ("drive_file_id", "driveFileId", "drive_id", "driveId"):
        val = meta.get(key)
        if val:
            return str(val)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path,
                     default=REPO / "manifests" / "p1b_priority_manifest.json")
    ap.add_argument("--tier", type=int, default=1)
    ap.add_argument("--quarantine-orphans", action="store_true",
                     help="INSERT a placeholder QUARANTINED row per orphan "
                          "doc_id in chunks_v2. Off by default -- orphans "
                          "are only reported.")
    ap.add_argument("--apply", action="store_true", help="Write (default: dry run)")
    args = ap.parse_args(argv)

    import psycopg

    folders = _load_tier_folders(args.manifest, args.tier)
    print(f"[reconcile] tier {args.tier}: {len(folders)} root folder(s)")

    from app.core.gdrive_service import (
        walk_folder,
    )

    now = datetime.now(timezone.utc).isoformat()
    url = _database_url()

    with psycopg.connect(url, connect_timeout=60) as cn, cn.cursor() as cur:
        totals: Counter = Counter()
        verify_writes: list[tuple[str, str, str]] = []   # (md5, ts, doc_id)
        changed_writes: list[tuple[str, str, str]] = []  # (md5, ts, doc_id)
        tombstone_writes: list[tuple[str, str]] = []      # (ts, doc_id)

        for folder in folders:
            project_id = folder["project_id"]
            root = (folder.get("folder_id") or "").strip()
            if not root:
                # Manifest entries without a folder_id exist (e.g. a
                # fixture/pilot project) -- p1b_ingest_drive_server.py
                # already skips these under skipped_no_folder_id. Nothing
                # here can walk what has no Drive root.
                totals["skipped_no_folder_id"] += 1
                print(f"\n[reconcile] skip {folder.get('folder_name')!r} "
                      f"({project_id}): no folder_id in manifest")
                continue
            print(f"\n[reconcile] walking {folder.get('folder_name')!r} "
                  f"({project_id}) root={root}")
            drive_files, errors = walk_folder(root)
            for err in errors:
                print(f"  [walk-error] {err}")
            drive_by_id = {
                f["id"]: f for f in drive_files if f.get("id") and f.get("md5Checksum")
            }
            print(f"  Drive files seen: {len(drive_files)} "
                  f"({len(drive_by_id)} with md5Checksum)")

            cur.execute(
                "SELECT id, metadata, ingest_status, drive_md5 "
                "FROM documents WHERE project_id = %s",
                (project_id,),
            )
            rows = cur.fetchall()
            print(f"  document rows: {len(rows)}")

            seen_drive_ids: set[str] = set()
            for doc_id, metadata, status, drive_md5 in rows:
                fid = _drive_file_id(metadata)
                if not fid:
                    continue  # not Drive-sourced; out of scope here
                if not _DRIVE_FILE_ID_RE.fullmatch(fid):
                    totals["quarantine_bad_id"] += 1
                    continue
                live = drive_by_id.get(fid)
                if live is None:
                    if status != "TOMBSTONED":
                        totals["tombstoned"] += 1
                        tombstone_writes.append((now, doc_id))
                    continue
                seen_drive_ids.add(fid)
                current_md5 = live["md5Checksum"]
                if drive_md5 is None:
                    totals["verified"] += 1
                    verify_writes.append((current_md5, now, doc_id))
                elif drive_md5 != current_md5:
                    totals["changed"] += 1
                    changed_writes.append((current_md5, now, doc_id))
                else:
                    totals["unchanged"] += 1

            # Files on Drive today that no document row claims at all --
            # informational only. Not this script's job to ingest them (that
            # is p1b_ingest_drive_server.py's resume path); surfaced here so
            # an operator can see the corpus growing between reconcile runs.
            new_on_drive = set(drive_by_id) - seen_drive_ids
            if new_on_drive:
                totals["new_on_drive_no_doc_row"] += len(new_on_drive)

            # Orphan chunks: doc_id present in chunks_v2, absent from documents.
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'chunks%%' "
                "AND table_name <> 'chunks' ORDER BY table_name DESC LIMIT 1"
            )
            chunk_table = cur.fetchone()[0]
            cur.execute(
                f"SELECT c.doc_id, COUNT(*) FROM {chunk_table} c "
                f"LEFT JOIN documents d ON d.id = c.doc_id "
                f"WHERE c.project_id = %s AND d.id IS NULL "
                f"GROUP BY c.doc_id",
                (project_id,),
            )
            orphans = cur.fetchall()
            if orphans:
                totals["orphan_doc_ids"] += len(orphans)
                print(f"  orphan doc_ids in {chunk_table} (no documents row): "
                      f"{len(orphans)}")
                if args.quarantine_orphans:
                    for orphan_doc_id, n_chunks in orphans:
                        cur.execute(
                            "INSERT INTO documents "
                            "(id, project_id, original_name, ingest_status, "
                            " ingest_status_reason, chunk_count, last_verified_at, "
                            " metadata) "
                            "VALUES (%s, %s, %s, 'QUARANTINED', "
                            " 'orphan_chunk:no_document_row', %s, %s, %s::jsonb) "
                            "ON CONFLICT (id) DO NOTHING",
                            (orphan_doc_id, project_id,
                             f"(orphan chunk group, {n_chunks} chunks)",
                             n_chunks, now,
                             json.dumps({"recovered_by": "reconcile_drive_md5",
                                         "recovered_at": now})),
                        )

        print("\n[reconcile] summary:")
        for k, v in totals.most_common():
            print(f"   {k:<22}{v:>6}")

        if not args.apply:
            cn.rollback()
            print("\nDRY RUN -- nothing written. Re-run with --apply.")
            return 0

        for md5, ts, doc_id in verify_writes:
            cur.execute(
                "UPDATE documents SET drive_md5=%s, last_verified_at=%s "
                "WHERE id=%s", (md5, ts, doc_id),
            )
        for md5, ts, doc_id in changed_writes:
            cur.execute(
                "UPDATE documents SET drive_md5=%s, last_verified_at=%s, "
                "ingest_status='UNVERIFIED', "
                "ingest_status_reason='content_changed:pending_reingest' "
                "WHERE id=%s", (md5, ts, doc_id),
            )
        for ts, doc_id in tombstone_writes:
            cur.execute(
                "UPDATE documents SET ingest_status='TOMBSTONED', "
                "ingest_status_reason='not_in_drive_walk', "
                "last_verified_at=%s WHERE id=%s", (ts, doc_id),
            )
        cn.commit()
        print(f"\n[reconcile] committed: {len(verify_writes)} verified, "
              f"{len(changed_writes)} changed, {len(tombstone_writes)} tombstoned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
