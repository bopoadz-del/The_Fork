"""Image block — local-only guarantees (no cloud vision).

Locks in:
1. Source contains no Anthropic / OpenAI / Grok / Claude references.
2. ``operation: metadata`` returns PIL metadata with ``provider: pil``.
3. ``operation: analyze`` returns a local-only structured description without
   making any network call — even with no env vars set.
"""

from __future__ import annotations

import inspect
import os
import tempfile

import pytest
from PIL import Image

from app.blocks import image as image_module
from app.blocks.image import ImageBlock


def _make_tmp_image(width: int = 320, height: int = 240, colour=(120, 60, 200)) -> str:
    img = Image.new("RGB", (width, height), colour)
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(f.name)
    f.close()
    return f.name


def test_image_module_has_no_forbidden_provider_names():
    src = inspect.getsource(image_module)
    forbidden = ["anthropic", "openai", "grok", "claude"]
    for term in forbidden:
        assert term.lower() not in src.lower(), f"forbidden provider name '{term}' reappeared in image module"


@pytest.mark.asyncio
async def test_metadata_operation_returns_pil_provider(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    path = _make_tmp_image()
    try:
        block = ImageBlock()
        result = await block.process(path, {"operation": "metadata"})
    finally:
        os.unlink(path)

    assert result["status"] == "success"
    assert result["provider"] == "pil"
    assert result["width"] == 320
    assert result["height"] == 240
    assert result["format"] == "PNG"


@pytest.mark.asyncio
async def test_analyze_operation_works_with_no_env_vars(monkeypatch):
    """Even with no API keys at all, analyze must succeed locally."""

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    path = _make_tmp_image()
    try:
        block = ImageBlock()
        result = await block.process(path, {"operation": "analyze"})
    finally:
        os.unlink(path)

    assert result["status"] == "success"
    assert result["provider"] in {"pil", "pil+tesseract"}
    assert "metadata" in result
    assert "description" in result
    # Description must not reference any cloud vision provider.
    desc = result["description"].lower()
    for term in ("claude", "anthropic", "openai", "grok"):
        assert term not in desc


def test_image_block_metadata():
    assert ImageBlock.name == "image"
    assert ImageBlock.version.startswith("3.")
    assert "local" in ImageBlock.tags
