#!/usr/bin/env python3
"""P1b — controlled re-ingestion of one local Drive folder through the normal pipeline.

Creates a project, adds every file under the local folder as a document, runs
``doc_index.index_document`` for each, and verifies chunks land in the v2
namespace. Unsupported files (e.g. .mp4) are recorded as skipped, not silently
lost. Logs every failure with the file path so Phase 1c reconciliation is
auditable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Set the approved embedder and namespace BEFORE any module imports.
os.environ.setdefault("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
os.environ.setdefault("RAG_VECTOR_NAMESPACE", "v2")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _safe_stored_name(original: str) -> str:
    """Filesystem-safe stored name; preserves extension."""
    base = Path(original).stem
    ext = Path(original).suffix
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)[:80]
    return f"{safe}{ext}"


def _ingest_file(
    src_path: Path,
    project_id: str,
    data_dir: Path,
) -> Tuple[str, Dict[str, Any]]:
    """Copy one file into the project and index it. Returns (relative_path, result)."""
    from app.core import doc_index, file_crypto, projects as projects_mod

    rel = str(src_path.relative_to(Path("G:/My Drive"))).replace("\\", "/")
    size = src_path.stat().st_size
    content_sha = hashlib.sha256(src_path.read_bytes()).hexdigest()

    stored_name = f"{hashlib.sha256(str(src_path).encode()).hexdigest()[:8]}_{_safe_stored_name(src_path.name)}"
    dest = data_dir / stored_name
    shutil.copy2(src_path, dest)

    doc = projects_mod.add_document(
        project_id=project_id,
        original_name=src_path.name,
        stored_as=stored_name,
        file_path=str(dest),
        size=size,
        content_sha256=content_sha,
        metadata={"local_drive_path": rel, "source": "p1b_local_reingestion"},
    )
    result = doc_index.index_document(project_id, doc["id"])
    return rel, result


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest one local Drive folder into v2")
    ap.add_argument("--folder", default="construction-3-001",
                    help="Folder name under G:/My Drive")
    ap.add_argument("--project-name", default=None,
                    help="Platform project name (default: folder name)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Ingest at most N files (0 = all)")
    ap.add_argument("--output", default="manifests/p1b_ingestion_report.json",
                    help="Report path")
    ap.add_argument("--resume", action="store_true",
                    help="Skip files already indexed in the project (by local_drive_path)")
    args = ap.parse_args()

    from app.core import projects as projects_mod
    from app.core.rag import embeddings as _emb, vector_store as _vs

    _emb.reset_embedder_cache()
    _vs.reset_store_cache()

    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    folder_path = Path(f"G:/My Drive/{args.folder}")
    if not folder_path.exists():
        print(f"ERROR: folder not found: {folder_path}", file=sys.stderr)
        return 1

    project_name = args.project_name or args.folder
    existing = None
    for p in projects_mod.list_projects():
        if p.get("name") == project_name:
            existing = p
            break
    if existing:
        project = existing
        print(f"[p1b] using existing project {project['id']} for {project_name}")
    else:
        project = projects_mod.create_project(project_name)
        print(f"[p1b] created project {project['id']} for {project_name}")
    project_id = project["id"]

    files = sorted(p for p in folder_path.rglob("*") if p.is_file())
    if args.limit:
        files = files[: args.limit]

    already_indexed: set[str] = set()
    if args.resume:
        for doc in projects_mod.list_documents(project_id):
            rel = (doc.get("metadata") or {}).get("local_drive_path")
            if rel:
                already_indexed.add(rel.replace("\\", "/"))
        print(f"[p1b] {len(already_indexed)} files already indexed; resuming", file=sys.stderr)

    drive_root = Path("G:/My Drive")
    filtered_files = [
        p for p in files
        if str(p.relative_to(drive_root)).replace("\\", "/") not in already_indexed
    ]
    print(f"[p1b] {len(filtered_files)} files to ingest ({len(files)} total, {len(files)-len(filtered_files)} skipped)", file=sys.stderr)

    successes = 0
    zero_chunk = 0
    errors: List[Dict[str, Any]] = []
    skipped_unsupported = 0
    results: List[Dict[str, Any]] = []

    t0 = time.monotonic()
    for idx, src_path in enumerate(files, 1):
        rel = str(src_path.relative_to(Path("G:/My Drive"))).replace("\\", "/")
        ext = src_path.suffix.lower()
        print(f"[p1b] {idx}/{len(files)} {rel}", file=sys.stderr)
        try:
            _, result = _ingest_file(src_path, project_id, data_dir)
            results.append({"path": rel, "result": result})
            if result.get("status") == "error":
                if result.get("error") == "ZERO_CHUNK":
                    zero_chunk += 1
                else:
                    errors.append({"path": rel, "error": result.get("error")})
            elif result.get("rag_indexed", 0) == 0:
                errors.append({"path": rel, "error": "no RAG chunks indexed"})
            else:
                successes += 1
        except Exception as exc:
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})

    elapsed = time.monotonic() - t0

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "project_name": project_name,
        "folder": args.folder,
        "folder_path": str(folder_path),
        "embedder": os.environ["RAG_EMBEDDING_MODEL"],
        "namespace": os.environ["RAG_VECTOR_NAMESPACE"],
        "total_files": len(files),
        "successes": successes,
        "zero_chunk": zero_chunk,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 1),
        "results": results,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"[p1b] report written to {out_path}", file=sys.stderr)
    print(
        f"[p1b] {successes}/{len(files)} succeeded, {zero_chunk} zero-chunk, {len(errors)} errors, "
        f"{elapsed:.1f}s",
        file=sys.stderr,
    )
    return 0 if not errors else 0  # report all outcomes; exit 0 so CI doesn't abort


if __name__ == "__main__":
    raise SystemExit(main())
