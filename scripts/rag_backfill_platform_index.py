#!/usr/bin/env python3
"""Platform-native RAG backfill for existing zero-chunk documents.

Reads a manifest of document IDs and re-indexes each one through the
platform's own doc_index pipeline (fitz text layer + OCR + chunking +
embedding). Intended to run on Render where tesseract is installed.

Usage (inside Render worker container, from /app):
    python scripts/rag_backfill_platform_index.py \
        --manifest rag_backfill_indexable_candidates.json \
        --project-id dg2_infra_pack_1 \
        --batch-index 1

Required env:
    DATABASE_URL, DATA_DIR,
    GDRIVE_SERVICE_ACCOUNT_JSON (for Drive-sourced docs),
    R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME (for R2-sourced docs),
    RAG_EMBEDDING_MODEL, RAG_VECTOR_NAMESPACE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
os.environ.setdefault("RAG_VECTOR_NAMESPACE", "v2")

from app.core import doc_index, file_crypto, gdrive_service, projects as projects_mod, r2_storage
from app.db.base import Document, SessionLocal


def _safe_stored_name(original: str) -> str:
    base = Path(original).stem
    ext = Path(original).suffix
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in base)[:80]
    return f"{safe}{ext}"


def _update_file_path(doc_id: str, file_path: str) -> None:
    with SessionLocal() as session:
        document = session.get(Document, doc_id)
        if document is None:
            raise RuntimeError(f"document {doc_id} not found")
        document.file_path = file_path
        session.commit()


def _resolve_source(doc: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (source_type, r2_object_key, drive_file_id)."""
    meta = doc.get("metadata") or {}
    r2_key = doc.get("r2_object_key") or meta.get("r2_object_key")
    drive_id = meta.get("drive_file_id")
    return ("r2" if r2_key else "drive" if drive_id else "unknown", r2_key, drive_id)


def _download_bytes(
    source_type: str,
    r2_key: Optional[str],
    drive_id: Optional[str],
) -> Tuple[Optional[bytes], Optional[str]]:
    if source_type == "r2" and r2_key:
        try:
            s3 = r2_storage._client()
            bucket = r2_storage._bucket_name()
            if not s3 or not bucket:
                return None, "R2 not configured"
            resp = s3.get_object(Bucket=bucket, Key=r2_key)
            return resp["Body"].read(), None
        except Exception as exc:  # noqa: BLE001
            return None, f"R2 download failed: {exc}"
    if source_type == "drive" and drive_id:
        return gdrive_service.download_file_bytes(drive_id)
    return None, "no resolvable source"


