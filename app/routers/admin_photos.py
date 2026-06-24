"""Admin endpoints for pushing photo bytes + detection metadata to the platform's photo RAG.

Two endpoints used in sequence by ``scripts/export_to_render.py``:

  1. ``POST /v1/admin/photo-bytes/{sha256}`` — multipart upload of one photo's raw bytes.
     Idempotent: existing sha256 returns 200 with ``stored: false``.
  2. ``POST /v1/admin/photo-import`` — text/plain JSONL body. One row per photo.
     Inserts into ``photo_chunks`` (chunk_id=sha256). Rejects rows whose sha256
     has no corresponding bytes in the ``photos`` table.

Schema: see alembic/versions/0006_photo_chunks_and_photos.py.

Auth: same admin gate as app/routers/admin.py — ``require_api_key`` dependency
+ ``role == "admin"`` check.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import text

from app.core.db import get_engine
from app.dependencies import require_api_key

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_PHOTO_BYTES = 25 * 1024 * 1024  # 25 MB hard cap per photo


def _require_admin(auth: Dict[str, Any]) -> None:
    if auth.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


@router.post("/v1/admin/photo-bytes/{sha256}")
async def upload_photo_bytes(
    sha256: str,
    file: UploadFile = File(...),
    auth: dict = Depends(require_api_key),
) -> Dict[str, Any]:
    """Upload raw photo bytes for the given SHA-256. Idempotent on sha256."""
    _require_admin(auth)

    if not sha256 or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
        raise HTTPException(status_code=400, detail="sha256 must be a 64-char lowercase hex string")

    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM photos WHERE sha256 = :s"),
            {"s": sha256},
        ).first()
        if existing is not None:
            return {"stored": False, "sha256": sha256}

    data = await file.read()
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail=f"photo exceeds {_MAX_PHOTO_BYTES}-byte limit")

    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != sha256.lower():
        raise HTTPException(
            status_code=400,
            detail=f"sha256 mismatch: url={sha256.lower()} body={actual_sha}",
        )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO photos (sha256, content_type, size_bytes, bytes) "
                "VALUES (:s, :c, :sz, :b)"
            ),
            {
                "s": sha256.lower(),
                "c": file.content_type or "image/jpeg",
                "sz": len(data),
                "b": data,
            },
        )

    return {"stored": True, "sha256": sha256.lower(), "size_bytes": len(data)}


@router.post("/v1/admin/photo-import")
async def photo_import(
    request: Request,
    auth: dict = Depends(require_api_key),
) -> Dict[str, Any]:
    """JSONL stream of photo_metadata rows. Inserts into photo_chunks.

    Behavior:
      - Idempotent on sha256 (UNIQUE constraint on photo_chunks.sha256).
      - Rejects rows whose sha256 has no row in photos (ordering enforced).
      - Bad-JSON or missing-sha256 rows are recorded in errors but don't abort.
    """
    _require_admin(auth)

    body = (await request.body()).decode("utf-8")
    inserted = skipped_duplicate = rejected_no_bytes = 0
    errors: List[str] = []

    engine = get_engine()
    with engine.begin() as conn:
        for line_no, line in enumerate(body.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_no}: bad JSON: {exc}")
                continue
            sha = row.get("sha256")
            if not sha or not isinstance(sha, str):
                errors.append(f"line {line_no}: missing or invalid sha256")
                continue
            sha = sha.lower()

            bytes_present = conn.execute(
                text("SELECT 1 FROM photos WHERE sha256 = :s"),
                {"s": sha},
            ).first()
            if bytes_present is None:
                rejected_no_bytes += 1
                continue

            already = conn.execute(
                text("SELECT 1 FROM photo_chunks WHERE sha256 = :s"),
                {"s": sha},
            ).first()
            if already is not None:
                skipped_duplicate += 1
                continue

            caption = row.get("caption") or "Site photo."
            project_id = row.get("project_id")  # may be None
            conn.execute(
                text(
                    "INSERT INTO photo_chunks (chunk_id, project_id, sha256, caption, photo_metadata) "
                    "VALUES (:cid, :p, :s, :c, :m)"
                ),
                {
                    "cid": sha,
                    "p": project_id,
                    "s": sha,
                    "c": caption,
                    "m": json.dumps(row),
                },
            )
            inserted += 1

    return {
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "rejected_no_bytes": rejected_no_bytes,
        "errors": errors,
    }
