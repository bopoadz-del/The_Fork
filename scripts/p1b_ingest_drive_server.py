#!/usr/bin/env python3
"""P1b — server-side Drive ingestion for the clean rebuild.

Runs on Render (or any server with the prod DB attached). Files are fetched
via the platform's Google Drive service-account path, written to DATA_DIR,
and indexed through the normal doc_index pipeline into the configured vector
namespace.

Differences from the local p1b script:
- Source is Google Drive API, not a laptop Drive mount.
- Folder selection is driven by manifests/p1b_priority_manifest.json tiers.
- Per-folder tally is kept and flushed to the report.
- Provenance metadata includes Drive file id, source tier, and ingestion run id.

Required env vars on the server:
  RAG_EMBEDDING_MODEL, RAG_VECTOR_NAMESPACE, DATABASE_URL, DATA_DIR,
  GDRIVE_SERVICE_ACCOUNT_JSON, GDRIVE_PROJECT_FOLDERS (optional)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Set defaults BEFORE importing app modules so they pick them up.
os.environ.setdefault("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
os.environ.setdefault("RAG_VECTOR_NAMESPACE", "v2")

# Files that cannot be downloaded as bytes from Drive or are not indexable.
_UNSUPPORTED_MIMES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.drawing",
    "application/vnd.google-apps.folder",
}

_UNSUPPORTED_EXTS = {".gdoc", ".gsheet", ".gslides", ".gdraw", ".rar"}

# Sidecar heartbeat. Lives next to the report because it answers the question
# the report cannot when the run is killed: where was it, and what was the box
# doing, at the last moment it was alive.
DEFAULT_RUN_STATE = "manifests/p1b_ingest_run_state.json"

# Set in the child by --supervise so it cannot supervise itself recursively.
SUPERVISED_ENV = "P1B_SUPERVISED"


def future_result_or_error(
    fut: Future,
    file_meta: Dict[str, Any] | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Drain a worker future without letting the exception escape.

    #477 caught archive_document / _ingest_file, but ``fut.result()`` in the
    main thread was still bare. A leaked botocore AccessDenied there aborts
    the whole TIER-1 run (de6a06b7542c died at 323/1380 after 'ingest
    continues' plus a leftover traceback).
    """
    rel = ""
    if file_meta:
        rel = file_meta.get("_drive_path") or file_meta.get("name") or ""
    try:
        rel_out, result = fut.result()
        return (rel_out or rel), result
    except Exception as exc:  # noqa: BLE001 — one file must not kill the run
        print(
            f"[p1b-server] worker future raised {type(exc).__name__}: {exc}; "
            f"continuing ingest for {rel!r}",
            file=sys.stderr,
        )
        return rel or "?", {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _is_geodatabase_internal(path: str) -> bool:
    """Esri geodatabase folders contain non-indexable internal files (gdb,
    timestamps, a00000001.gdbtable, etc.) that Drive reports as tiny
    application/octet-stream blobs. Skip any file inside a ``*.gdb`` folder.
    """
    return any(part.lower().endswith(".gdb") for part in Path(path).parts[:-1])


def _safe_stored_name(original: str) -> str:
    """Filesystem-safe stored name; preserves extension."""
    base = Path(original).stem
    ext = Path(original).suffix
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)[:80]
    return f"{safe}{ext}"


