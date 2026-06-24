"""Public photo bytes serving — ``GET /v1/photos/{sha256}``.

Used as the citation target for photo chunks surfaced by RAG retrieval.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import text

from app.core.db import get_engine

router = APIRouter()


@router.get("/v1/photos/{sha256}")
async def get_photo(sha256: str) -> Response:
    """Return raw photo bytes by SHA-256. Public read (no auth).

    Phase 3 TODO: project-scope check against photo_chunks.project_id when
    photos start being uploaded under specific projects via the platform UI.
    """
    if not sha256 or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
        raise HTTPException(status_code=400, detail="sha256 must be a 64-char lowercase hex string")

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT content_type, bytes FROM photos WHERE sha256 = :s"),
            {"s": sha256.lower()},
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="photo not found")
    return Response(
        content=row.bytes,
        media_type=row.content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
