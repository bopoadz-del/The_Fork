"""Unit coverage for the pure logic in scripts/reconcile_drive_md5.py.

The script's main() talks to live Neon + Drive and is exercised manually
(dry-run, then --apply) rather than in CI. These tests pin the two decision
functions that do NOT need either: drive_file_id extraction and the
regex that gates a document into (or out of) TOMBSTONE consideration.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "reconcile_drive_md5", REPO / "scripts" / "reconcile_drive_md5.py"
)
reconcile = importlib.util.module_from_spec(_spec)
sys.modules["reconcile_drive_md5"] = reconcile
_spec.loader.exec_module(reconcile)  # type: ignore[union-attr]


def test_drive_file_id_reads_the_canonical_key():
    assert reconcile._drive_file_id({"drive_file_id": "abc123"}) == "abc123"


def test_drive_file_id_falls_back_across_legacy_key_spellings():
    """Rows have been written by several ingest generations -- camelCase and
    snake_case both landed in production metadata."""
    for key in ("driveFileId", "drive_id", "driveId"):
        assert reconcile._drive_file_id({key: "xyz789"}) == "xyz789"


def test_drive_file_id_is_none_for_non_drive_documents():
    """user_upload / tier1_local_mount rows with no Drive identity are out
    of this script's scope -- they must not be misread as some other key."""
    assert reconcile._drive_file_id({}) is None
    assert reconcile._drive_file_id(None) is None
    assert reconcile._drive_file_id({"r2_object_key": "x"}) is None


def test_drive_file_id_re_accepts_real_drive_ids():
    real = "1GH3ri2gfPultO9FG56MdsLC7-7SvJB9j"
    assert reconcile._DRIVE_FILE_ID_RE.fullmatch(real)


def test_drive_file_id_re_rejects_garbage_that_would_crash_a_drive_lookup():
    """A malformed id must route to quarantine, not to a live Drive API call
    that 400s and gets misread as 'file not found' (-> wrongly tombstoned)."""
    for bad in ("", "not a url", "https://drive.google.com/file/d/xyz", "short"):
        assert not reconcile._DRIVE_FILE_ID_RE.fullmatch(bad)


def test_load_tier_folders_reads_the_live_priority_manifest():
    """Smoke check against the real manifest shape, not a fixture -- if the
    manifest's schema drifts this catches it before a live run does.

    Every entry carries a project_id, but folder_id can legitimately be
    absent (a fixture/pilot project) -- main() skips those, it does not
    require every entry to be walkable."""
    folders = reconcile._load_tier_folders(
        REPO / "manifests" / "p1b_priority_manifest.json", tier=1
    )
    assert folders, "tier 1 must have at least one folder entry"
    for f in folders:
        assert f.get("project_id")
    assert any(f.get("folder_id") for f in folders), (
        "at least one tier-1 entry must have a walkable folder_id"
    )


def test_load_tier_folders_raises_on_a_tier_that_does_not_exist():
    import pytest

    with pytest.raises(SystemExit):
        reconcile._load_tier_folders(
            REPO / "manifests" / "p1b_priority_manifest.json", tier=999
        )