def _ingest_file(
    file_meta: Dict[str, Any],
    project_id: str,
    data_dir: Path,
    run_id: str,
    gdrive_service: Any,
    existing_doc: Dict[str, Any] | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Download one Drive file and index it. Returns (drive_path, result).

    ``existing_doc`` — a document row already registered for this Drive file
    (a previous pass added the row but indexing produced zero chunks, e.g.
    the NUL-byte DataError). The file is re-downloaded to the same
    deterministic path and re-indexed into the SAME document row instead of
    creating a duplicate.
    """
    from app.core import doc_index, file_crypto, projects as projects_mod, r2_storage

    rel = file_meta.get("_drive_path") or file_meta.get("name", "")
    mime = file_meta.get("mimeType", "")
    ext = Path(rel).suffix.lower()

    # Skip Google-native, known-unsupported, and geodatabase internal files.
    if (
        mime in _UNSUPPORTED_MIMES
        or ext in _UNSUPPORTED_EXTS
        or _is_geodatabase_internal(rel)
    ):
        return rel, {
            "status": "error",
            "error": "SKIPPED_UNSUPPORTED",
            "mimeType": mime,
        }

    # Size guard before download. Default OFF (0) — Tier-1 the client project contract
    # volumes (signed PSA, drawing packs) exceed 500 MB and were still
    # SKIPPED_TOO_LARGE after the 100→500 bump. Set P1B_MAX_FILE_SIZE_MB
    # to a positive number to re-enable a cap.
    size = int(file_meta.get("size") or 0)
    max_size = int(os.getenv("P1B_MAX_FILE_SIZE_MB", "0")) * 1024 * 1024
    if max_size > 0 and size > max_size:
        return rel, {
            "status": "error",
            "error": "SKIPPED_TOO_LARGE",
            "size_mb": round(size / (1024 * 1024), 1),
        }

    # Minimum useful size guard. Files below this threshold cannot produce
    # indexable content (empty placeholders, geodatabase internals, etc.).
    # Default OFF (0); set P1B_MIN_FILE_SIZE_BYTES to skip them cleanly.
    min_size = int(os.getenv("P1B_MIN_FILE_SIZE_BYTES", "0"))
    if min_size > 0 and size < min_size:
        return rel, {
            "status": "error",
            "error": "SKIPPED_TOO_SMALL",
            "size_bytes": size,
        }

    # Download from Drive.
    raw_bytes, dl_err = gdrive_service.download_file_bytes(file_meta["id"])
    if dl_err:
        return rel, {
            "status": "error",
            "error": f"DOWNLOAD_FAILED: {dl_err}",
        }

    if not raw_bytes:
        return rel, {
            "status": "error",
            "error": "SKIPPED_EMPTY",
        }

    content_sha = hashlib.sha256(raw_bytes).hexdigest()
    stored_name = f"{hashlib.sha256(rel.encode()).hexdigest()[:8]}_{_safe_stored_name(Path(rel).name)}"
    dest = data_dir / stored_name
    file_crypto.write_document(str(dest), raw_bytes)

    # Archive raw bytes to R2 before indexing. The local copy is only needed
    # for the extractors/indexers and is deleted afterwards.
    # R2 failure (AccessDenied, timeouts, client construction) must not abort
    # this file: the Neon document row + index still happen with r2_archived=False.
    try:
        archive = r2_storage.archive_document(
            project_id=project_id,
            drive_file_id=file_meta["id"],
            original_name=Path(rel).name,
            raw_bytes=raw_bytes,
            content_sha256=content_sha,
        )
    except Exception as exc:  # noqa: BLE001 — belt-and-suspenders if archive_document leaks
        print(
            f"[p1b-server] R2 archive raised {type(exc).__name__}: {exc}; "
            f"continuing ingest for {rel!r} (r2_archived=False)",
            file=sys.stderr,
        )
        archive = {
            "archived": False,
            "r2_object_key": None,
            "r2_bucket": None,
            "r2_endpoint": None,
            "r2_account_id": None,
            "error": f"R2_UPLOAD_FAILED: {type(exc).__name__}: {exc}",
        }

    # Release the payload BEFORE indexing. Extraction is the memory peak on
    # this box (app/core/extract_isolated measured one 4.4 MB PDF taking the
    # live instance from 790 MB to 3.9 GB of 4 GB), and until now the whole
    # downloaded file stayed resident through it for no reason: the bytes were
    # already written to ``dest`` and already uploaded to R2, and every reader
    # from here on works off the path. With P1B_MAX_FILE_SIZE_MB defaulting to
    # 0 (no cap) and P1B_PARALLELISM=2, this was up to two multi-hundred-MB
    # buffers held for the duration of the two heaviest extractions.
    downloaded_bytes = len(raw_bytes)
    del raw_bytes

    common_meta = {
        "drive_file_id": file_meta["id"],
        "drive_path": rel,
        "source": "p1b_server_drive_reingestion",
        "ingestion_run_id": run_id,
        "mimeType": mime,
        "content_sha256": content_sha,
    }
    if archive.get("r2_object_key"):
        common_meta["r2_object_key"] = archive["r2_object_key"]
        common_meta["r2_bucket"] = archive.get("r2_bucket")
        common_meta["r2_endpoint"] = archive.get("r2_endpoint")
        common_meta["r2_account_id"] = archive.get("r2_account_id")
    if archive.get("error"):
        common_meta["r2_archive_error"] = archive["error"]

    if existing_doc is not None:
        # Zero-chunk retry: the row exists from a failed pass; re-index it
        # in place. The stored name is deterministic, so the fresh download
        # above landed at the same path the row's file_path points to.
        projects_mod.update_document_metadata(existing_doc["id"], common_meta)
        result = doc_index.index_document(project_id, existing_doc["id"])
        result["reindexed_existing_doc"] = True
        r2_storage.delete_local_archive(str(dest))
        result["r2_archive"] = archive
        print(
            f"[p1b-server] VERIFICATION doc_id={existing_doc['id']} "
            f"drive_file_id={file_meta['id']} drive_path={rel!r} "
            f"mime={mime!r} drive_size={size} downloaded_bytes={downloaded_bytes} "
            f"rag_indexed={result.get('rag_indexed', 0)} "
            f"r2_archived={archive.get('archived')} "
            f"r2_object_key={archive.get('r2_object_key')} "
            f"r2_error={archive.get('error')} "
            f"index_status={result.get('status')} "
            f"index_error={result.get('error')}",
            file=sys.stderr,
        )
        return rel, result

    doc = projects_mod.add_document(
        project_id=project_id,
        original_name=Path(rel).name,
        stored_as=stored_name,
        file_path=str(dest),
        size=size,
        content_sha256=content_sha,
        metadata=common_meta,
    )
    result = doc_index.index_document(project_id, doc["id"])
    r2_storage.delete_local_archive(str(dest))
    result["r2_archive"] = archive
    print(
        f"[p1b-server] VERIFICATION doc_id={doc['id']} "
        f"drive_file_id={file_meta['id']} drive_path={rel!r} "
        f"mime={mime!r} drive_size={size} downloaded_bytes={downloaded_bytes} "
        f"rag_indexed={result.get('rag_indexed', 0)} "
        f"r2_archived={archive.get('archived')} "
        f"r2_object_key={archive.get('r2_object_key')} "
        f"r2_error={archive.get('error')} "
        f"index_status={result.get('status')} "
        f"index_error={result.get('error')}",
        file=sys.stderr,
    )
    return rel, result


def _load_priority_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Priority manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Server-side Drive ingestion by priority tier")
    ap.add_argument("--priority-manifest", default="manifests/p1b_priority_manifest.json",
                    help="Path to the priority manifest")
    ap.add_argument("--tier", type=int, default=1,
                    help="Ingest only this tier (1, 2, or 3)")
    ap.add_argument("--output", default="manifests/p1b_server_ingestion_report.json",
                    help="Report path")
    ap.add_argument("--resume", action="store_true",
                    help="Skip files already indexed in the project (by drive_file_id)")
    ap.add_argument(
        "--folder-id",
        action="append",
        default=None,
        dest="folder_ids",
        metavar="ID",
        help=(
            "Restrict this run to these Drive folder id(s). Repeat the flag or "
            "pass comma-separated ids. Walks that folder instead of the full "
            "tier parent; documents still use the matching parent project's "
            "row (project_id / folder_name from the tier entry). If the id is "
            "not a parent folder_id in this tier, it is treated as a subfolder "
            "of the unique parent that has a folder_id. Omit to walk every "
            "folder in the tier (default --tier N --resume behaviour)."
        ),
    )
    ap.add_argument("--keep-alive", action="store_true",
                    help="After the ingestion pass completes, sleep forever so a background worker stays live")
    ap.add_argument("--total-shards", type=int, default=None,
                    help="Total worker shards (env: INGEST_TOTAL_SHARDS)")
    ap.add_argument("--shard-index", type=int, default=None,
                    help="This worker's shard index (env: INGEST_SHARD_INDEX)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Plan only — walk + shard, no DB/index writes (env: INGEST_DRY_RUN)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process at most N files after shard filter (env: INGEST_LIMIT)")
    ap.add_argument("--offset", type=int, default=None,
                    help="Skip first N files after shard filter (env: INGEST_OFFSET)")
    ap.add_argument(
        "--run-state",
        default=None,
        help=(
            "Sidecar heartbeat file (env: P1B_RUN_STATE, default "
            f"{DEFAULT_RUN_STATE}). Updated after every file so a SIGKILLed run "
            "can still be post-mortemed on the next start."
        ),
    )
    ap.add_argument(
        "--supervise",
        action="store_true",
        help=(
            "Run the ingest as a child process and report HOW it died. Only a "
            "parent can see a SIGKILL: run 37159882e871 vanished at 316/1361 "
            "with no traceback and nothing recorded which signal ended it."
        ),
    )
    return ap


def parse_folder_ids(raw: List[str] | None) -> List[str]:
    """Flatten repeatable and comma-separated ``--folder-id`` values."""
    if not raw:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in raw:
        for part in str(item).split(","):
            fid = part.strip()
            if not fid or fid in seen:
                continue
            seen.add(fid)
            out.append(fid)
    return out


def folder_entries_for_run(
    tier_folders: List[Dict[str, Any]],
    requested_ids: List[str],
    *,
    tier: int,
) -> List[Dict[str, Any]]:
    """Return the folder entries this run should walk.

    With no ``requested_ids``, the tier list is unchanged (full parent walk).
    With ids, each id that matches a parent ``folder_id`` walks that parent.
    An id that is not a parent is a subfolder walk of the unique parent that
    has a ``folder_id`` — same ``project_id`` / ``folder_name``, different
    walk root. Raises ``ValueError`` when an id matches no parent and there
    is no unique parent to attach it to.
    """
    if not requested_ids:
        return list(tier_folders)

    parents_by_id: Dict[str, Dict[str, Any]] = {}
    parents_with_id: List[Dict[str, Any]] = []
    for entry in tier_folders:
        fid = (entry.get("folder_id") or "").strip() or None
        if not fid:
            continue
        parents_by_id[fid] = entry
        parents_with_id.append(entry)

    walks: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    for fid in requested_ids:
        if fid in parents_by_id:
            spec = dict(parents_by_id[fid])
            spec["walk_folder_id"] = fid
            walks.append(spec)
            continue
        if len(parents_with_id) == 1:
            spec = dict(parents_with_id[0])
            spec["walk_folder_id"] = fid
            walks.append(spec)
            continue
        unmatched.append(fid)

    if unmatched:
        raise ValueError(
            f"--folder-id not in tier {tier} parent folders and no unique "
            f"parent to treat as a subfolder walk: {', '.join(unmatched)}"
        )
    if not walks:
        raise ValueError(
            f"--folder-id did not match any folder in tier {tier}"
        )
    return walks


def run_state_path(args: argparse.Namespace, *, dry_run: bool | None = None) -> Path:
    """Where this run heartbeats. Dry runs get their own file so a plan-only
    pass cannot overwrite the evidence left by a live run that died."""
    raw = args.run_state or os.getenv("P1B_RUN_STATE") or DEFAULT_RUN_STATE
    path = Path(raw)
    if dry_run is None:
        dry_run = bool(getattr(args, "dry_run", False))
    if dry_run:
        return path.with_name(path.stem + "_dry" + path.suffix)
    return path


def supervised_child_argv(argv: List[str]) -> List[str]:
    """The same command, re-executed unbuffered, without ``--supervise``."""
    return [sys.executable, "-u"] + [a for a in argv if a != "--supervise"]


def run_under_supervisor(args: argparse.Namespace, argv: List[str] | None = None) -> int:
    """Re-exec this script as a child so its death has a witness.

    SIGKILL runs no handler, writes no traceback and flushes no buffer, so the
    killed process itself can never explain what happened — every one of the
    three silent TIER-1 deaths looked identical from inside. The parent sees
    the wait status and the cgroup oom_kill counter, which is the difference
    between "worker PID 829 disappeared" and "killed by SIGKILL (9), oom_kill
    0 -> 1".
    """
    from app.core import ingest_lifecycle as lifecycle

    child = supervised_child_argv(list(sys.argv if argv is None else argv))
    env = dict(os.environ)
    env[SUPERVISED_ENV] = "1"
    return lifecycle.run_supervised(
        child,
        env=env,
        state_path=run_state_path(args),
        forward=lifecycle.stop_signals(),
    )


def main() -> int:
    from app.core import ingest_lifecycle as lifecycle
    from app.core import ingest_sharding as sharding

    args = build_parser().parse_args()

    if args.supervise and not os.getenv(SUPERVISED_ENV):
        return run_under_supervisor(args)

    try:
        total_shards, shard_index = sharding.resolve_shard_env(
            total_shards=args.total_shards,
            shard_index=args.shard_index,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    dry_run = sharding.resolve_dry_run(args.dry_run)
    limit, offset = sharding.resolve_limit_offset(limit=args.limit, offset=args.offset)

    from app.core import gdrive_service, projects as projects_mod
    from app.core.rag import embeddings as _emb, vector_store as _vs

    if not gdrive_service.is_configured():
        print("ERROR: GDRIVE_SERVICE_ACCOUNT_JSON is not set.", file=sys.stderr)
        return 1

    if not dry_run:
        _emb.reset_embedder_cache()
        _vs.reset_store_cache()

    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    if not dry_run:
        data_dir.mkdir(parents=True, exist_ok=True)

    priority_manifest = _load_priority_manifest(Path(args.priority_manifest))
    tier_key = str(args.tier)
    tier = priority_manifest.get("tiers", {}).get(tier_key)
    if not tier:
        print(f"ERROR: tier {args.tier} not found in priority manifest.", file=sys.stderr)
        return 1

    requested_ids = parse_folder_ids(args.folder_ids)
    if args.folder_ids is not None and not requested_ids:
        print("ERROR: --folder-id was given but no folder id was provided.", file=sys.stderr)
        return 1
    try:
        folder_entries = folder_entries_for_run(
            tier["folders"], requested_ids, tier=args.tier,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex[:12]
    print(
        f"[p1b-server] run_id={run_id} tier={args.tier} folders={len(folder_entries)} "
        f"shard_index={shard_index} total_shards={total_shards} dry_run={dry_run}"
        + (f" folder_ids={requested_ids}" if requested_ids else ""),
        file=sys.stderr,
    )

    # Track per-folder tally.
    folder_tallies: Dict[str, Dict[str, Any]] = {}
    global_tally = {
        "succeeded": 0,
        "zero_chunk": 0,
        "skipped_too_large": 0,
        "skipped_too_small": 0,
        "skipped_unsupported": 0,
        "skipped_empty": 0,
        "download_failed": 0,
        "errors": 0,
        "already_indexed": 0,
    }
    results: List[Dict[str, Any]] = []

    def _bump_folder(folder_name: str, key: str) -> None:
        folder_tallies.setdefault(folder_name, {
            "succeeded": 0, "zero_chunk": 0, "skipped_too_large": 0,
            "skipped_too_small": 0, "skipped_unsupported": 0,
            "skipped_empty": 0, "download_failed": 0, "errors": 0,
        })[key] += 1

    def _report_payload(reason: str, complete: bool) -> Dict[str, Any]:
        """One report shape for every write.

        ``exit_reason`` / ``complete`` are the fields the three silent deaths
        needed and no report carried: a partial report and a finished one were
        byte-indistinguishable apart from the counts, so a run that stopped at
        316/1361 read as a run that had 316 files to do. The tally itself is
        untouched — nothing here promotes an unfinished run to a finished one.
        """
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "tier": args.tier,
            "embedder": os.environ["RAG_EMBEDDING_MODEL"],
            "namespace": os.environ["RAG_VECTOR_NAMESPACE"],
            "exit_reason": reason,
            "complete": complete,
            "elapsed_sec": round(time.monotonic() - t0_global, 3),
            "shard_index": shard_index,
            "total_shards": total_shards,
            "global_tally": global_tally,
            "folder_tallies": folder_tallies,
            "results": results,
        }

    def _write_report(reason: str, complete: bool) -> None:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(_report_payload(reason, complete), fh, indent=2, ensure_ascii=False)

    def _write_partial_report() -> None:
        _write_report("in_progress", False)

    def _flush_final(reason: str, complete: bool) -> None:
        """The run's last words. Called exactly once, by the lifecycle guard,
        on every exit path this process can observe — normal return, stop
        signal, exception, SystemExit, atexit.

        Before this existed the tally line was the last statement of ``main``,
        so any exit that was not a clean fall-through produced no tally at all
        and the operator had to infer the outcome from a truncated log.
        """
        elapsed_global = time.monotonic() - t0_global
        if not dry_run:
            _write_report(reason, complete)
            shard_manifest = sharding.empty_shard_manifest(
                shard_index=shard_index,
                total_shards=total_shards,
                extra={
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": run_id,
                    "tier": args.tier,
                    "embedder": os.environ["RAG_EMBEDDING_MODEL"],
                    "namespace": os.environ["RAG_VECTOR_NAMESPACE"],
                    "exit_reason": reason,
                    "complete": complete,
                    "processed": global_tally["succeeded"],
                    "skipped": global_tally["skipped_too_large"] + global_tally["skipped_too_small"]
                    + global_tally["skipped_empty"],
                    "failed": global_tally["errors"] + global_tally["zero_chunk"]
                    + global_tally["download_failed"],
                    "unsupported": global_tally["skipped_unsupported"],
                    "already_indexed": global_tally["already_indexed"],
                    "durations_sec": {"_total": round(elapsed_global, 3)},
                    "global_tally": global_tally,
                    "folder_tallies": folder_tallies,
                    "results": results,
                },
            )
            shard_out = sharding.shard_manifest_path(shard_index, total_shards)
            shard_out.parent.mkdir(parents=True, exist_ok=True)
            with open(shard_out, "w", encoding="utf-8") as fh:
                json.dump(shard_manifest, fh, indent=2, ensure_ascii=False)
            print(f"[p1b-server] report written to {args.output}", file=sys.stderr, flush=True)
            print(f"[p1b-server] shard manifest → {shard_out}", file=sys.stderr, flush=True)
        print(
            f"[p1b-server] tier {args.tier}: {global_tally['succeeded']} succeeded, "
            f"{global_tally['zero_chunk']} zero-chunk, "
            f"{global_tally['skipped_too_large']} too-large, "
            f"{global_tally['skipped_too_small']} too-small, "
            f"{global_tally['skipped_unsupported']} unsupported, "
            f"{global_tally['already_indexed']} already_indexed, "
            f"{global_tally['errors']} errors, {elapsed_global:.1f}s "
            f"shard={shard_index}/{total_shards} dry_run={dry_run} "
            f"exit_reason={reason} complete={complete}",
            file=sys.stderr,
            flush=True,
        )
        if not complete:
            print(
                f"[p1b-server] RUN INCOMPLETE exit_reason={reason} — the tally "
                f"above counts only the files that finished. Re-run with "
                f"--resume to continue; nothing here marks the tier done.",
                file=sys.stderr,
                flush=True,
            )

    t0_global = time.monotonic()
    run = lifecycle.RunLifecycle(
        run_id=run_id,
        label=f"p1b-server tier={args.tier} shard={shard_index}/{total_shards}",
        state_path=run_state_path(args, dry_run=dry_run),
        flush=_flush_final,
        stall_after_s=float(os.getenv("P1B_STALL_WARN_S", "900")),
        heartbeat_every=max(1, int(os.getenv("P1B_HEARTBEAT_EVERY", "10"))),
    )
    run.start(
        context={
            "tier": args.tier,
            "output": args.output,
            "folders": len(folder_entries),
            "folder_ids": requested_ids,
            "shard_index": shard_index,
            "total_shards": total_shards,
            "dry_run": dry_run,
            "limit": limit,
            "offset": offset,
            "parallelism": int(os.getenv("P1B_PARALLELISM", "2")),
            "supervised": bool(os.getenv(SUPERVISED_ENV)),
            "argv": sys.argv[1:],
        },
    )

    if dry_run:
        # Fresh dry-run manifest each invocation (multi-folder appends below).
        dry_manifest = sharding.shard_manifest_path(shard_index, total_shards)
        if dry_manifest.exists():
            dry_manifest.unlink()

    for folder_entry in folder_entries:
        parent_folder_id = folder_entry.get("folder_id")
        folder_id = folder_entry.get("walk_folder_id") or parent_folder_id
        folder_name = folder_entry.get("folder_name") or folder_entry.get("project_id")
        project_id_for_folder = folder_entry.get("project_id")

        if not folder_id:
            print(f"[p1b-server] SKIP {folder_name}: no folder_id (needs Chadi to set)", file=sys.stderr)
            continue

        if parent_folder_id and folder_id != parent_folder_id:
            print(
                f"[p1b-server] walking {folder_name} subfolder ({folder_id}) "
                f"parent={parent_folder_id} ...",
                file=sys.stderr,
            )
        else:
            print(f"[p1b-server] walking {folder_name} ({folder_id}) ...", file=sys.stderr)
        files, walk_errors = gdrive_service.walk_folder(folder_id, max_depth=12, page_size=200)
        if walk_errors:
            for err in walk_errors:
                print(f"[p1b-server] WALK ERROR: {err}", file=sys.stderr)

        # Ensure ONE project row for this folder BEFORE sharding / resume.
        # projects.name is not UNIQUE — parallel shard workers that each
        # create_project(folder_name) would mint separate random ids and
        # split documents/chunks across projects. Prefer the manifest's
        # stable project_id; fall back to a name-derived id.
        already_indexed: set[str] = set()
        retry_doc_by_fid: Dict[str, Dict[str, Any]] = {}
        project_id: str | None = None
        if not dry_run:
            preferred_id = (project_id_for_folder or "").strip() or None
            project, created = projects_mod.get_or_create_project(
                folder_name,
                project_id=preferred_id,
                origin="user_drive_import",
            )
            project_id = project["id"]
            print(
                f"[p1b-server] {'created' if created else 'using'} project "
                f"{project_id} for {folder_name} "
                f"(shard={shard_index}/{total_shards})",
                file=sys.stderr,
            )

            # Resume is ALWAYS on for live runs. A document row alone is
            # NOT proof of success — skip only files whose doc actually
            # has chunks; zero-chunk docs are re-indexed in place.
            chunk_counts: Dict[str, int] = {}
            counts_available = True
            try:
                chunk_counts = _vs.get_store().count_by_doc(project_id)
            except Exception as exc:  # noqa: BLE001 — degrade to row-only resume
                print(f"[p1b-server] WARN: chunk-count resume check failed "
                      f"({type(exc).__name__}: {exc}); falling back to "
                      f"row-presence resume", file=sys.stderr)
                counts_available = False
            def _doc_is_unsupported(doc: Dict[str, Any]) -> bool:
                meta = doc.get("metadata") or {}
                path = meta.get("drive_path") or ""
                mime = meta.get("mimeType", "")
                ext = Path(path).suffix.lower()
                return (
                    mime in _UNSUPPORTED_MIMES
                    or ext in _UNSUPPORTED_EXTS
                    or _is_geodatabase_internal(path)
                )

            for doc in projects_mod.list_documents(project_id):
                fid = (doc.get("metadata") or {}).get("drive_file_id")
                if not fid:
                    continue
                if not counts_available:
                    already_indexed.add(fid)
                elif chunk_counts.get(doc["id"], 0) > 0:
                    already_indexed.add(fid)
                elif _doc_is_unsupported(doc):
                    # Legacy zero-chunk row for a file we now know is
                    # unsupported (e.g. geodatabase internals). Don't retry.
                    continue
                elif fid not in retry_doc_by_fid:
                    retry_doc_by_fid[fid] = doc

        # Drop unsupported before sharding so every worker sees the same
        # supported universe and sha256 assignment stays partition-complete.
        def _is_unsupported(fm: Dict[str, Any]) -> bool:
            path = fm.get("_drive_path") or fm.get("name") or ""
            mime = fm.get("mimeType", "")
            ext = Path(path).suffix.lower()
            return (
                mime in _UNSUPPORTED_MIMES
                or ext in _UNSUPPORTED_EXTS
                or _is_geodatabase_internal(path)
            )

        unsupported_files = [f for f in files if _is_unsupported(f)]
        supported_files = [f for f in files if not _is_unsupported(f)]
        filtered_files = [f for f in supported_files if f["id"] not in already_indexed]
        skipped_already = len(supported_files) - len(filtered_files)

        def _drive_identity(fm: Dict[str, Any]) -> str:
            return sharding.file_identity(
                drive_file_id=fm.get("id"),
                source_path=fm.get("_drive_path") or fm.get("name"),
            )

        def _drive_path(fm: Dict[str, Any]) -> str:
            return fm.get("_drive_path") or fm.get("name") or fm.get("id") or ""

        shard_files = sharding.filter_by_shard(
            filtered_files,
            identity_fn=_drive_identity,
            shard_index=shard_index,
            total_shards=total_shards,
        )
        # Sort before offset/limit so a limited batch picks the intended
        # end of the size distribution, while the shard partition itself
        # stays stable. Default smallest-first; set P1B_LARGEST_FIRST=1
        # to process largest files first (useful for quickly reaching real
        # documents during testing). Files with no reported size (0) go to
        # the end regardless.
        largest_first = os.getenv("P1B_LARGEST_FIRST", "").strip().lower() in {"1", "true", "yes"}

        def _sort_key(f: Dict[str, Any]) -> Tuple[bool, int]:
            size = int(f.get("size") or 0)
            # size==0 always sorts last; otherwise largest-first uses
            # negative size so the reverse flag is not needed.
            return (size == 0, -size if largest_first else size)

        shard_files.sort(key=_sort_key)
        # Offset/limit AFTER shard filter so assignment stays stable.
        batch_files = sharding.apply_offset_limit(
            shard_files, offset=offset, limit=limit,
        )
        print(
            f"[p1b-server] {folder_name}: discovered={len(files)} "
            f"supported={len(supported_files)} unsupported={len(unsupported_files)} "
            f"already_indexed={skipped_already} "
            f"shard={shard_index}/{total_shards} assigned={len(shard_files)} "
            f"batch={len(batch_files)} "
            f"({len(retry_doc_by_fid)} zero-chunk retries)",
            file=sys.stderr,
        )
        global_tally["skipped_unsupported"] += len(unsupported_files)
        global_tally["already_indexed"] += skipped_already
        if skipped_already:
            print(
                f"[p1b-server] skipped_already_indexed={skipped_already} "
                f"for {folder_name}",
                file=sys.stderr,
            )

        if dry_run:
            report = sharding.dry_run_report(
                total_discovered=len(files),
                supported_items=filtered_files,
                unsupported_count=len(unsupported_files),
                shard_index=shard_index,
                total_shards=total_shards,
                identity_fn=_drive_identity,
                path_fn=_drive_path,
                already_indexed_count=skipped_already,
            )
            report.update({
                "folder": folder_name,
                "folder_id": folder_id,
                "run_id": run_id,
                "tier": args.tier,
                "offset": offset,
                "limit": limit,
                "batch_after_offset_limit": len(batch_files),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })
            out_path = sharding.shard_manifest_path(shard_index, total_shards)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Merge multi-folder dry-runs into one manifest when possible.
            existing_report: Dict[str, Any] = {}
            if out_path.exists():
                try:
                    existing_report = json.loads(out_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    existing_report = {}
            folders = list(existing_report.get("folders") or [])
            folders.append(report)
            merged = {
                "dry_run": True,
                "db_writes": False,
                "shard_index": shard_index,
                "total_shards": total_shards,
                "run_id": run_id,
                "tier": args.tier,
                "folders": folders,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, indent=2, ensure_ascii=False)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            print(f"[p1b-server] dry-run manifest → {out_path}", file=sys.stderr)
            continue

        assert project_id is not None  # set above for live runs

        # Use the sharded (+offset/limit) batch for live work.
        filtered_files = batch_files

        t0_folder = time.monotonic()

        def _tally_result(rel: str, result: Dict[str, Any]) -> None:
            results.append({"folder": folder_name, "path": rel, "result": result})
            if result.get("status") == "error":
                err = result.get("error")
                if err == "ZERO_CHUNK":
                    global_tally["zero_chunk"] += 1
                    _bump_folder(folder_name, "zero_chunk")
                elif err == "SKIPPED_TOO_LARGE":
                    global_tally["skipped_too_large"] += 1
                    _bump_folder(folder_name, "skipped_too_large")
                elif err == "SKIPPED_TOO_SMALL":
                    global_tally["skipped_too_small"] += 1
                    _bump_folder(folder_name, "skipped_too_small")
                elif err == "SKIPPED_UNSUPPORTED":
                    global_tally["skipped_unsupported"] += 1
                    _bump_folder(folder_name, "skipped_unsupported")
                elif err == "SKIPPED_EMPTY":
                    global_tally["skipped_empty"] += 1
                    _bump_folder(folder_name, "skipped_empty")
                elif err.startswith("DOWNLOAD_FAILED"):
                    global_tally["download_failed"] += 1
                    _bump_folder(folder_name, "download_failed")
                else:
                    global_tally["errors"] += 1
                    _bump_folder(folder_name, "errors")
            elif result.get("rag_indexed", 0) == 0:
                global_tally["zero_chunk"] += 1
                _bump_folder(folder_name, "zero_chunk")
            else:
                global_tally["succeeded"] += 1
                _bump_folder(folder_name, "succeeded")

        def _process_one(file_meta: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
            rel = file_meta.get("_drive_path") or file_meta.get("name", "")
            try:
                _, result = _ingest_file(
                    file_meta, project_id, data_dir, run_id, gdrive_service,
                    existing_doc=retry_doc_by_fid.get(file_meta["id"]),
                )
            except Exception as exc:  # noqa: BLE001 — one file must not kill the run
                result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            return rel, result

        # Parallel ingestion: downloads are network-bound and extraction
        # releases the GIL in fitz/torch. Keep only ``pool_size`` futures
        # in flight — submitting all 6k at once made the 4 GB worker OOM
        # after the first few SKIPPED_TOO_LARGE completions (the other
        # workers were already downloading multi-hundred-MB PDFs).
        # Ordering is already applied before offset/limit via P1B_LARGEST_FIRST.
        sort_label = "largest-first" if largest_first else "smallest-first"
        pool_size = max(1, int(os.getenv("P1B_PARALLELISM", "2")))
        tally_lock = threading.Lock()
        done_count = 0
        stop_announced = False
        print(f"[p1b-server] parallel ingestion with {pool_size} workers "
              f"({sort_label}, {len(filtered_files)} files)",
              file=sys.stderr, flush=True)
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            pending: Dict[Future, Dict[str, Any]] = {}
            file_iter = iter(filtered_files)

            def _submit_next() -> bool:
                try:
                    fm = next(file_iter)
                except StopIteration:
                    return False
                pending[pool.submit(_process_one, fm)] = fm
                return True

            for _ in range(pool_size):
                if not _submit_next():
                    break
            while pending:
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                for fut in done:
                    fm = pending.pop(fut, None)
                    rel, result = future_result_or_error(fut, fm)
                    with tally_lock:
                        done_count += 1
                        idx = done_count
                        _tally_result(rel, result)
                        status = result.get("error") or result.get("status", "ok")
                        print(
                            f"[p1b-server] {folder_name} {idx}/{len(filtered_files)} "
                            f"[{status}] {rel}",
                            file=sys.stderr,
                            flush=True,
                        )
                        if idx % 10 == 0:
                            _write_partial_report()
                    # Heartbeat outside the tally lock: this also writes the
                    # sidecar, and the signal handler runs on this same thread.
                    run.note_progress(
                        idx,
                        len(filtered_files),
                        folder=folder_name,
                        in_flight=len(pending),
                    )
                    # A stop request stops SUBMITTING but still drains what is
                    # already running, so the tally covers every file actually
                    # attempted. At most ``pool_size`` files are in flight, so
                    # the drain is bounded by the extraction timeout, not by
                    # the remaining queue.
                    if not run.should_stop():
                        _submit_next()
                    elif not stop_announced:
                        stop_announced = True
                        print(
                            f"[p1b-server] stop requested ({run.stop_reason()}) — "
                            f"draining {len(pending)} in-flight file(s), "
                            f"submitting no more",
                            file=sys.stderr,
                            flush=True,
                        )

        elapsed_folder = time.monotonic() - t0_folder
        print(f"[p1b-server] {folder_name} done in {elapsed_folder:.1f}s",
              file=sys.stderr, flush=True)
        if run.should_stop():
            print(
                f"[p1b-server] not starting further folders: "
                f"stop requested ({run.stop_reason()})",
                file=sys.stderr,
                flush=True,
            )
            break

    # Exactly one final flush, here or from the atexit/excepthook fallback.
    if run.should_stop():
        run.finish(f"signal:{run.stop_reason()}", complete=False)
        exit_code = lifecycle.signal_exit_code(
            run.stop_flag.signum or getattr(signal, "SIGTERM", 15)
        )
    else:
        run.finish(lifecycle.PHASE_COMPLETED, complete=True)
        exit_code = 0

    if args.keep_alive and not dry_run:
        print("[p1b-server] pass complete; keeping container alive for log inspection.",
              file=sys.stderr, flush=True)
        while True:
            time.sleep(3600)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
