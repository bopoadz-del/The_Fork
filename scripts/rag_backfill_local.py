#!/usr/bin/env python3
"""Local/offline RAG backfill processor.

Reads the Render-side audit JSON, picks a zero-chunk document with a
recoverable source (Drive file ID or R2 object key), downloads the raw file,
extracts text, chunks it, embeds the chunks with the production embedder, and
uploads a payload to R2.  A companion Render-side loader
(scripts/rag_backfill_loader.py) then imports the payload into the live corpus.

Usage:
    export GDRIVE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json
    export RAG_EMBEDDING_MODEL=minishlab/potion-base-8M
    export R2_ACCESS_KEY_ID=...
    export R2_SECRET_ACCESS_KEY=...
    export R2_ENDPOINT_URL=...
    export R2_BUCKET_NAME=theshovel-raw-docs

    python scripts/rag_backfill_local.py \
        --audit rag_backfill_audit_latest.json \
        --doc-id fd95077e... \
        --project-id client_infra_pack_1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import app.core.r2_storage as r2_storage  # noqa: E402
from app.core.doc_index import chunk_text, extract_document_text  # noqa: E402
from app.core.gdrive_service import download_file_bytes  # noqa: E402
from app.core.rag.embeddings import get_embedder  # noqa: E402


def find_document(audit: Dict[str, Any], doc_id: str) -> Optional[Dict[str, Any]]:
    for d in audit.get("all_documents", []):
        if d["doc_id"] == doc_id:
            return d
    return None


def download_source(doc: Dict[str, Any]) -> bytes:
    """Download raw file bytes from Drive or R2."""
    r2_key = doc.get("r2_object_key")
    drive_id = doc.get("drive_file_id")

    if r2_key:
        s3 = r2_storage._client()
        bucket = r2_storage._bucket_name()
        if not s3 or not bucket:
            raise RuntimeError("R2 not configured")
        resp = s3.get_object(Bucket=bucket, Key=r2_key)
        return resp["Body"].read()

    if drive_id:
        blob, err = download_file_bytes(drive_id)
        if blob is None:
            raise RuntimeError(f"Drive download failed: {err}")
        return blob

    raise RuntimeError("Document has no recoverable source (no r2_object_key or drive_file_id)")


def build_payload(
    project_id: str,
    doc: Dict[str, Any],
    raw_bytes: bytes,
    work_dir: Path,
) -> Dict[str, Any]:
    original_name = doc["original_name"]
    suffix = Path(original_name).suffix or ".bin"
    local_path = work_dir / f"{doc['doc_id']}{suffix}"
    local_path.write_bytes(raw_bytes)

    print(f"[local] downloaded {len(raw_bytes)} bytes -> {local_path}")

    text = extract_document_text(str(local_path), original_name)
    print(f"[local] extracted text length: {len(text)}")
    if not text.strip():
        raise RuntimeError("Extracted text is empty")

    chunks = chunk_text(text, words_per_chunk=500)
    print(f"[local] chunks: {len(chunks)}")
    if not chunks:
        raise RuntimeError("No chunks produced")

    embedder = get_embedder()
    print(f"[local] embedder: {embedder.model_name} dim={embedder.dim}")
    embeddings = embedder.encode(chunks)
    print(f"[local] embeddings shape: {embeddings.shape}")

    payload = {
        "project_id": project_id,
        "doc_id": doc["doc_id"],
        "original_name": original_name,
        "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "embedding_model": embedder.model_name,
        "embedding_dim": embedder.dim,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_index": i,
                "text": txt,
                "embedding": vec.tolist(),
            }
            for i, (txt, vec) in enumerate(zip(chunks, embeddings))
        ],
    }
    return payload


def upload_payload(project_id: str, doc_id: str, payload: Dict[str, Any]) -> str:
    ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%d_%H%M%S")
    key = f"projects/{project_id}/backfill/{doc_id}_{ts}.json"
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    result = r2_storage.archive_document(
        project_id=project_id,
        drive_file_id=f"backfill_{doc_id}_{ts}",
        original_name=f"backfill_{doc_id}_{ts}.json",
        raw_bytes=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )
    if result.get("error"):
        raise RuntimeError(f"R2 upload failed: {result['error']}")
    returned = result["r2_object_key"]
    print(f"[local] payload uploaded to R2: {returned}")
    return returned


def main() -> int:
    parser = argparse.ArgumentParser(description="Local RAG backfill processor")
    parser.add_argument("--audit", required=True, help="Path to audit JSON")
    parser.add_argument("--doc-id", required=True, help="Document ID to backfill")
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--work-dir", default=None, help="Temp working directory")
    args = parser.parse_args()

    with open(args.audit, "r", encoding="utf-8") as f:
        audit = json.load(f)

    doc = find_document(audit, args.doc_id)
    if doc is None:
        print(f"[local] ERROR: doc_id {args.doc_id} not found in audit", file=sys.stderr)
        return 1

    print(f"[local] backfilling doc_id={args.doc_id} name={doc['original_name']}")

    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="rag_backfill_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        raw_bytes = download_source(doc)
        payload = build_payload(args.project_id, doc, raw_bytes, work_dir)
        r2_key = upload_payload(args.project_id, args.doc_id, payload)
        print(f"[local] OK: {r2_key}")
        return 0
    except Exception as exc:
        print(f"[local] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
