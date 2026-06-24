"""Tests for image block's safety_qaqc mode (Plan Task 2.2)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.blocks.image import ImageBlock


@pytest.fixture
def image_path(tmp_path):
    p = tmp_path / "img.jpg"
    Image.new("RGB", (100, 100), color=(120, 60, 30)).save(p)
    return p


@pytest.mark.asyncio
async def test_safety_qaqc_mode_includes_detector_output(image_path):
    """When SAFETY_DETECTOR_WEIGHTS is set, mode=safety_qaqc includes
    fine-tuned YOLO output AND adds 'safety_qaqc' to provider string."""
    fake_detector = MagicMock()
    fake_detector.detect.return_value = [
        {"class_id": 0, "class": "no_hardhat", "category": "safety",
         "confidence": 0.9, "bbox": [0, 0, 10, 10]}
    ]
    with patch("app.blocks.safety_detector.default_detector", return_value=fake_detector):
        block = ImageBlock()
        result = await block.execute({"file_path": str(image_path)}, {"mode": "safety_qaqc"})

    assert result["status"] == "success"
    body = result["result"] if "result" in result else result
    assert body["safety_qaqc"] == [{
        "class_id": 0, "class": "no_hardhat", "category": "safety",
        "confidence": 0.9, "bbox": [0, 0, 10, 10],
    }]
    assert "safety_qaqc" in body["provider"]


@pytest.mark.asyncio
async def test_safety_qaqc_mode_no_detector_returns_empty(image_path):
    """When SAFETY_DETECTOR_WEIGHTS isn't configured, mode=safety_qaqc
    still returns success with empty safety_qaqc list."""
    with patch("app.blocks.safety_detector.default_detector", return_value=None):
        block = ImageBlock()
        result = await block.execute({"file_path": str(image_path)}, {"mode": "safety_qaqc"})

    body = result["result"] if "result" in result else result
    assert body["safety_qaqc"] == []
    assert "safety_qaqc" not in body["provider"]


@pytest.mark.asyncio
async def test_default_mode_does_not_run_safety_qaqc(image_path):
    """Calls without mode=safety_qaqc must NOT add a safety_qaqc field
    (preserves the existing API for callers that don't opt in)."""
    block = ImageBlock()
    result = await block.execute({"file_path": str(image_path)}, {})
    body = result["result"] if "result" in result else result
    assert "safety_qaqc" not in body
