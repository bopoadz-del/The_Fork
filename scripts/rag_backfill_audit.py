#!/usr/bin/env python3
"""Render-side RAG backfill audit.

Reads the existing Render Postgres (via DATABASE_URL) and produces an audit JSON
identifying missing / failed / zero-chunk / unindexed documents for a target
project.  The audit is uploaded to R2 so it can be downloaded locally.

Usage (on Render worker):
    python scripts/rag_backfill_audit.py --project-id client_infra_pack_1

Outputs:
    R2: projects/<project_id>/audits/rag_backfill_audit_<timestamp>.json
    Logs: audit summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.core import r2_storage  # noqa: E402
from app.core.db import get_engine  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _iso(value: Any) -> Optional[str]:
    """Serialize a datetime or ISO string to ISO format."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def fetch_documents(project_id: str) -> List[Dict[str, Any]]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, original_name, stored_as, file_path, size,
                       content_sha256, metadata, uploaded_at
                FROM documents
                WHERE project_id = :project_id
                ORDER BY uploaded_at DESC
                """
            ),
            {"project_id": project_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def fetch_chunk_counts(project_id: str) -> Dict[str, int]:
    engine = get_engine()
    # Discover active chunks namespace from env; default to v2.
    namespace = os.getenv("RAG_VECTOR_NAMESPACE", "v2")
    table = f"chunks_{namespace}"
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT doc_id, COUNT(*) as cnt
                FROM {table}
                WHERE project_id = :project_id
                GROUP BY doc_id
            """),
            {"project_id": project_id},
        ).mappings().all()
    return {r["doc_id"]: r["cnt"] for r in rows}


def source_exists(file_path: Optional[str]) -> bool:
    if not file_path:
        return False
    try:
        return Path(file_path).exists() and Path(file_path).stat().st_size > 0
    except OSError:
        return False


def build_audit(project_id: str) -> Dict[str, Any]:
    docs = fetch_documents(project_id)
    chunk_counts = fetch_chunk_counts(project_id)

    audit: Dict[str, Any] = {
        "project_id": project_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_documents": len(docs),
        "total_chunks": sum(chunk_counts.values()),
        "zero_chunk_documents": [],
        "unindexed_documents": [],
        "missing_source_documents": [],
        "duplicate_drive_file_ids": {},
        "duplicate_content_sha256": {},
        "docs_with_r2_key": 0,
        "docs_with_drive_id": 0,
        "docs_with_source_file": 0,
        "all_documents": [],
    }

    drive_id_map: Dict[str, List[str]] = {}
    sha_map: Dict[str, List[str]] = {}
    audit["document_errors"] = []

    for d in docs:
        doc_id = d["id"]
        try:
            meta = d.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}
            chunk_count = chunk_counts.get(doc_id, 0)
            has_source = source_exists(d.get("file_path"))
            drive_file_id = meta.get("drive_file_id") or meta.get("driveFileId")
            r2_key = meta.get("r2_object_key")

            record = {
                "doc_id": doc_id,
                "original_name": d["original_name"],
                "stored_as": d["stored_as"],
                "file_path": d["file_path"],
                "size": d["size"],
                "content_sha256": d["content_sha256"],
                "metadata": meta,
                "chunk_count": chunk_count,
                "has_source_file": has_source,
                "drive_file_id": drive_file_id,
                "r2_object_key": r2_key,
                "uploaded_at": _iso(d["uploaded_at"]),
            }
            audit["all_documents"].append(record)

            if chunk_count == 0:
                audit["zero_chunk_documents"].append(record)
            if not chunk_count:
                audit["unindexed_documents"].append(record)
            if not has_source and not r2_key and not drive_file_id:
                audit["missing_source_documents"].append(record)
            if r2_key:
                audit["docs_with_r2_key"] += 1
            if drive_file_id:
                audit["docs_with_drive_id"] += 1
                drive_id_map.setdefault(drive_file_id, []).append(doc_id)
            if has_source:
                audit["docs_with_source_file"] += 1
            if d["content_sha256"]:
                sha_map.setdefault(d["content_sha256"], []).append(doc_id)
        except Exception as exc:  # noqa: BLE001
            audit["document_errors"].append({
                "doc_id": doc_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    audit["duplicate_drive_file_ids"] = {
        k: v for k, v in drive_id_map.items() if len(v) > 1
    }
    audit["duplicate_content_sha256"] = {
        k: v for k, v in sha_map.items() if len(v) > 1
    }
    return audit


def upload_audit(project_id: str, audit: Dict[str, Any]) -> Tuple[str, str]:
    ts = _now()
    key = f"projects/{project_id}/audits/rag_backfill_audit_{ts}.json"
    payload = json.dumps(audit, ensure_ascii=False, default=str).encode("utf-8")
    result = r2_storage.archive_document(
        project_id=project_id,
        drive_file_id=f"audit_{ts}",
        original_name=f"rag_backfill_audit_{ts}.json",
        raw_bytes=payload,
        content_sha256=hashlib.sha256(payload).hexdigest(),
    )
    if result.get("error"):
        raise RuntimeError(f"R2 upload failed: {result['error']}")
    return key, result["r2_object_key"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render-side RAG backfill audit")
    parser.add_argument("--project-id", required=True, help="Target project_id")
    args = parser.parse_args()

    print(f"[audit] starting for project_id={args.project_id}")
    audit = build_audit(args.project_id)

    summary = {
        "total_documents": audit["total_documents"],
        "total_chunks": audit["total_chunks"],
        "zero_chunk_documents": len(audit["zero_chunk_documents"]),
        "unindexed_documents": len(audit["unindexed_documents"]),
        "missing_source_documents": len(audit["missing_source_documents"]),
        "duplicate_drive_file_ids": len(audit["duplicate_drive_file_ids"]),
        "duplicate_content_sha256": len(audit["duplicate_content_sha256"]),
        "docs_with_r2_key": audit["docs_with_r2_key"],
        "docs_with_drive_id": audit["docs_with_drive_id"],
        "docs_with_source_file": audit["docs_with_source_file"],
        "document_errors": len(audit.get("document_errors", [])),
    }
    print(f"[audit] summary={json.dumps(summary, indent=2)}")

    r2_key, returned_key = upload_audit(args.project_id, audit)
    print(f"[audit] uploaded to R2: {returned_key}")
    print(f"[audit] download via R2 key: {returned_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
