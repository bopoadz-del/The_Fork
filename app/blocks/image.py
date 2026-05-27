"""Image Block — local-only image analysis (PIL metadata + Tesseract OCR).

No external cloud vision API. All processing runs in-process on the host:

- ``metadata`` / default — PIL-based dimensions, mode, format, file size,
  megapixels, aspect ratio, dominant colour channel.
- ``extract_text`` — Tesseract OCR (already a project dependency) for
  text-bearing images. Returns the extracted text plus a confidence proxy.
- ``construction`` / ``analyze`` — combines metadata with an OCR pass and
  returns a structured local-only summary. A future local CV tier (e.g.
  YOLO for object/PPE detection) will plug into this same operation.

The block exposes ``provider`` so callers can see the local backend that
served the response (``pil``, ``tesseract``, ``pil+tesseract``).
"""

import os
from pathlib import Path
from typing import Any, Dict, Tuple

from app.core.universal_base import UniversalBlock

_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB — generous for local processing


async def _download_to_temp(url: str) -> str:
    """Download a URL to a temporary file and return its path."""
    import tempfile
    import httpx

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
    suffix = "." + content_type.split("/")[-1] if "/" in content_type else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(resp.content)
        return f.name


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


def _tesseract_ocr(file_path: str) -> Tuple[str, float]:
    """Run Tesseract OCR locally. Returns (text, mean_confidence_0_to_1)."""
    import pytesseract
    from PIL import Image

    img = Image.open(file_path)
    text = pytesseract.image_to_string(img).strip()

    confidence = 0.0
    try:
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        confs = [int(c) for c in data.get("conf", []) if str(c).isdigit() and int(c) >= 0]
        if confs:
            confidence = sum(confs) / len(confs) / 100.0
    except Exception:
        pass

    return text, confidence


class ImageBlock(UniversalBlock):
    """Local image analysis — PIL metadata + Tesseract OCR. No cloud calls."""

    name = "image"
    version = "3.0"
    description = "Local image analysis — metadata + OCR (no cloud vision)"
    layer = 3
    tags = ["domain", "vision", "image", "local"]
    requires = []

    ui_schema = {
        "input": {
            "type": "image",
            "accept": [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"],
            "placeholder": "Upload image to analyze...",
            "multiline": True,
        },
        "output": {
            "type": "text",
            "fields": [
                {"name": "description", "type": "markdown", "label": "Analysis"},
                {"name": "extracted_text", "type": "text", "label": "Text"},
                {"name": "metadata", "type": "object", "label": "Metadata"},
            ],
        },
        "quick_actions": [
            {"icon": "🖼️", "label": "Metadata", "prompt": "Show image metadata"},
            {"icon": "📐", "label": "Construction", "prompt": "Analyze construction image"},
            {"icon": "🔍", "label": "Extract Text", "prompt": "Extract all text"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        operation = params.get("operation", "analyze")

        file_path, tmp_path = await self._resolve_source(input_data)
        if isinstance(file_path, dict):
            return file_path  # error dict

        try:
            file_size = os.path.getsize(file_path)
            if file_size > _MAX_IMAGE_BYTES:
                return {"status": "error", "error": f"Image too large (max {_MAX_IMAGE_BYTES // (1024*1024)} MB)"}

            if operation == "metadata":
                meta = _pil_metadata(file_path)
                return {"status": "success", "operation": "metadata", "provider": "pil", **meta}

            if operation == "extract_text":
                text, confidence = _tesseract_ocr(file_path)
                if not text:
                    return {
                        "status": "success",
                        "operation": "extract_text",
                        "provider": "tesseract",
                        "extracted_text": "",
                        "confidence": confidence,
                        "note": "No text detected by Tesseract.",
                    }
                return {
                    "status": "success",
                    "operation": "extract_text",
                    "provider": "tesseract",
                    "extracted_text": text,
                    "confidence": confidence,
                    "word_count": len(text.split()),
                }

            # analyze / construction → combined local summary
            meta = _pil_metadata(file_path)
            ocr_text = ""
            ocr_conf = 0.0
            ocr_error = None
            try:
                ocr_text, ocr_conf = _tesseract_ocr(file_path)
            except Exception as e:
                ocr_error = str(e)

            description = self._compose_local_summary(
                operation=operation, meta=meta, ocr_text=ocr_text, ocr_conf=ocr_conf, ocr_error=ocr_error,
            )

            return {
                "status": "success",
                "operation": operation,
                "provider": "pil+tesseract" if ocr_text else "pil",
                "description": description,
                "extracted_text": ocr_text,
                "ocr_confidence": ocr_conf,
                "metadata": meta,
            }

        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    async def _resolve_source(self, input_data: Any):
        """Return (file_path, tmp_path_or_None) or an error dict on failure."""
        url = None
        file_path = None

        if isinstance(input_data, str):
            if input_data.startswith("http"):
                url = input_data
            elif os.path.exists(input_data):
                file_path = input_data
            else:
                return {"status": "error", "error": "Provide a valid file path or URL"}, None
        elif isinstance(input_data, dict):
            file_path = input_data.get("file_path") or input_data.get("path")
            url = input_data.get("url")
            if not file_path and not url:
                raw = input_data.get("text") or input_data.get("input") or ""
                if isinstance(raw, str) and raw.startswith("http"):
                    url = raw
                elif raw and os.path.exists(raw):
                    file_path = raw
        else:
            return {"status": "error", "error": "Input must be a file path, URL, or {file_path, url}"}, None

        tmp_path = None
        if url and not file_path:
            try:
                file_path = await _download_to_temp(url)
                tmp_path = file_path
            except Exception as e:
                return {"status": "error", "error": f"Failed to download URL: {e}"}, None

        if not file_path:
            return {"status": "error", "error": "Provide file_path or url"}, None

        return file_path, tmp_path

    @staticmethod
    def _compose_local_summary(
        operation: str, meta: Dict, ocr_text: str, ocr_conf: float, ocr_error: str
    ) -> str:
        lines = []
        if operation == "construction":
            lines.append("**Construction image — local analysis**")
        else:
            lines.append("**Image — local analysis**")
        lines.append("")
        lines.append(
            f"- **Dimensions:** {meta.get('width')} × {meta.get('height')} px "
            f"({meta.get('megapixels')} MP, aspect {meta.get('aspect_ratio')})"
        )
        lines.append(f"- **Format:** {meta.get('format')} ({meta.get('mode')})")
        lines.append(f"- **File size:** {meta.get('file_size_bytes')} bytes")
        if "dominant_channel" in meta:
            lines.append(f"- **Dominant channel:** {meta['dominant_channel']}")

        lines.append("")
        if ocr_text:
            preview = ocr_text if len(ocr_text) <= 800 else ocr_text[:800] + "..."
            lines.append(f"**Extracted text** (Tesseract, confidence {ocr_conf:.2f}):")
            lines.append("")
            lines.append("```")
            lines.append(preview)
            lines.append("```")
        elif ocr_error:
            lines.append(f"_OCR unavailable: {ocr_error}_")
        else:
            lines.append("_No text detected._")

        return "\n".join(lines)
