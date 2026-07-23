#!/usr/bin/env python3
"""Render-side RAG backfill loader.

Reads a backfill payload JSON from R2 and imports the chunks into the live
vector store using the active namespace (default v2).  This keeps the live
corpus intact: existing chunks for the document are replaced only if present,
and no documents or projects are deleted.

Usage (on Render worker):
    python scripts/rag_backfill_loader.py \
        --r2-key projects/<project_id>/backfill/<doc_id>_<ts>.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

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


def load_payload(payload: Dict[str, Any]) -> int:
    project_id = payload["project_id"]
    doc_id = payload["doc_id"]
    chunks = [c["text"] for c in payload["chunks"]]
    embeddings = np.asarray([c["embedding"] for c in payload["chunks"]], dtype=np.float32)

    store = get_store(model_name=payload["embedding_model"])
    print(f"[loader] store namespace dim={store.dim} model={store.model_name}")
    print(f"[loader] payload model={payload['embedding_model']} dim={payload['embedding_dim']}")

    if payload["embedding_dim"] != store.dim:
        raise RuntimeError(
            f"Dimension mismatch: payload {payload['embedding_dim']} vs store {store.dim}"
        )

    written = store.upsert_chunks(project_id, doc_id, chunks, embeddings)
    print(f"[loader] upserted {written} chunks for {doc_id}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Render RAG backfill loader")
    parser.add_argument("--r2-key", required=True, help="R2 object key of payload JSON")
    args = parser.parse_args()

    print(f"[loader] loading payload from R2: {args.r2_key}")
    payload = download_payload(args.r2_key)
    written = load_payload(payload)

    # Verify by counting chunks for the document.
    store = get_store(model_name=payload["embedding_model"])
    counts = store.count_by_doc(payload["project_id"])
    doc_count = counts.get(payload["doc_id"], 0)
    print(f"[loader] verified chunk count for {payload['doc_id']}: {doc_count}")

    if doc_count != written:
        print(f"[loader] WARNING: written {written} but count shows {doc_count}", file=sys.stderr)
        return 1

    print("[loader] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
