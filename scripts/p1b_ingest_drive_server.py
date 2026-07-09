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
import sys
import time
import uuid
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
    from app.core import doc_index, file_crypto, projects as projects_mod

    rel = file_meta.get("_drive_path") or file_meta.get("name", "")
    mime = file_meta.get("mimeType", "")
    ext = Path(rel).suffix.lower()

    # Skip Google-native and known-unsupported files cleanly.
    if mime in _UNSUPPORTED_MIMES or ext in _UNSUPPORTED_EXTS:
        return rel, {
            "status": "error",
            "error": "SKIPPED_UNSUPPORTED",
            "mimeType": mime,
        }

    # Size guard before download.
    size = int(file_meta.get("size") or 0)
    max_size = int(os.getenv("P1B_MAX_FILE_SIZE_MB", "100")) * 1024 * 1024
    if max_size > 0 and size > max_size:
        return rel, {
            "status": "error",
            "error": "SKIPPED_TOO_LARGE",
            "size_mb": round(size / (1024 * 1024), 1),
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

    if existing_doc is not None:
        # Zero-chunk retry: the row exists from a failed pass; re-index it
        # in place. The stored name is deterministic, so the fresh download
        # above landed at the same path the row's file_path points to.
        result = doc_index.index_document(project_id, existing_doc["id"])
        result["reindexed_existing_doc"] = True
        return rel, result

    doc = projects_mod.add_document(
        project_id=project_id,
        original_name=Path(rel).name,
        stored_as=stored_name,
        file_path=str(dest),
        size=size,
        content_sha256=content_sha,
        metadata={
            "drive_file_id": file_meta["id"],
            "drive_path": rel,
            "source": "p1b_server_drive_reingestion",
            "ingestion_run_id": run_id,
            "mimeType": mime,
        },
    )
    result = doc_index.index_document(project_id, doc["id"])
    return rel, result


def _load_priority_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Priority manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Server-side Drive ingestion by priority tier")
    ap.add_argument("--priority-manifest", default="manifests/p1b_priority_manifest.json",
                    help="Path to the priority manifest")
    ap.add_argument("--tier", type=int, default=1,
                    help="Ingest only this tier (1, 2, or 3)")
    ap.add_argument("--output", default="manifests/p1b_server_ingestion_report.json",
                    help="Report path")
    ap.add_argument("--resume", action="store_true",
                    help="Skip files already indexed in the project (by drive_file_id)")
    ap.add_argument("--keep-alive", action="store_true",
                    help="After the ingestion pass completes, sleep forever so a background worker stays live")
    args = ap.parse_args()

    from app.core import gdrive_service, projects as projects_mod
    from app.core.rag import embeddings as _emb, vector_store as _vs

    if not gdrive_service.is_configured():
        print("ERROR: GDRIVE_SERVICE_ACCOUNT_JSON is not set.", file=sys.stderr)
        return 1

    _emb.reset_embedder_cache()
    _vs.reset_store_cache()

    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    priority_manifest = _load_priority_manifest(Path(args.priority_manifest))
    tier_key = str(args.tier)
    tier = priority_manifest.get("tiers", {}).get(tier_key)
    if not tier:
        print(f"ERROR: tier {args.tier} not found in priority manifest.", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex[:12]
    print(f"[p1b-server] run_id={run_id} tier={args.tier} folders={len(tier['folders'])}", file=sys.stderr)

    # Track per-folder tally.
    folder_tallies: Dict[str, Dict[str, Any]] = {}
    global_tally = {
        "succeeded": 0,
        "zero_chunk": 0,
        "skipped_too_large": 0,
        "skipped_unsupported": 0,
        "skipped_empty": 0,
        "download_failed": 0,
        "errors": 0,
    }
    results: List[Dict[str, Any]] = []

    def _bump_folder(folder_name: str, key: str) -> None:
        folder_tallies.setdefault(folder_name, {
            "succeeded": 0, "zero_chunk": 0, "skipped_too_large": 0,
            "skipped_unsupported": 0, "skipped_empty": 0,
            "download_failed": 0, "errors": 0,
        })[key] += 1

    def _write_partial_report() -> None:
        partial = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "tier": args.tier,
            "embedder": os.environ["RAG_EMBEDDING_MODEL"],
            "namespace": os.environ["RAG_VECTOR_NAMESPACE"],
            "global_tally": global_tally,
            "folder_tallies": folder_tallies,
            "results": results,
        }
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(partial, fh, indent=2, ensure_ascii=False)

    t0_global = time.monotonic()
    for folder_entry in tier["folders"]:
        folder_id = folder_entry.get("folder_id")
        folder_name = folder_entry.get("folder_name") or folder_entry.get("project_id")
        project_id_for_folder = folder_entry.get("project_id")

        if not folder_id:
            print(f"[p1b-server] SKIP {folder_name}: no folder_id (needs Chadi to set)", file=sys.stderr)
            continue

        print(f"[p1b-server] walking {folder_name} ({folder_id}) ...", file=sys.stderr)
        files, walk_errors = gdrive_service.walk_folder(folder_id, max_depth=12, page_size=200)
        if walk_errors:
            for err in walk_errors:
                print(f"[p1b-server] WALK ERROR: {err}", file=sys.stderr)

        # Filter already-indexed files when resuming. A document row alone is
        # NOT proof of success — add_document runs before indexing, so a doc
        # whose chunk insert failed (e.g. the NUL-byte DataError) has a row
        # and zero chunks. Skip only files whose doc actually has chunks in
        # the active namespace; zero-chunk docs are re-indexed in place.
        already_indexed: set[str] = set()
        retry_doc_by_fid: Dict[str, Dict[str, Any]] = {}
        existing = None
        if args.resume:
            # Find the platform project for this folder; create if missing.
            for p in projects_mod.list_projects():
                if p.get("name") == folder_name:
                    existing = p
                    break
            if existing:
                chunk_counts: Dict[str, int] = {}
                counts_available = True
                try:
                    chunk_counts = _vs.get_store().count_by_doc(existing["id"])
                except Exception as exc:  # noqa: BLE001 — degrade to row-only resume
                    print(f"[p1b-server] WARN: chunk-count resume check failed "
                          f"({type(exc).__name__}: {exc}); falling back to "
                          f"row-presence resume", file=sys.stderr)
                    counts_available = False
                for doc in projects_mod.list_documents(existing["id"]):
                    fid = (doc.get("metadata") or {}).get("drive_file_id")
                    if not fid:
                        continue
                    if not counts_available:
                        # Row-presence resume (legacy behaviour): can't tell
                        # zero-chunk docs apart, so skip anything with a row.
                        already_indexed.add(fid)
                    elif chunk_counts.get(doc["id"], 0) > 0:
                        already_indexed.add(fid)
                    elif fid not in retry_doc_by_fid:
                        retry_doc_by_fid[fid] = doc

        filtered_files = [f for f in files if f["id"] not in already_indexed]
        print(f"[p1b-server] {folder_name}: {len(files)} files, {len(filtered_files)} to ingest "
              f"({len(retry_doc_by_fid)} zero-chunk retries)", file=sys.stderr)

        # Ensure platform project exists.
        project = existing
        if project is None:
            project = projects_mod.create_project(folder_name)
            print(f"[p1b-server] created project {project['id']} for {folder_name}", file=sys.stderr)
        project_id = project["id"]

        t0_folder = time.monotonic()
        for idx, file_meta in enumerate(filtered_files, start=1):
            rel = file_meta.get("_drive_path") or file_meta.get("name", "")
            print(f"[p1b-server] {folder_name} {idx}/{len(filtered_files)} {rel}", file=sys.stderr)
            try:
                _, result = _ingest_file(
                    file_meta, project_id, data_dir, run_id, gdrive_service,
                    existing_doc=retry_doc_by_fid.get(file_meta["id"]),
                )
                results.append({"folder": folder_name, "path": rel, "result": result})
                if result.get("status") == "error":
                    err = result.get("error")
                    if err == "ZERO_CHUNK":
                        global_tally["zero_chunk"] += 1
                        _bump_folder(folder_name, "zero_chunk")
                    elif err == "SKIPPED_TOO_LARGE":
                        global_tally["skipped_too_large"] += 1
                        _bump_folder(folder_name, "skipped_too_large")
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
            except Exception as exc:
                global_tally["errors"] += 1
                _bump_folder(folder_name, "errors")
                results.append({
                    "folder": folder_name,
                    "path": rel,
                    "result": {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                })

            if idx % 10 == 0:
                _write_partial_report()

        elapsed_folder = time.monotonic() - t0_folder
        print(f"[p1b-server] {folder_name} done in {elapsed_folder:.1f}s", file=sys.stderr)

    elapsed_global = time.monotonic() - t0_global
    _write_partial_report()
    print(f"[p1b-server] report written to {args.output}", file=sys.stderr)
    print(
        f"[p1b-server] tier {args.tier}: {global_tally['succeeded']} succeeded, "
        f"{global_tally['zero_chunk']} zero-chunk, "
        f"{global_tally['skipped_too_large']} too-large, "
        f"{global_tally['skipped_unsupported']} unsupported, "
        f"{global_tally['errors']} errors, {elapsed_global:.1f}s",
        file=sys.stderr,
    )
    if args.keep_alive:
        print("[p1b-server] pass complete; keeping container alive for log inspection.", file=sys.stderr)
        while True:
            time.sleep(3600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
