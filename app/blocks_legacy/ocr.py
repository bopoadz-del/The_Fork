"""OCR Block - Extract text from images using OCR."""

import os
import io
import base64
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig
from PIL import Image


class OCRBlock(BaseBlock):
    """Extract text from images using OCR (Optical Character Recognition)."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="ocr",
            version="1.0",
            description="Extract text from images using OCR",
            supported_inputs=["image", "file_path", "base64"],
            supported_outputs=["text", "bounding_boxes"]
        ,
            layer=3,
            tags=["domain", "documents", "ocr", "vision"]))
        self._pytesseract_available = self._check_pytesseract()
        self._easyocr_available = self._check_easyocr()
    
    def _check_pytesseract(self) -> bool:
        try:
            import pytesseract
            return True
        except ImportError:
            return False
    
    def _check_easyocr(self) -> bool:
        try:
            import easyocr
            return True
        except ImportError:
            return False
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process image and extract text."""
        params = params or {}
        language = params.get("language", "eng")
        return_boxes = params.get("return_boxes", False)
        engine = params.get("engine", "auto")  # auto, pytesseract, easyocr
        
        image = self._load_image(input_data)
        
        result = {
            "image_size": image.size,
            "image_mode": image.mode,
        }
        
        # Convert to RGB if necessary
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        
        # Choose OCR engine
        if engine == "auto":
            if self._pytesseract_available:
                engine = "pytesseract"
            elif self._easyocr_available:
                engine = "easyocr"
            else:
                engine = "none"
        
        if engine == "pytesseract" and self._pytesseract_available:
            import pytesseract
            
            text = pytesseract.image_to_string(image, lang=language)
            result["text"] = text.strip()
            result["word_count"] = len(text.split())
            result["engine"] = "pytesseract"
            
            if return_boxes:
                data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
                boxes = []
                for i in range(len(data["text"])):
                    if int(data["conf"][i]) > 0:
                        boxes.append({
                            "text": data["text"][i],
                            "confidence": data["conf"][i] / 100,
                            "x": data["left"][i],
                            "y": data["top"][i],
                            "width": data["width"][i],
                            "height": data["height"][i]
                        })
                result["bounding_boxes"] = boxes
            
            result["confidence"] = 0.90
            
        elif engine == "easyocr" and self._easyocr_available:
            import easyocr
            
            # Initialize reader (cached)
            if not hasattr(self, "_reader"):
                self._reader = easyocr.Reader([language[:2] if len(language) >= 2 else "en"])
            
            img_array = self._pil_to_array(image)
            ocr_result = self._reader.readtext(img_array)
            
            texts = []
            boxes = []
            confidences = []
            
            for detection in ocr_result:
                bbox, text, conf = detection
                texts.append(text)
                confidences.append(conf)
                if return_boxes:
                    boxes.append({
                        "text": text,
                        "confidence": conf,
                        "bbox": bbox
                    })
            
            result["text"] = " ".join(texts)
            result["word_count"] = len(result["text"].split())
            result["engine"] = "easyocr"
            result["avg_confidence"] = sum(confidences) / len(confidences) if confidences else 0
            
            if return_boxes:
                result["bounding_boxes"] = boxes
            
            result["confidence"] = result["avg_confidence"]
            
        else:
            # Fallback: return image info
            result["text"] = "[OCR engine not available - install pytesseract or easyocr]"
            result["engine"] = "none"
            result["confidence"] = 0.0
        
        return result
    
    def _load_image(self, input_data: Any) -> Image.Image:
        """Load image from various input formats."""
        if isinstance(input_data, dict):
            if "image" in input_data:
                return input_data["image"]
            if "file_path" in input_data:
                return Image.open(input_data["file_path"])
            if "base64" in input_data:
                img_data = base64.b64decode(input_data["base64"])
                return Image.open(io.BytesIO(img_data))
            if "source_id" in input_data:
                return Image.open(f"/app/data/{input_data['source_id']}")
        if isinstance(input_data, str) and os.path.exists(input_data):
            return Image.open(input_data)
        if isinstance(input_data, Image.Image):
            return input_data
        raise ValueError("Invalid input: expected image file path, base64, or PIL Image")
    
    def _pil_to_array(self, image: Image.Image):
        """Convert PIL image to numpy array."""
        import numpy as np
        return np.array(image)
