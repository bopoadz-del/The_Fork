"""OCR Block - Extract text from images with Typed Schema"""

import os
import io
import tempfile
from typing import Any, Dict
from app.core.typed_block import TypedBlock, Schema, ContentType


class OCRBlock(TypedBlock):
    """Optical Character Recognition from images with typed I/O"""
    
    name = "ocr"
    version = "2.0.0"
    description = "Extract text from images using OCR with preprocessing"
    layer = 3
    tags = ["domain", "vision", "ocr", "documents", "typed"]
    requires = []
    
    default_config = {
        "languages": ["en"],
        "preprocess": True,
        "deskew": False,
        "contrast_factor": 1.5
    }
    
    # Type schemas for chain validation
    input_schema = Schema(
        content_type=ContentType.IMAGE,
        required_fields=["file_path"],
        optional_fields=["path", "url"],
        format_hints={"accept": [".jpg", ".jpeg", ".png", ".webp"]}
    )
    
    output_schema = Schema(
        content_type=ContentType.TEXT,
        required_fields=["text"],
        optional_fields=["confidence", "word_count", "engine", "preprocessed", "status"],
        format_hints={}
    )
    
    ui_schema = {
        "input": {
            "type": "image",
            "accept": [".jpg", ".jpeg", ".png", ".webp"],
            "placeholder": "Upload image to extract text...",
            "multiline": False
        },
        "output": {
            "type": "text",
            "fields": [
                {"name": "text", "type": "text", "label": "Extracted Text"},
                {"name": "confidence", "type": "percentage", "label": "Confidence"}
            ]
        },
        "quick_actions": [
            {"icon": "🔍", "label": "Extract Text", "prompt": "Extract all text from this image"},
            {"icon": "🔢", "label": "Extract Numbers", "prompt": "Extract all numbers and measurements from this image"},
            {"icon": "📋", "label": "Full OCR", "prompt": "Perform full OCR and return structured content"}
        ]
    }
    
    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """Extract text from image (or PDF) with preprocessing"""
        params = params or {}

        # Download from URL if needed (handles bare URL strings and InputAdapter {"text": "url"} wrapping)
        url = None
        if isinstance(input_data, str) and input_data.startswith("http"):
            url = input_data
        elif isinstance(input_data, dict):
            url = input_data.get("url")
            if not url:
                raw = input_data.get("text") or input_data.get("input") or ""
                if raw.startswith("http"):
                    url = raw

        if url:
            import httpx
            try:
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    response = await client.get(url, timeout=30)
                    response.raise_for_status()
                    ext = ".jpg"
                    for candidate in [".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"]:
                        if candidate in url.lower():
                            ext = candidate
                            break
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as f:
                        f.write(response.content)
                        input_data = f.name
            except Exception as e:
                return {"status": "error", "text": "", "confidence": 0, "error": f"Download failed: {str(e)}"}

        image_path = self._get_image_path(input_data)
        if not image_path:
            return {"status": "error", "text": "", "confidence": 0, "error": "No image provided"}

        if not os.path.exists(image_path):
            return {"status": "error", "text": "", "confidence": 0, "error": f"File not found: {image_path}"}
        
        preprocess = params.get("preprocess", self.config.get("preprocess", True))
        languages = params.get("languages", self.config.get("languages", ["en"]))
        
        # Detect if input is a PDF and convert pages to images
        page_images = self._prepare_images(image_path, preprocess)
        if not page_images:
            return {"status": "error", "text": "", "confidence": 0, "error": "Could not process input file"}
        
        # Try pytesseract first, fallback to Claude Vision, then PyMuPDF
        all_texts = []
        all_confs = []
        engine_used = None
        tesseract_available = True

        try:
            import pytesseract
            pytesseract.get_tesseract_version()  # raises if not installed
        except Exception:
            tesseract_available = False

        if tesseract_available:
            try:
                import pytesseract
                from PIL import Image
                engine_used = "pytesseract"

                for page_img_path in page_images:
                    img = Image.open(page_img_path)
                    text = pytesseract.image_to_string(img)
                    if text.strip():
                        all_texts.append(text.strip())
                        all_confs.append(0.85)
            except Exception as e:
                tesseract_available = False

        if not tesseract_available:
            # Claude Vision OCR fallback (works for images, no system deps)
            anthropic_key = os.getenv("ANTHROPIC_API_KEY")
            vision_error = None
            if anthropic_key and not image_path.lower().endswith(".pdf"):
                try:
                    import base64, anthropic, mimetypes
                    with open(image_path, "rb") as f:
                        img_bytes = f.read()
                    b64 = base64.standard_b64encode(img_bytes).decode()
                    mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
                    client = anthropic.Anthropic(api_key=anthropic_key)
                    response = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=4096,
                        messages=[{"role": "user", "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                            {"type": "text", "text": "Extract ALL text from this image. Return only the extracted text, nothing else."}
                        ]}],
                    )
                    text = response.content[0].text.strip()
                    if text:
                        return {
                            "status": "success",
                            "text": text,
                            "confidence": 0.92,
                            "word_count": len(text.split()),
                            "engine": "claude_vision",
                            "preprocessed": False,
                            "pages": len(page_images),
                            "note": "Tesseract not installed; used Claude Vision OCR"
                        }
                except Exception as e:
                    vision_error = str(e)
            elif not anthropic_key:
                vision_error = "ANTHROPIC_API_KEY not set"
            else:
                vision_error = "PDF files not supported for Vision OCR"

            # Final fallback: PyMuPDF text extraction (text-layer PDFs only)
            pdf_text = self._extract_pdf_text(image_path)
            if pdf_text:
                return {
                    "status": "success",
                    "text": pdf_text,
                    "confidence": 0.95,
                    "word_count": len(pdf_text.split()),
                    "engine": "pymupdf_fallback",
                    "preprocessed": False,
                    "pages": 1,
                    "note": "Tesseract not installed; used PyMuPDF text extraction"
                }
            return {"status": "error", "text": "", "confidence": 0, "error": f"Tesseract not installed; Vision OCR failed: {vision_error}"}

        if not all_texts:
            return {"status": "success", "text": "", "confidence": 0, "message": "No text detected"}
        
        full_text = "\n".join(all_texts)
        avg_conf = sum(all_confs) / len(all_confs) if all_confs else 0
        
        return {
            "status": "success",
            "text": full_text,
            "confidence": round(avg_conf, 2),
            "word_count": len(full_text.split()),
            "engine": engine_used or "unknown",
            "preprocessed": preprocess,
            "pages": len(page_images)
        }
    
    def _get_image_path(self, input_data: Any) -> str:
        """Extract image path from input"""
        if isinstance(input_data, str):
            return input_data
        elif isinstance(input_data, dict):
            return input_data.get("file_path") or input_data.get("path") or input_data.get("url")
        return None
    
    def _prepare_images(self, file_path: str, preprocess: bool = True) -> list:
        """Convert input to a list of image file paths (handles PDFs and images)."""
        from PIL import Image
        
        is_pdf = file_path.lower().endswith(".pdf")
        if not is_pdf:
            # Try to open as image; if it fails, try as PDF via PyMuPDF
            try:
                Image.open(file_path)
                # It's a valid image
                if preprocess:
                    return [self._preprocess_image(file_path)]
                return [file_path]
            except Exception:
                is_pdf = True
        
        if is_pdf:
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(file_path)
                images = []
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=200)
                    img_path = tempfile.mktemp(suffix=f"_page{page_num + 1}.png")
                    pix.save(img_path)
                    if preprocess:
                        img_path = self._preprocess_image(img_path)
                    images.append(img_path)
                doc.close()
                return images
            except ImportError:
                return []
            except Exception:
                return []
        
        return []
    
    def _extract_pdf_text(self, file_path: str) -> str:
        """Extract text from a PDF using PyMuPDF (fallback when OCR is unavailable)."""
        try:
            import fitz
            doc = fitz.open(file_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text.strip()
        except Exception:
            return ""
    
    def _preprocess_image(self, image_path: str) -> str:
        """Enhance image for better OCR quality"""
        from PIL import Image, ImageEnhance, ImageFilter
        
        img = Image.open(image_path)
        
        # Convert to RGB if necessary
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        
        # Resize if too small (improve effective DPI)
        min_dim = min(img.size)
        if min_dim < 1000:
            scale = 1200 / min_dim
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # Convert to grayscale
        gray = img.convert('L')
        
        # Enhance contrast
        contrast_factor = self.config.get("contrast_factor", 1.5)
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(contrast_factor)
        
        # Sharpen
        gray = gray.filter(ImageFilter.SHARPEN)
        
        # Apply mild denoise
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
        
        # Adaptive thresholding: if image is low-contrast, apply point operation
        # to increase separation between text and background
        stat = gray.getextrema()
        if stat and (stat[1] - stat[0]) < 100:
            # Low dynamic range — apply threshold
            gray = gray.point(lambda x: 0 if x < 128 else 255, '1').convert('L')
        
        # Save to temp file
        fd, temp_path = tempfile.mkstemp(suffix="_ocr.png")
        os.close(fd)
        gray.save(temp_path, "PNG")
        return temp_path
