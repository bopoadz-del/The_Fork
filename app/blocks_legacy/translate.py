"""Translate Block - Text translation."""

import os
from typing import Any, Dict, List, Optional
from app.core.block import BaseBlock, BlockConfig
import aiohttp


class TranslateBlock(BaseBlock):
    """Text translation supporting multiple providers."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="translate",
            version="1.0",
            description="Text translation with multiple providers",
            supported_inputs=["text"],
            supported_outputs=["translated_text"]
        ,
            layer=3,
            tags=["domain", "nlp", "translation"]))
        self._googletrans_available = self._check_googletrans()
        self._deep_available = self._check_deepl()
    
    def _check_googletrans(self) -> bool:
        try:
            from googletrans import Translator
            return True
        except ImportError:
            return False
    
    def _check_deepl(self) -> bool:
        try:
            import deepl
            return True
        except ImportError:
            return False
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Translate text."""
        params = params or {}
        target_lang = params.get("target", "en")
        source_lang = params.get("source", "auto")
        provider = params.get("provider", "auto")
        
        text = self._get_text(input_data)
        
        result = {
            "source_text": text[:200] + "..." if len(text) > 200 else text,
            "target_language": target_lang,
            "source_language": source_lang,
        }
        
        if provider == "auto":
            if self._deep_available and os.getenv("DEEPL_API_KEY"):
                provider = "deepl"
            elif self._googletrans_available:
                provider = "google"
            else:
                provider = "mock"
        
        if provider == "deepl" and self._deep_available:
            translation = await self._translate_deepl(text, target_lang, source_lang)
            result.update(translation)
        elif provider == "google" and self._googletrans_available:
            translation = await self._translate_google(text, target_lang, source_lang)
            result.update(translation)
        elif provider == "libre":
            translation = await self._translate_libre(text, target_lang, source_lang)
            result.update(translation)
        elif provider == "mock":
            result["translated_text"] = f"[Translated to {target_lang}] {text[:100]}..."
            result["detected_source"] = source_lang if source_lang != "auto" else "unknown"
            result["provider"] = "mock"
            result["confidence"] = 1.0
        else:
            result["translated_text"] = text
            result["error"] = f"Provider {provider} not available"
            result["confidence"] = 0.0
        
        return result
    
    def _get_text(self, input_data: Any) -> str:
        """Extract text from input."""
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, dict):
            if "text" in input_data:
                return input_data["text"]
            if "result" in input_data and isinstance(input_data["result"], dict):
                return input_data["result"].get("text", "")
        raise ValueError("Invalid text input")
    
    async def _translate_deepl(self, text: str, target: str, source: str) -> Dict:
        """Translate using DeepL."""
        import deepl
        
        api_key = os.getenv("DEEPL_API_KEY")
        if not api_key:
            return {
                "translated_text": text,
                "error": "DEEPL_API_KEY not set",
                "confidence": 0.0
            }
        
        try:
            translator = deepl.Translator(api_key)
            
            result = translator.translate_text(
                text,
                source_lang=source if source != "auto" else None,
                target_lang=target.upper()
            )
            
            return {
                "translated_text": result.text,
                "detected_source": result.detected_source_lang.lower() if hasattr(result, 'detected_source_lang') else source,
                "provider": "deepl",
                "confidence": 0.95
            }
        except Exception as e:
            return {
                "translated_text": text,
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _translate_google(self, text: str, target: str, source: str) -> Dict:
        """Translate using Google Translate."""
        from googletrans import Translator
        
        try:
            translator = Translator()
            
            result = translator.translate(
                text,
                dest=target,
                src=source if source != "auto" else "auto"
            )
            
            return {
                "translated_text": result.text,
                "detected_source": result.src,
                "pronunciation": result.pronunciation,
                "provider": "google",
                "confidence": 0.90
            }
        except Exception as e:
            return {
                "translated_text": text,
                "error": str(e),
                "confidence": 0.0
            }
    
    async def _translate_libre(self, text: str, target: str, source: str) -> Dict:
        """Translate using LibreTranslate (free, self-hostable)."""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "q": text,
                    "source": source if source != "auto" else "auto",
                    "target": target
                }
                
                async with session.post(
                    "https://libretranslate.de/translate",
                    json=payload
                ) as response:
                    data = await response.json()
                    
                    return {
                        "translated_text": data.get("translatedText", text),
                        "detected_source": data.get("detectedLanguage", {}).get("language", source),
                        "provider": "libretranslate",
                        "confidence": 0.80
                    }
        except Exception as e:
            return {
                "translated_text": text,
                "error": str(e),
                "confidence": 0.0
            }