def _index_one(
    doc_id: str,
    project_id: str,
    data_dir: Path,
    candidate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "doc_id": doc_id,
        "project_id": project_id,
        "status": "pending",
        "error": None,
        "rag_indexed": 0,
        "chunk_count": 0,
        "extracted_text_len": 0,
    }

    doc = projects_mod.get_document(doc_id)
    if doc is None:
        result["status"] = "ERROR"
        result["error"] = "document row not found"
        return result

    original_name = doc.get("original_name") or (candidate or {}).get("original_name", "unknown")
    result["original_name"] = original_name

    source_type, r2_key, drive_id = _resolve_source(doc)
    if source_type == "unknown":
        result["status"] = "ERROR"
        result["error"] = "no source pointer (r2_object_key or drive_file_id)"
        return result

    raw_bytes, err = _download_bytes(source_type, r2_key, drive_id)
    if err or raw_bytes is None:
        result["status"] = "ERROR"
        result["error"] = err or "download returned no bytes"
        return result

    content_sha = hashlib.sha256(raw_bytes).hexdigest()
    stored_name = _safe_stored_name(original_name)
    dest = data_dir / f"{content_sha[:16]}_{stored_name}"
    file_crypto.write_document(str(dest), raw_bytes)

    # Ensure file_path is fresh so index_document can read it.
    _update_file_path(doc_id, str(dest))

    # Best-effort R2 archive update if missing.
    if not r2_key:
        try:
            archive = r2_storage.archive_document(
                project_id=project_id,
                drive_file_id=drive_id or doc_id,
                original_name=original_name,
                raw_bytes=raw_bytes,
                content_sha256=content_sha,
            )
            if archive.get("r2_object_key"):
                projects_mod.update_document_metadata(doc_id, {
                    "r2_object_key": archive["r2_object_key"],
                    "r2_bucket": archive.get("r2_bucket"),
                    "r2_endpoint": archive.get("r2_endpoint"),
                    "r2_account_id": archive.get("r2_account_id"),
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[platform-index] {doc_id}: R2 archive warning: {exc}", file=sys.stderr)

    # Index through platform pipeline.
    idx_result = doc_index.index_document(project_id, doc_id)
    result["rag_indexed"] = int(idx_result.get("rag_indexed", 0) or idx_result.get("indexed", 0))
    result["status"] = "OK" if result["rag_indexed"] > 0 else "ZERO_CHUNK"
    if idx_result.get("error"):
        result["error"] = str(idx_result["error"])

    # Introspection for reporting.
    try:
        text, meta = doc_index._extract_with_meta(str(dest), original_name)
        result["extracted_text_len"] = len(text)
        result["chunk_count"] = len(doc_index.chunk_text(text))
        if meta.get("ocr_low_quality"):
            result["ocr_low_quality"] = True
        if meta.get("ocr_truncated"):
            result["ocr_truncated"] = True
    except Exception as exc:  # noqa: BLE001
        result["introspection_error"] = f"{type(exc).__name__}: {exc}"

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Platform-native RAG backfill indexer")
    parser.add_argument("--manifest", required=True, help="Path to candidates+batches JSON")
    parser.add_argument("--batch-index", type=int, required=True, help="1-based batch index")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--resume", action="store_true", help="Skip docs already rag_indexed > 0")
    args = parser.parse_args()

    if not gdrive_service.is_configured():
        print("WARNING: GDRIVE_SERVICE_ACCOUNT_JSON not set; Drive-sourced docs will fail", file=sys.stderr)

    with open(args.manifest, "r", encoding="utf-8") as f:
        data = json.load(f)
    batches = data["batches"]
    candidates = {c["doc_id"]: c for c in data.get("candidates", [])}

    if args.batch_index < 1 or args.batch_index > len(batches):
        print(f"Invalid batch index {args.batch_index}; {len(batches)} batches available", file=sys.stderr)
        return 1

    batch = batches[args.batch_index - 1]
    print(f"[platform-index] batch {args.batch_index}/{len(batches)}: {len(batch)} docs, "
          f"{sum(c.get('size', 0) for c in batch)/1024/1024:.1f} MB")

    data_dir = Path(args.data_dir) if args.data_dir else Path(os.getenv("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    for c in batch:
        doc_id = c["doc_id"]
        print(f"[platform-index] processing {doc_id} {c.get('original_name', '')[:60]}")

        if args.resume:
            doc = projects_mod.get_document(doc_id)
            if doc and (doc.get("metadata") or {}).get("rag_indexed", 0) > 0:
                print(f"[platform-index] {doc_id}: already indexed, skipping")
                results.append({"doc_id": doc_id, "status": "SKIPPED_ALREADY_INDEXED"})
                continue

        result = _index_one(doc_id, args.project_id, data_dir, candidate=c)
        results.append(result)
        print(f"[platform-index] {doc_id}: status={result['status']} rag_indexed={result['rag_indexed']} "
              f"chunks={result['chunk_count']} text_len={result['extracted_text_len']}")

    report_path = data_dir / f"platform_index_batch_{args.batch_index}_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "project_id": args.project_id,
            "batch_index": args.batch_index,
            "manifest": args.manifest,
            "results": results,
            "summary": {
                "total": len(results),
                "ok": sum(1 for r in results if r.get("status") == "OK"),
                "zero_chunk": sum(1 for r in results if r.get("status") == "ZERO_CHUNK"),
                "error": sum(1 for r in results if r.get("status") == "ERROR"),
                "skipped": sum(1 for r in results if "SKIPPED" in str(r.get("status", ""))),
            },
        }, f, indent=2, ensure_ascii=False)
    print(f"[platform-index] report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
