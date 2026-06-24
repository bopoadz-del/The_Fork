"""Admin endpoint for pushing photo detection metadata into the platform's photo RAG.

ONE endpoint:
  POST /v1/admin/photo-import -- text/plain JSONL body. One row per photo.
  Inserts into ``photo_chunks`` (chunk_id=sha256). Each row should carry a
  ``source_url`` if the UI needs to render an ``<img src>`` for the citation;
  Render does NOT store raw photo bytes (architecture correction post-merge:
  photos belong at the user's source, not on Render).

Schema: see alembic/versions/0006_photo_chunks_and_photos.py +
         alembic/versions/0007_drop_photos_table.py.

Auth: same admin gate as app/routers/admin.py.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from app.core.db import get_engine
from app.dependencies import require_api_key

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_admin(auth: Dict[str, Any]) -> None:
    if auth.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


@router.post("/v1/admin/photo-import")
async def photo_import(
    request: Request,
    auth: dict = Depends(require_api_key),
) -> Dict[str, Any]:
    """JSONL stream of photo_metadata rows. Inserts into photo_chunks.

    Behavior:
      - Idempotent on sha256 (UNIQUE constraint on photo_chunks.sha256).
      - Bad-JSON or missing-sha256 rows are recorded in errors but don't abort.
      - The full row JSON is stored in photo_chunks.photo_metadata so future
        consumers can read source_url, project_id, safety_qaqc, etc.
    """
    _require_admin(auth)

    body = (await request.body()).decode("utf-8")
    inserted = skipped_duplicate = 0
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
        "errors": errors,
    }
