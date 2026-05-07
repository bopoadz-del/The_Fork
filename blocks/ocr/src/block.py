"""OCR Block - Standalone OCR for images"""
from blocks.base import LegoBlock
from typing import Dict, Any, List

class OCRBlock(LegoBlock):
    """OCR for images - text extraction"""
    name = "ocr"
    version = "1.0.0"
    requires = ["config"]
    layer = 3  # Domain layer
    tags = ["ocr", "text", "document", "domain"]
    default_config = {
        "engine": "tesseract",
        "lang": "eng"
    }
    
    ENGINES = {
        "tesseract": {"local": True, "accuracy": "high"},
        "easyocr": {"local": True, "accuracy": "very_high"},
        "paddle": {"local": True, "accuracy": "very_high", "multilingual": True},
        "google_vision": {"local": False, "accuracy": "highest"}
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.default_engine = config.get("engine", "easyocr")
        self.api_key = config.get("google_vision_key")
        
        # Lazy load models
        self._easyocr_reader = None
        self._paddle_ocr = None
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "extract_text":
            return await self._extract_text(input_data)
        elif action == "extract_structured":
            return await self._extract_structured(input_data)
        elif action == "detect_language":
            return await self._detect_language(input_data)
        return {"error": "Unknown action"}
    
    async def _extract_text(self, data: Dict) -> Dict:
        """Extract text from image"""
        image_bytes = data.get("image_bytes") or data.get("image")
        file_path = data.get("file_path")
        engine = data.get("engine", self.default_engine)
        lang = data.get("language", "en")
        
        if not image_bytes and file_path:
            with open(file_path, "rb") as f:
                image_bytes = f.read()
        
        if not image_bytes:
            return {"error": "No image bytes or file_path provided"}
        
        if engine == "tesseract":
            return await self._tesseract_ocr(image_bytes, lang)
        elif engine == "easyocr":
            return await self._easyocr_ocr(image_bytes, lang)
        elif engine == "paddle":
            return await self._paddle_ocr(image_bytes, lang)
        elif engine == "google_vision":
            return await self._google_vision_ocr(image_bytes, lang)
        
        return {"error": f"Unknown engine: {engine}"}
    
    async def _tesseract_ocr(self, image_bytes: bytes, lang: str) -> Dict:
        """Tesseract OCR"""
        try:
            import pytesseract
            from PIL import Image
            import io
            
            image = Image.open(io.BytesIO(image_bytes))
            text = pytesseract.image_to_string(image, lang=lang)
            
            # Get confidence
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            confidences = [int(c) for c in data["conf"] if int(c) > 0]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            return {
                "text": text,
                "confidence": avg_confidence / 100,
                "engine": "tesseract",
                "language": lang
            }
            
        except ImportError:
            return {"error": "pytesseract not installed. Run: pip install pytesseract pillow"}
        except Exception as e:
            return {"error": f"Tesseract OCR failed: {str(e)}"}
    
    async def _easyocr_ocr(self, image_bytes: bytes, lang: str) -> Dict:
        """EasyOCR - more accurate, supports multiple languages"""
        try:
            import easyocr
            import numpy as np
            from PIL import Image
            import io
            
            # Lazy init reader
            if self._easyocr_reader is None:
                lang_list = [lang] if lang != "auto" else ['en']
                self._easyocr_reader = easyocr.Reader(lang_list, gpu=False)
            
            # Convert bytes to numpy array
            image = Image.open(io.BytesIO(image_bytes))
            image_array = np.array(image)
            
            # Run OCR
            results = self._easyocr_reader.readtext(image_array)
            
            # Extract text and confidence
            texts = []
            total_confidence = 0
            
            for (bbox, text, conf) in results:
                texts.append(text)
                total_confidence += conf
            
            full_text = " ".join(texts)
            avg_confidence = total_confidence / len(results) if results else 0
            
            return {
                "text": full_text,
                "confidence": avg_confidence,
                "engine": "easyocr",
                "language": lang,
                "blocks": len(results)
            }
            
        except ImportError:
            return {"error": "easyocr not installed. Run: pip install easyocr"}
        except Exception as e:
            return {"error": f"EasyOCR failed: {str(e)}"}
    
    async def _paddle_ocr(self, image_bytes: bytes, lang: str) -> Dict:
        """PaddleOCR - best for multilingual"""
        try:
            from paddleocr import PaddleOCR
            from PIL import Image
            import numpy as np
            import io
            
            # Lazy init
            if self._paddle_ocr is None:
                self._paddle_ocr = PaddleOCR(use_angle_cls=True, lang=lang, use_gpu=False)
            
            # Save to temp file (Paddle requires file path)
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(image_bytes)
                temp_path = f.name
            
            result = self._paddle_ocr.ocr(temp_path, cls=True)
            
            import os
            os.unlink(temp_path)
            
            texts = []
            confidences = []
            
            if result and result[0]:
                for line in result[0]:
                    if line:
                        text = line[1][0]
                        conf = line[1][1]
                        texts.append(text)
                        confidences.append(conf)
            
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            return {
                "text": " ".join(texts),
                "confidence": avg_confidence,
                "engine": "paddle",
                "language": lang,
                "lines": len(texts)
            }
            
        except ImportError:
            return {"error": "paddleocr not installed. Run: pip install paddleocr"}
        except Exception as e:
            return {"error": f"PaddleOCR failed: {str(e)}"}
    
    async def _google_vision_ocr(self, image_bytes: bytes, lang: str) -> Dict:
        """Google Cloud Vision OCR"""
        if not self.api_key:
            return {"error": "Google Vision API key not configured"}
        
        try:
            import aiohttp
            
            import base64
            b64_image = base64.b64encode(image_bytes).decode()
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://vision.googleapis.com/v1/images:annotate?key={self.api_key}",
                    json={
                        "requests": [{
                            "image": {"content": b64_image},
                            "features": [{"type": "TEXT_DETECTION"}]
                        }]
                    }
                ) as resp:
                    result = await resp.json()
                    
                    if "error" in result:
                        return {"error": result["error"]["message"]}
                    
                    text = result["responses"][0].get("fullTextAnnotation", {}).get("text", "")
                    
                    return {
                        "text": text,
                        "confidence": 0.95,
                        "engine": "google_vision",
                        "language": lang
                    }
                    
        except Exception as e:
            return {"error": f"Google Vision failed: {str(e)}"}
    
    async def _extract_structured(self, data: Dict) -> Dict:
        """Extract structured data (tables, forms)"""
        image_bytes = data.get("image_bytes")
        
        # First get text
        text_result = await self._extract_text({"image_bytes": image_bytes})
        
        if "error" in text_result:
            return text_result
        
        # Try to parse as structured data
        text = text_result.get("text", "")
        
        # Look for patterns (dates, amounts, IDs)
        import re
        
        dates = re.findall(r'\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}', text)
        amounts = re.findall(r'\$?[\d,]+\.?\d*', text)
        emails = re.findall(r'[\w.-]+@[\w.-]+\.\w+', text)
        
        return {
            "text": text,
            "structured": {
                "dates": dates[:10],
                "amounts": amounts[:10],
                "emails": emails,
            },
            "confidence": text_result.get("confidence"),
            "engine": text_result.get("engine")
        }
    
    async def _detect_language(self, data: Dict) -> Dict:
        """Detect language of text in image"""
        # Run OCR with auto language
        result = await self._easyocr_ocr(data.get("image_bytes"), "en")
        
        if "error" in result:
            return result
        
        # Use langdetect on extracted text
        try:
            from langdetect import detect
            lang = detect(result["text"])
            return {
                "language": lang,
                "confidence": 0.9,
                "sample_text": result["text"][:100]
            }
        except ImportError:
            return {"language": "unknown", "confidence": 0}
    
    def health(self) -> Dict:
        h = super().health()
        h["engines"] = list(self.ENGINES.keys())
        h["default"] = self.default_engine
        
        # Check available engines
        available = []
        try:
            import pytesseract
            available.append("tesseract")
        except ImportError:
            pass
        try:
            import easyocr
            available.append("easyocr")
        except ImportError:
            pass
        
        h["available_engines"] = available
        return h
