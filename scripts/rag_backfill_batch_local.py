#!/usr/bin/env python3
"""Local batch RAG backfill processor.

Reads a JSON list of candidate documents, downloads each from Drive/R2, extracts
text, chunks, embeds, and uploads a combined payload to R2.  A Render-side
loader then imports all docs/chunks in one run.

Usage:
    export GDRIVE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json
    export RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
    export R2_* credentials

    python scripts/rag_backfill_batch_local.py \
        --candidates rag_backfill_indexable_candidates.json \
        --batch-index 1 \
        --project-id client_infra_pack_1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
    raise RuntimeError("No recoverable source")


CAD_BIM_EXTS: Set[str] = {
    ".dwg", ".dwt", ".dxf", ".rvt", ".nwd", ".nwc", ".rfa", ".ifc", ".ifczip",
    ".stp", ".step", ".igs", ".iges", ".sat", ".skp", ".3ds", ".max", ".blend",
    ".obj", ".fbx", ".gltf", ".glb", ".bim", ".dwf", ".dwfx", ".pln", ".pla",
    ".gsm", ".mod", ".lcf", ".pat", ".lin", ".shx", ".shp", ".sdf",
}

INDEXABLE_EXTS: Set[str] = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".rtf", ".txt",
    ".csv", ".json", ".xml", ".html", ".htm", ".md", ".vsdx", ".kmz", ".kml",
}


def _ext(name: str) -> str:
    return Path(name).suffix.lower()


def _known_file_key(original_name: str, size: int) -> Tuple[str, int]:
    return (Path(original_name).name, size)


def _zip_contents_summary(zip_path: Path) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"files": [], "has_indexable": False, "all_cad_bim": True}
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                ext = _ext(info.filename)
                is_cad_bim = ext in CAD_BIM_EXTS
                is_indexable = ext in INDEXABLE_EXTS
                summary["files"].append({"name": info.filename, "ext": ext, "size": info.file_size})
                if is_indexable:
                    summary["has_indexable"] = True
                if not is_cad_bim and not ext:
                    pass
                if not is_cad_bim:
                    summary["all_cad_bim"] = False
    except Exception as exc:  # noqa: BLE001
        summary["error"] = f"{type(exc).__name__}: {exc}"
    return summary


def _extract_zip_contents(
    zip_path: Path,
    extract_dir: Path,
    known_files: Set[Tuple[str, int]],
) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                ext = _ext(info.filename)
                if ext in CAD_BIM_EXTS:
                    extracted.append({
                        "name": info.filename,
                        "size": info.file_size,
                        "status": "SKIPPED_CAD_BIM",
                        "reason": "CAD/BIM file inside zip; not indexable",
                    })
                    continue
                if ext not in INDEXABLE_EXTS:
                    extracted.append({
                        "name": info.filename,
                        "size": info.file_size,
                        "status": "SKIPPED_UNSUPPORTED",
                        "reason": f"unsupported extension {ext}",
                    })
                    continue
                inner_path = extract_dir / info.filename
                inner_path.parent.mkdir(parents=True, exist_ok=True)
                zf.extract(info, extract_dir)
                raw = inner_path.read_bytes()
                key = _known_file_key(info.filename, len(raw))
                if key in known_files:
                    extracted.append({
                        "name": info.filename,
                        "size": len(raw),
                        "status": "SKIPPED_DUPLICATE",
                        "reason": "file already exists separately in corpus",
                    })
                    continue
                try:
                    text = extract_document_text(str(inner_path), info.filename)
                    chunks = chunk_text(text, words_per_chunk=500)
                    extracted.append({
                        "name": info.filename,
                        "size": len(raw),
                        "status": "OK" if chunks else "ZERO_CHUNK",
                        "chunk_count": len(chunks),
                        "chunks": chunks,
                    })
                except Exception as exc:  # noqa: BLE001
                    extracted.append({
                        "name": info.filename,
                        "size": len(raw),
                        "status": "ERROR",
                        "reason": f"{type(exc).__name__}: {exc}",
                    })
    except Exception as exc:  # noqa: BLE001
        extracted.append({
            "name": str(zip_path),
            "size": zip_path.stat().st_size,
            "status": "ERROR",
            "reason": f"zip open failed: {type(exc).__name__}: {exc}",
        })
    return extracted


def _extract_pdf_sub_batches(
    file_path: Path,
    filename: str,
    work_dir: Path,
    pages_per_batch: int = 50,
) -> List[str]:
    """Extract text from a large PDF in 50-page sub-batches.

    Splits the PDF into temporary range PDFs, extracts text from each,
    chunks, and returns the combined chunk list. Keeps memory bounded.
    """
    import fitz

    all_chunks: List[str] = []
    doc = fitz.open(str(file_path))
    total_pages = len(doc)
    try:
        for start in range(0, total_pages, pages_per_batch):
            end = min(start + pages_per_batch, total_pages)
            sub_doc = fitz.open()
            sub_doc.insert_pdf(doc, from_page=start, to_page=end - 1)
            sub_path = work_dir / f"{file_path.stem}_p{start+1:04d}_p{end:04d}.pdf"
            sub_doc.save(str(sub_path))
            sub_doc.close()

            text = extract_document_text(str(sub_path), filename)
            if text.strip():
                sub_chunks = chunk_text(text, words_per_chunk=500)
                all_chunks.extend(sub_chunks)
                print(f"[pdf-sub-batch] pages {start+1}-{end}: {len(sub_chunks)} chunks")
            else:
                print(f"[pdf-sub-batch] pages {start+1}-{end}: no text")
    finally:
        doc.close()
    return all_chunks


def process_doc(
    doc: Dict[str, Any],
    work_dir: Path,
    known_files: Optional[Set[Tuple[str, int]]] = None,
    pdf_pages_per_batch: int = 50,
) -> Optional[Dict[str, Any]]:
    original_name = doc["original_name"]
    doc_id = doc["doc_id"]
    suffix = Path(original_name).suffix or ".bin"
    local_path = work_dir / f"{doc_id}{suffix}"
    known_files = known_files or set()

    try:
        raw_bytes = download_source(doc)
        local_path.write_bytes(raw_bytes)

        if suffix.lower() == ".zip":
            extract_dir = work_dir / f"{doc_id}_extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)
            entries = _extract_zip_contents(local_path, extract_dir, known_files)
            all_chunks: List[str] = []
            for e in entries:
                if e.get("status") == "OK" and e.get("chunks"):
                    all_chunks.extend(e["chunks"])
            if not all_chunks:
                return {
                    "doc_id": doc_id,
                    "status": "ZERO_CHUNK",
                    "original_name": original_name,
                    "error": f"zip produced no usable chunks; entries={[(e['name'], e.get('status')) for e in entries]}",
                }
            return {
                "doc_id": doc_id,
                "status": "OK",
                "original_name": original_name,
                "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "size": len(raw_bytes),
                "chunk_count": len(all_chunks),
                "chunks": all_chunks,
                "zip_entries": entries,
            }

        if suffix.lower() == ".pdf":
            chunks = _extract_pdf_sub_batches(local_path, original_name, work_dir, pdf_pages_per_batch)
        else:
            text = extract_document_text(str(local_path), original_name)
            chunks = chunk_text(text, words_per_chunk=500)

        if not chunks:
            return {
                "doc_id": doc_id,
                "status": "ZERO_CHUNK",
                "original_name": original_name,
                "error": "no chunks produced",
            }
        return {
            "doc_id": doc_id,
            "status": "OK",
            "original_name": original_name,
            "content_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size": len(raw_bytes),
            "chunk_count": len(chunks),
            "chunks": chunks,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "doc_id": doc_id,
            "status": "ERROR",
            "original_name": original_name,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Local batch RAG backfill processor")
    parser.add_argument("--candidates", required=True, help="Path to candidates+batches JSON")
    parser.add_argument("--batch-index", type=int, required=True, help="1-based batch index")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args()

    with open(args.candidates, "r", encoding="utf-8") as f:
        data = json.load(f)
    batches = data["batches"]
    if args.batch_index < 1 or args.batch_index > len(batches):
        print(f"Invalid batch index {args.batch_index}; {len(batches)} batches available", file=sys.stderr)
        return 1

    batch = batches[args.batch_index - 1]
    print(f"[batch-local] processing batch {args.batch_index}/{len(batches)}: {len(batch)} docs, "
          f"{sum(c['size'] for c in batch)/1024/1024:.1f} MB")

    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="rag_batch_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    embedder = get_embedder()
    print(f"[batch-local] embedder: {embedder.model_name} dim={embedder.dim}")

    # Deduplicate ZIP contents against files already known in the corpus.
    all_candidates = data.get("candidates", [])
    known_files: Set[Tuple[str, int]] = {
        _known_file_key(c["original_name"], c["size"]) for c in all_candidates
    }

    doc_results: List[Dict[str, Any]] = []
    ok_chunks: List[str] = []
    for c in batch:
        print(f"[batch-local] processing {c['doc_id']} {c['original_name'][:70]}")
        result = process_doc(c, work_dir, known_files=known_files)
        if result.get("status") == "OK":
            ok_chunks.extend(result["chunks"])
        doc_results.append(result)

    if not ok_chunks:
        print("[batch-local] no chunks produced for any doc; aborting payload upload", file=sys.stderr)
        return 1

    print(f"[batch-local] embedding {len(ok_chunks)} chunks")
    embeddings = embedder.encode(ok_chunks)
    print(f"[batch-local] embeddings shape: {embeddings.shape}")

    # Build per-doc chunk slices
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
    key = f"projects/{args.project_id}/backfill/batch_{args.batch_index}_{ts}.json"
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    result = r2_storage.archive_document(
        project_id=args.project_id,
        drive_file_id=f"batch_{args.batch_index}_{ts}",
        original_name=f"batch_{args.batch_index}_{ts}.json",
        raw_bytes=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )
    if result.get("error"):
        raise RuntimeError(f"R2 upload failed: {result['error']}")

    print(f"[batch-local] OK: {result['r2_object_key']}")
    print(f"[batch-local] successful docs: {len(payload_docs)} / {len(batch)}")
    if payload["errors"]:
        print(f"[batch-local] errors: {len(payload['errors'])}")
        for e in payload["errors"]:
            print(f"  - {e['doc_id']} {e['status']}: {e['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
