"""POST /v1/chat/analyze-photo — analyze a chat-attached photo without
making it a permanent project document.

Photos attached through the chat composer are question-context, not
corpus material. Routing them through ``/v1/projects/<pid>/documents``
forced ownership checks that 404'd whenever a user opened a shared
admin-approved project (Dar Al Arkan Master Corpus and similar).

This endpoint:
  * accepts any signed-in user (no project ownership required)
  * writes the photo to an ephemeral temp file
  * runs the image block in safety_qaqc mode
  * returns ``{safety_qaqc: {count, top:[...]}, filename}``
  * deletes the temp file before returning
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.dependencies import require_user

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_PHOTO_BYTES = 25 * 1024 * 1024  # 25 MB
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}


@router.post("/v1/chat/analyze-photo")
async def analyze_chat_photo(
    file: UploadFile = File(...),
    auth: dict = Depends(require_user),
) -> Dict[str, Any]:
    original_name = (file.filename or "photo.jpg").strip() or "photo.jpg"
    original_name = os.path.basename(original_name.replace("\\", "/"))
    _, ext = os.path.splitext(original_name.lower())
    if ext not in _IMAGE_EXTS:
        raise HTTPException(400, f"Not an image extension: {ext or '<none>'}")

    data = await file.read()
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(413, f"Photo exceeds {_MAX_PHOTO_BYTES // (1024*1024)} MB limit")
    if not data:
        raise HTTPException(400, "Empty file")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="chatphoto_")
    try:
        os.write(tmp_fd, data)
    finally:
        os.close(tmp_fd)

    try:
        from app.dependencies import get_block_instance
        image_block = get_block_instance("image")
        if image_block is None:
            raise HTTPException(503, "Image block not available")

        analysis = await image_block.execute(
            {"file_path": tmp_path},
            {"mode": "safety_qaqc", "prompt": "construction safety + QA/QC scan"},
        )
        body = analysis.get("result", {}) or {}
        detections = body.get("safety_qaqc") or []
        coco = body.get("summary_by_class") or {}
        person_count = int(coco.get("person", 0))

        summary = None
        if detections:
            summary = {
                "count": len(detections),
                "top": [
                    {
                        "class": d.get("class"),
                        "confidence": round(float(d.get("confidence") or 0.0), 3),
                    }
                    for d in detections[:8]
                ],
            }

        return {
            "filename": original_name,
            "size_bytes": len(data),
            "person_count": person_count,
            "safety_qaqc": summary,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
