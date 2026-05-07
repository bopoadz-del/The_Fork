"""Image Block - Claude Vision analysis + PIL metadata fallback"""

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict

from app.core.universal_base import UniversalBlock

_MODEL = "claude-haiku-4-5-20251001"
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def _load_image_b64(file_path: str) -> tuple[str, str]:
    """Return (base64_data, media_type) for a local file."""
    mime, _ = mimetypes.guess_type(file_path)
    media_type = mime or "image/jpeg"
    with open(file_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


async def _download_image_b64(url: str) -> tuple[str, str]:
    import httpx

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    data = base64.standard_b64encode(resp.content).decode("utf-8")
    return data, content_type


async def _analyze_with_claude(img_data: str, media_type: str, prompt: str) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    msg = await client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return msg.content[0].text


def _pil_metadata(file_path: str) -> Dict:
    from PIL import Image

    img = Image.open(file_path)
    width, height = img.size
    mode = img.mode
    fmt = img.format or Path(file_path).suffix.upper().lstrip(".")
    file_size = os.path.getsize(file_path)

    info = {
        "width": width,
        "height": height,
        "mode": mode,
        "format": fmt,
        "file_size_bytes": file_size,
        "megapixels": round(width * height / 1_000_000, 2),
        "aspect_ratio": f"{width}:{height}",
    }

    if mode in ("RGB", "RGBA"):
        r, g, b = img.convert("RGB").split()
        info["dominant_channel"] = max(
            ("red", _avg(r)), ("green", _avg(g)), ("blue", _avg(b)),
            key=lambda x: x[1],
        )[0]

    return info


def _avg(channel) -> float:
    import numpy as np
    return float(np.array(channel).mean())


class ImageBlock(UniversalBlock):
    """Image analysis via Claude Vision; PIL metadata when no API key"""

    name = "image"
    version = "2.0"
    description = "Analyze images with Claude Vision AI or extract basic metadata"
    layer = 3
    tags = ["domain", "vision", "image"]
    requires = []

    ui_schema = {
        "input": {
            "type": "image",
            "accept": [".jpg", ".jpeg", ".png", ".webp", ".gif"],
            "placeholder": "Upload image to analyze...",
            "multiline": True,
        },
        "output": {
            "type": "text",
            "fields": [
                {"name": "description", "type": "markdown", "label": "Analysis"},
                {"name": "objects_detected", "type": "array", "label": "Objects"},
            ],
        },
        "quick_actions": [
            {"icon": "🖼️", "label": "Analyze Image", "prompt": "Describe what's in this image"},
            {"icon": "📐", "label": "Construction", "prompt": "Analyze this construction drawing"},
            {"icon": "🔍", "label": "Extract Text", "prompt": "Extract all text from this image"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        operation = params.get("operation", "analyze")
        prompt = params.get("prompt", "Describe this image in detail. List any key objects, text, or notable features.")

        # Resolve source
        file_path = None
        url = None
        if isinstance(input_data, str):
            if input_data.startswith("http"):
                url = input_data
            elif os.path.exists(input_data):
                file_path = input_data
            else:
                return {"status": "error", "error": "Provide a valid file path or URL"}
        elif isinstance(input_data, dict):
            file_path = input_data.get("file_path") or input_data.get("path")
            url = input_data.get("url")
            prompt = input_data.get("prompt", prompt)
            # InputAdapter wraps bare strings as {"text": "..."} — handle URL and path from it
            if not file_path and not url:
                raw = input_data.get("text") or input_data.get("input") or ""
                if raw.startswith("http"):
                    url = raw
                elif raw and os.path.exists(raw):
                    file_path = raw
        else:
            return {"status": "error", "error": "Input must be a file path, URL, or {file_path, url, prompt}"}

        if operation == "metadata":
            try:
                if url and not file_path:
                    import tempfile
                    img_data, media_type = await _download_image_b64(url)
                    raw = base64.b64decode(img_data)
                    suffix = "." + media_type.split("/")[-1].split(";")[0]
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                        f.write(raw)
                        tmp = f.name
                    try:
                        meta = _pil_metadata(tmp)
                    finally:
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
                elif file_path:
                    meta = _pil_metadata(file_path)
                else:
                    return {"status": "error", "error": "Provide file_path or url for metadata"}
                return {"status": "success", "operation": "metadata", **meta}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        has_api_key = bool(os.getenv("ANTHROPIC_API_KEY"))

        if not has_api_key:
            # Fallback: PIL metadata only
            if file_path:
                try:
                    meta = _pil_metadata(file_path)
                    return {
                        "status": "success",
                        "operation": "metadata_only",
                        "note": "Set ANTHROPIC_API_KEY for AI vision analysis",
                        **meta,
                    }
                except Exception as e:
                    return {"status": "error", "error": str(e)}
            return {
                "status": "error",
                "error": "ANTHROPIC_API_KEY not set. Provide a file path for basic metadata, or set the key for AI vision.",
            }

        try:
            if url:
                img_data, media_type = await _download_image_b64(url)
            else:
                img_data, media_type = _load_image_b64(file_path)

            if len(img_data) * 3 // 4 > _MAX_IMAGE_BYTES:
                return {"status": "error", "error": "Image too large (max 5 MB)"}

            if operation == "extract_text":
                prompt = "Extract all visible text from this image exactly as it appears. Format clearly."
            elif operation == "construction":
                prompt = (
                    "Analyze this construction drawing or site photo. Identify: "
                    "document type, scale/dimensions if visible, materials mentioned, "
                    "key measurements, any annotations, and overall purpose."
                )

            description = await _analyze_with_claude(img_data, media_type, prompt)

            result = {
                "status": "success",
                "operation": operation,
                "description": description,
                "model": _MODEL,
                "source": url or os.path.basename(file_path),
            }

            if file_path:
                try:
                    meta = _pil_metadata(file_path)
                    result["metadata"] = meta
                except Exception:
                    pass

            return result

        except Exception as e:
            return {"status": "error", "error": str(e), "operation": operation}
