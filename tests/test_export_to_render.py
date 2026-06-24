"""Tests for scripts/export_to_render.py."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from scripts.export_to_render import (
    _build_caption,
    _normalize_detection,
    _sha256_of,
    run_export,
)


@pytest.fixture
def photos_and_jsonl(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    img = photos / "a.jpg"
    Image.new("RGB", (50, 50), color=(200, 100, 50)).save(img)
    jsonl = tmp_path / "in.jsonl"
    jsonl.write_text(json.dumps({
        "filename": "a.jpg",
        "detections": [
            {"class": "no_hardhat", "conf": 0.91, "bbox": [10, 10, 30, 30]},
            {"class": "concrete_crack", "conf": 0.55, "bbox": [5, 5, 25, 25]},
        ],
    }) + "\n", encoding="utf-8")
    return photos, jsonl


def test_sha256_of_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    assert _sha256_of(p) == hashlib.sha256(b"hello world").hexdigest()


def test_build_caption_safety_and_qaqc():
    detections = [
        {"class_id": 0, "class": "no_hardhat", "category": "safety",
         "confidence": 0.9, "bbox": [0, 0, 10, 10]},
        {"class_id": 3, "class": "concrete_crack", "category": "qaqc",
         "confidence": 0.5, "bbox": [0, 0, 10, 10]},
    ]
    caption = _build_caption(detections)
    assert "no_hardhat" in caption
    assert "concrete_crack" in caption
    assert "1 safety issue" in caption
    assert "1 QA/QC issue" in caption


def test_build_caption_empty():
    assert _build_caption([]) == "Site photo (no detected violations or defects)."


def test_normalize_detection_known_class():
    out = _normalize_detection({"class": "no_hardhat", "conf": 0.9, "bbox": [1, 2, 3, 4]})
    assert out == {"class_id": 0, "class": "no_hardhat", "category": "safety",
                   "confidence": 0.9, "bbox": [1.0, 2.0, 3.0, 4.0]}


def test_normalize_detection_unknown_class_returns_none():
    assert _normalize_detection({"class": "totally_unknown", "conf": 0.9, "bbox": [1, 2, 3, 4]}) is None


@pytest.mark.asyncio
async def test_dry_run_does_not_call_http(photos_and_jsonl, tmp_path):
    photos, jsonl = photos_and_jsonl
    state = tmp_path / "state.json"
    with patch("scripts.export_to_render.httpx.AsyncClient") as ac:
        result = await run_export(jsonl, photos, "https://render.test", "tok", state,
                                  source_zip="z", project_id=None, dry_run=True)
    ac.assert_not_called()
    assert result["prepared"] == 1


@pytest.mark.asyncio
async def test_export_posts_metadata_only_no_bytes(photos_and_jsonl, tmp_path):
    """Architecture correction: export pushes ONLY metadata to /v1/admin/photo-import.
    Raw photo bytes do not go to Render."""
    photos, jsonl = photos_and_jsonl
    state = tmp_path / "state.json"

    posted_urls = []

    async def fake_post(url, **kwargs):
        posted_urls.append(url)
        r = AsyncMock()
        r.status_code = 200
        r.raise_for_status = lambda: None
        r.json = lambda: {"inserted": 1, "skipped_duplicate": 0, "errors": []}
        return r

    fake_client = AsyncMock()
    fake_client.post = fake_post

    with patch("scripts.export_to_render.httpx.AsyncClient") as ac:
        ac.return_value.__aenter__.return_value = fake_client
        result = await run_export(jsonl, photos, "https://render.test", "tok", state,
                                  source_zip="z", project_id=None, dry_run=False)

    # No /photo-bytes/ calls — that endpoint is gone.
    assert not any("/photo-bytes/" in u for u in posted_urls)
    # One /photo-import call.
    assert sum(1 for u in posted_urls if "/photo-import" in u) == 1
    assert result["import_result"]["inserted"] == 1
