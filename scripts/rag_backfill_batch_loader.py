#!/usr/bin/env python3
"""Render-side batch RAG backfill loader.

Reads a combined backfill payload JSON from R2 and imports all docs/chunks
using VectorStore.upsert_chunks per document.  Verifies per-doc chunk counts.

Usage (on Render worker):
    python scripts/rag_backfill_batch_loader.py \
        --r2-key projects/<project_id>/backfill/batch_1_<ts>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

import app.core.r2_storage as r2_storage  # noqa: E402
from app.core.rag.vector_store import get_store  # noqa: E402


def download_payload(r2_key: str) -> Dict[str, Any]:
    s3 = r2_storage._client()
    bucket = r2_storage._bucket_name()
    if not s3 or not bucket:
        raise RuntimeError("R2 not configured")
    resp = s3.get_object(Bucket=bucket, Key=r2_key)
    raw = resp["Body"].read().decode("utf-8")
    return json.loads(raw)


def load_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    store = get_store(model_name=payload["embedding_model"])
    print(f"[batch-loader] store dim={store.dim} model={store.model_name}")

    if payload["embedding_dim"] != store.dim:
        raise RuntimeError(f"Dimension mismatch: payload {payload['embedding_dim']} vs store {store.dim}")

    results: Dict[str, Any] = {"successful": 0, "zero_chunk": 0, "errors": [], "chunks_inserted": 0}

    for doc in payload["docs"]:
        doc_id = doc["doc_id"]
        project_id = doc["project_id"]
        chunks = [c["text"] for c in doc["chunks"]]
        embeddings = np.asarray([c["embedding"] for c in doc["chunks"]], dtype=np.float32)
        try:
            written = store.upsert_chunks(project_id, doc_id, chunks, embeddings)
            results["successful"] += 1
            results["chunks_inserted"] += written
            print(f"[batch-loader] {doc_id}: upserted {written} chunks")
        except Exception as exc:  # noqa: BLE001
            results["errors"].append({"doc_id": doc_id, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[batch-loader] {doc_id}: ERROR {exc}", file=sys.stderr)

    # Verify
    counts = store.count_by_doc(payload["project_id"])
    for doc in payload["docs"]:
        doc_id = doc["doc_id"]
        actual = counts.get(doc_id, 0)
        expected = doc["chunk_count"]
        if actual != expected:
            print(f"[batch-loader] VERIFY FAIL {doc_id}: expected {expected}, got {actual}", file=sys.stderr)
        else:
            print(f"[batch-loader] VERIFY OK {doc_id}: {actual} chunks")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Render batch RAG backfill loader")
    parser.add_argument("--r2-key", required=True)
    args = parser.parse_args()

    print(f"[batch-loader] loading payload from R2: {args.r2_key}")
    payload = download_payload(args.r2_key)
    print(f"[batch-loader] payload docs: {len(payload.get('docs', []))}")
    print(f"[batch-loader] payload errors from local: {len(payload.get('errors', []))}")

    results = load_payload(payload)
    print(f"[batch-loader] successful docs: {results['successful']}")
    print(f"[batch-loader] chunks inserted: {results['chunks_inserted']}")
    print(f"[batch-loader] loader errors: {len(results['errors'])}")
    if results["errors"]:
        for e in results["errors"]:
            print(f"  - {e['doc_id']}: {e['error']}")
    return 0 if not results["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
