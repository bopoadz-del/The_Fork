#!/usr/bin/env python3
"""Render-side RAG cleanup: remove unsupported zero-chunk document rows.

Reads a JSON list of target doc_ids from R2 (or local path), deletes matching
documents from the documents table for the given project, and reports counts.
No chunks are deleted because the targets are zero-chunk docs.

Usage (on Render worker):
    python scripts/rag_cleanup_unsupported.py \
        --project-id client_infra_pack_1 \
        --targets-r2-key projects/<pid>/cleanup/rag_cleanup_targets.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import app.core.r2_storage as r2_storage  # noqa: E402
from app.core.db import get_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402


def load_targets(path_or_r2: str) -> List[Dict[str, Any]]:
    if path_or_r2.startswith("projects/") or path_or_r2.startswith("rag_"):
        # Treat as R2 key
        s3 = r2_storage._client()
        bucket = r2_storage._bucket_name()
        if not s3 or not bucket:
            raise RuntimeError("R2 not configured")
        resp = s3.get_object(Bucket=bucket, Key=path_or_r2)
        raw = resp["Body"].read().decode("utf-8")
    else:
        with open(path_or_r2, "r", encoding="utf-8") as f:
            raw = f.read()
    return json.loads(raw)


def cleanup(project_id: str, targets: List[Dict[str, Any]], dry_run: bool = False) -> Dict[str, int]:
    doc_ids = [t["doc_id"] for t in targets]
    engine = get_engine()
    namespace = os.getenv("RAG_VECTOR_NAMESPACE", "v2")
    chunks_table = f"chunks_{namespace}"

    results = {"documents_before": 0, "chunks_before": 0, "documents_deleted": 0, "chunks_deleted": 0}

    with engine.connect() as conn:
        # Counts before
        results["documents_before"] = conn.execute(
            text("SELECT COUNT(*) FROM documents WHERE project_id = :pid"),
            {"pid": project_id},
        ).scalar() or 0
        results["chunks_before"] = conn.execute(
            text(f"SELECT COUNT(*) FROM {chunks_table} WHERE project_id = :pid"),
            {"pid": project_id},
        ).scalar() or 0

        if dry_run:
            results["documents_deleted"] = len(doc_ids)
            results["chunks_deleted"] = conn.execute(
                text(f"SELECT COUNT(*) FROM {chunks_table} WHERE project_id = :pid AND doc_id = ANY(:doc_ids)"),
                {"pid": project_id, "doc_ids": doc_ids},
            ).scalar() or 0
            return results

        # Delete chunks first (should be zero, but keep FK order clean)
        chunk_del = conn.execute(
            text(f"DELETE FROM {chunks_table} WHERE project_id = :pid AND doc_id = ANY(:doc_ids)"),
            {"pid": project_id, "doc_ids": doc_ids},
        )
        results["chunks_deleted"] = chunk_del.rowcount or 0

        # Delete documents
        doc_del = conn.execute(
            text("DELETE FROM documents WHERE project_id = :pid AND id = ANY(:doc_ids)"),
            {"pid": project_id, "doc_ids": doc_ids},
        )
        results["documents_deleted"] = doc_del.rowcount or 0

        conn.commit()

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove unsupported zero-chunk documents from RAG")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--targets", required=True, help="Path or R2 key to cleanup_targets.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    targets = load_targets(args.targets)
    print(f"[cleanup] loaded {len(targets)} target doc_ids")
    print(f"[cleanup] dry_run={args.dry_run}")

    results = cleanup(args.project_id, targets, dry_run=args.dry_run)
    print(f"[cleanup] documents before: {results['documents_before']}")
    print(f"[cleanup] chunks before: {results['chunks_before']}")
    print(f"[cleanup] documents deleted: {results['documents_deleted']}")
    print(f"[cleanup] chunks deleted: {results['chunks_deleted']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
