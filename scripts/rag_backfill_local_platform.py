#!/usr/bin/env python3
"""Local RAG backfill using the platform's own extraction pipeline.

Processes one batch JSON locally (reads local Drive paths), extracts text via
app.core.doc_index, chunks, embeds, and writes a payload JSON ready for the
Render loader. OCR requires tesseract installed locally.

Usage:
    .venv/Scripts/python scripts/rag_backfill_local_platform.py \
        --batch rag_backfill_batch_5.json \
        --project-id dg2_infra_pack_1
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

os.environ.setdefault("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
os.environ.setdefault("RAG_VECTOR_NAMESPACE", "v2")

import app.core.r2_storage as r2_storage  # noqa: E402
from app.core import doc_index  # noqa: E402
from app.core.gdrive_service import download_file_bytes  # noqa: E402
from app.core.rag.embeddings import get_embedder  # noqa: E402


def _download_source(candidate: Dict[str, Any]) -> Optional[bytes]:
    drive_id = candidate.get("drive_file_id")
    if drive_id:
        blob, _ = download_file_bytes(drive_id)
        return blob
    return None


def _read_local_file(candidate: Dict[str, Any]) -> Optional[bytes]:
    local_path = candidate.get("local_path")
    if local_path and Path(local_path).exists():
        return Path(local_path).read_bytes()
    return None


def process_file(candidate: Dict[str, Any], work_dir: Path) -> Dict[str, Any]:
    original_name = candidate["original_name"]
    doc_id = candidate["doc_id"]

    raw_bytes = _read_local_file(candidate)
    source = "local"
    if raw_bytes is None:
        raw_bytes = _download_source(candidate)
        source = "drive"
    if raw_bytes is None:
        return {
            "doc_id": doc_id,
            "status": "ERROR",
            "original_name": original_name,
            "error": "no local path and no Drive download",
        }

    suffix = Path(original_name).suffix or ".bin"
    local_path = work_dir / f"{doc_id}{suffix}"
    local_path.write_bytes(raw_bytes)

    try:
        text, meta = doc_index._extract_with_meta(str(local_path), original_name)
        chunks = doc_index.chunk_text(text, words_per_chunk=500)
        if not chunks:
            return {
                "doc_id": doc_id,
                "status": "ZERO_CHUNK",
                "original_name": original_name,
                "error": "no chunks produced",
                "source": source,
            }
        return {
            "doc_id": doc_id,
            "status": "OK",
            "original_name": original_name,
            "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size": len(raw_bytes),
            "chunk_count": len(chunks),
            "chunks": chunks,
            "extracted_text_len": len(text),
            "ocr_low_quality": meta.get("ocr_low_quality", False),
            "ocr_truncated": meta.get("ocr_truncated", False),
            "source": source,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "doc_id": doc_id,
            "status": "ERROR",
            "original_name": original_name,
            "error": f"{type(exc).__name__}: {exc}",
            "source": source,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Local platform backfill processor")
    parser.add_argument("--batch", required=True, help="Path to batch JSON")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args()

    with open(args.batch, "r", encoding="utf-8") as f:
        batch_data = json.load(f)
    files = batch_data["files"]
    print(f"[local-platform] batch {batch_data['batch_id']}: {len(files)} files, "
          f"{sum(c.get('size', 0) for c in files)/1024/1024:.1f} MB")

    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="rag_batch_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    embedder = get_embedder()
    print(f"[local-platform] embedder: {embedder.model_name} dim={embedder.dim}")

    doc_results: List[Dict[str, Any]] = []
    ok_chunks: List[str] = []
    for c in files:
        print(f"[local-platform] processing {c['doc_id']} {c['original_name'][:60]}")
        result = process_file(c, work_dir)
        if result.get("status") == "OK":
            ok_chunks.extend(result["chunks"])
        doc_results.append(result)

    if not ok_chunks:
        print("[local-platform] no chunks produced for any doc; aborting", file=sys.stderr)
        return 1

    print(f"[local-platform] embedding {len(ok_chunks)} chunks")
    embeddings = embedder.encode(ok_chunks)

    payload_docs = []
    chunk_offset = 0
    for result in doc_results:
        if result.get("status") != "OK":
            continue
        n = result["chunk_count"]
        doc_embs = embeddings[chunk_offset:chunk_offset + n]
        chunk_offset += n
        payload_docs.append({
            "doc_id": result["doc_id"],
            "project_id": args.project_id,
            "original_name": result["original_name"],
            "content_sha256": result["content_sha256"],
            "size": result["size"],
            "embedding_model": embedder.model_name,
            "embedding_dim": embedder.dim,
            "chunk_count": n,
            "chunks": [
                {"chunk_index": i, "text": txt, "embedding": vec.tolist()}
                for i, (txt, vec) in enumerate(zip(result["chunks"], doc_embs))
            ],
        })

    payload = {
        "project_id": args.project_id,
        "embedding_model": embedder.model_name,
        "embedding_dim": embedder.dim,
        "docs": payload_docs,
        "errors": [r for r in doc_results if r.get("status") != "OK"],
    }

    ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%d_%H%M%S")
    payload_path = work_dir / f"batch_{batch_data['batch_id']}_{ts}.json"
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, default=str)

    # Best-effort R2 archive of the payload.
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    r2_result = r2_storage.archive_document(
        project_id=args.project_id,
        drive_file_id=f"batch_{batch_data['batch_id']}_{ts}",
        original_name=f"batch_{batch_data['batch_id']}_{ts}.json",
        raw_bytes=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )

    print(f"\n[local-platform] OK docs: {len(payload_docs)}/{len(files)}")
    print(f"[local-platform] chunks: {len(ok_chunks)}")
    print(f"[local-platform] errors: {len(payload['errors'])}")
    print(f"[local-platform] local payload: {payload_path}")
    if r2_result.get("r2_object_key"):
        print(f"[local-platform] R2 key: {r2_result['r2_object_key']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
