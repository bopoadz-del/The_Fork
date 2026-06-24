"""Push V2 detection metadata to Render's photo RAG.

Architecture correction (post-0007): raw photo bytes do NOT go to Render.
Photos belong at the user's source (operator's PC, Drive, project upload
destination). Render holds only the detection metadata in photo_chunks
so the RAG retriever can surface photos by class-name query.

ONE admin endpoint:
  POST /v1/admin/photo-import -- text/plain JSONL stream

Input JSONL is the output of ``scripts/test_model_on_corpus.py``:
  {"filename": "...", "detections": [{"class": str, "conf": float, "bbox": [...]}]}

This script augments each row with `sha256` (computed from the photo file),
a templated `caption` from the detection list, and a `safety_qaqc` field in
the schema the spec defines (`{class_id, class, category, confidence, bbox}`),
then pushes.

Usage:
    python scripts/export_to_render.py \\
        --jsonl data/training/corpus_detections_v2.jsonl \\
        --photos-dir data/training/raw_photos \\
        --base-url https://the-fork.onrender.com \\
        --token "$ADMIN_TOKEN" \\
        [--state-file data/training/export_state.json] \\
        [--source-zip construction-3-001.zip] \\
        [--project-id NULL] \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.blocks.safety_classes import get_class_by_name  # noqa: E402

try:
    import httpx
except ImportError:
    print("httpx not installed; pip install httpx", file=sys.stderr)
    raise SystemExit(1)


_RETRY_BACKOFFS = (1.0, 2.0, 4.0)
_CONTENT_TYPE_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _content_type_for(path: Path) -> str:
    return _CONTENT_TYPE_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_caption(detections: List[Dict]) -> str:
    """Templated caption per the V1 spec."""
    if not detections:
        return "Site photo (no detected violations or defects)."
    safety_classes: List[str] = []
    qaqc_classes: List[str] = []
    for d in detections:
        try:
            entry = get_class_by_name(d["class"])
        except KeyError:
            continue
        if entry.category == "safety":
            safety_classes.append(d["class"])
        elif entry.category == "qaqc":
            qaqc_classes.append(d["class"])
    parts: List[str] = []
    if safety_classes:
        parts.append(f"{len(safety_classes)} safety issue(s): " + ", ".join(safety_classes))
    if qaqc_classes:
        parts.append(f"{len(qaqc_classes)} QA/QC issue(s): " + ", ".join(qaqc_classes))
    if not parts:
        return "Site photo (no detected violations or defects)."
    return "Site photo showing " + "; ".join(parts) + "."


def _normalize_detection(d: Dict) -> Optional[Dict]:
    """Reshape test_model_on_corpus.py detection -> spec's safety_qaqc shape.

    Returns None if the class isn't in our registry."""
    try:
        entry = get_class_by_name(d["class"])
    except KeyError:
        return None
    return {
        "class_id": entry.id,
        "class": entry.name,
        "category": entry.category,
        "confidence": float(d.get("conf", d.get("confidence", 0.0))),
        "bbox": [float(x) for x in d.get("bbox", [])],
    }


def _load_state(state_file: Path) -> Dict:
    if state_file.is_file():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"uploaded_sha256s": []}


def _save_state(state_file: Path, state: Dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, sort_keys=True, indent=2), encoding="utf-8")


async def _post_with_retries(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    last_exc: Optional[Exception] = None
    for backoff in (0.0, *_RETRY_BACKOFFS):
        if backoff:
            await asyncio.sleep(backoff)
        try:
            r = await client.post(url, **kwargs)
            if r.status_code >= 500:
                last_exc = RuntimeError(f"{r.status_code} on {url}")
                continue
            r.raise_for_status()
            return r
        except httpx.HTTPError as exc:
            last_exc = exc
    raise RuntimeError(f"giving up on {url}: {last_exc}")


async def run_export(
    jsonl: Path,
    photos_dir: Path,
    base_url: str,
    token: str,
    state_file: Path,
    source_zip: Optional[str],
    project_id: Optional[str],
    dry_run: bool,
) -> Dict:
    rows = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"loaded {len(rows)} rows from {jsonl}")

    state = _load_state(state_file)
    uploaded = set(state.get("uploaded_sha256s", []))

    augmented_rows: List[Tuple[Path, Dict]] = []
    skipped_missing_file = 0

    for row in rows:
        filename = row.get("filename")
        if not filename:
            continue
        img_path = photos_dir / filename
        if not img_path.is_file():
            skipped_missing_file += 1
            continue
        sha = _sha256_of(img_path)
        detections = row.get("detections", [])
        safety_qaqc = [d for d in (_normalize_detection(d) for d in detections) if d is not None]
        caption = _build_caption(safety_qaqc)
        augmented = {
            "sha256": sha,
            "filename": filename,
            "source_zip": source_zip,
            "project_id": project_id,
            "safety_qaqc": safety_qaqc,
            "caption": caption,
            "inference_failed": False,
            "inference_error": None,
        }
        augmented_rows.append((img_path, augmented))

    print(f"prepared {len(augmented_rows)} rows; {skipped_missing_file} missing files skipped")

    if dry_run:
        sample = augmented_rows[:3]
        print("\n=== dry-run sample (first 3 augmented rows) ===")
        for path, row in sample:
            print(f"\n{path.name} sha={row['sha256'][:12]}...")
            print(json.dumps({k: v for k, v in row.items() if k != "sha256"}, indent=2)[:400])
        return {"prepared": len(augmented_rows), "uploaded_bytes": 0, "imported": 0}

    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=120, headers=headers) as client:
        # Metadata-only. Raw photo bytes belong at the user's source
        # (operator's PC, Drive, project upload destination); Render only
        # holds detection metadata in photo_chunks. The augmented rows
        # carry sha256 (for dedup + future cross-reference) and a
        # source_url field when the caller knows it.
        body = "\n".join(json.dumps(r) for _path, r in augmented_rows) + "\n"
        print(f"\nposting {len(augmented_rows)} metadata rows to /v1/admin/photo-import ...")
        r = await _post_with_retries(
            client,
            f"{base_url}/v1/admin/photo-import",
            content=body,
            headers={"content-type": "text/plain", **headers},
        )
        import_result = r.json()

    summary = {
        "prepared": len(augmented_rows),
        "import_result": import_result,
    }
    print("\n=== summary ===")
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--jsonl", type=Path, required=True)
    p.add_argument("--photos-dir", type=Path, required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--token", required=True)
    p.add_argument("--state-file", type=Path, default=Path("data/training/export_state.json"))
    p.add_argument("--source-zip", default=None)
    p.add_argument("--project-id", default=None,
                   help="Project ID to attach (default None; the V1 zip has no confirmed project)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(run_export(
        args.jsonl, args.photos_dir, args.base_url.rstrip("/"),
        args.token, args.state_file, args.source_zip, args.project_id, args.dry_run,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
