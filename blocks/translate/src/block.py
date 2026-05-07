"""Translate Block - REAL Argos integration"""
from blocks.base import LegoBlock
from typing import Dict, Any

class TranslateBlock(LegoBlock):
    """Translation - WORKING Argos or DeepL"""
    name = "translate"
    version = "1.0.0"
    requires = ["config"]
    layer = 4  # Utility layer
    tags = ["translation", "language", "nlp", "utility"]
    default_config = {
        "engine": "argos",
        "source_lang": "auto",
        "target_lang": "en"
    }
    
    LANGUAGES = {
        "en": "English", "ar": "Arabic", "zh": "Chinese", "es": "Spanish",
        "fr": "French", "de": "German", "hi": "Hindi", "ja": "Japanese",
        "ko": "Korean", "pt": "Portuguese", "ru": "Russian", "tr": "Turkish",
        "it": "Italian", "nl": "Dutch", "pl": "Polish", "vi": "Vietnamese"
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.api_key = config.get("deepl_key")
        self.use_local = config.get("local", True)  # Default to local
        self._argos_loaded = False
        self._argos_languages = None
        
    async def initialize(self):
        """Initialize translation engines"""
        if self.use_local:
            try:
                import argostranslate.package
                import argostranslate.translate
                
                # Check installed packages
                self._argos_languages = argostranslate.translate.get_installed_languages()
                
                if not self._argos_languages:
                    print("   ⚠️  No Argos language packages installed")
                    print("   Run: argostranslate.package.install_from_path('en_es.argos')")
                else:
                    print(f"   ✅ Argos ready: {len(self._argos_languages)} languages")
                    self._argos_loaded = True
                    
            except ImportError:
                print("   ⚠️  argostranslate not installed")
                print("   Run: pip install argostranslate")
        
        return True
    
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "translate":
            return await self._translate(input_data)
        elif action == "detect_language":
            return await self._detect_language(input_data)
        elif action == "batch_translate":
            return await self._batch_translate(input_data)
        elif action == "install_language":
            return await self._install_language(input_data)
        return {"error": "Unknown action"}
    
    async def _translate(self, data: Dict) -> Dict:
        """Translate text - tries Argos first, falls back to DeepL/Google"""
        text = data.get("text")
        target = data.get("target", "en")
        source = data.get("source", "auto")
        
        # Try Argos (local, free)
        if self.use_local and self._argos_loaded:
            result = await self._argos_translate(text, source, target)
            if "translated_text" in result:
                return result
        
        # Try DeepL (cloud, accurate)
        if self.api_key:
            return await self._deepl_translate(text, source, target)
        
        # Fallback to Google (free, no key needed)
        return await self._google_translate(text, source, target)
    
    async def _argos_translate(self, text: str, source: str, target: str) -> Dict:
        """Translate using Argos (local, offline)"""
        try:
            import argostranslate.translate
            
            # Get language objects
            installed_languages = argostranslate.translate.get_installed_languages()
            
            from_lang = next((l for l in installed_languages if l.code == source), None)
            to_lang = next((l for l in installed_languages if l.code == target), None)
            
            if not from_lang or not to_lang:
                return {"error": f"Language pair {source}->{target} not installed"}
            
            # Get translation
            translation = from_lang.get_translation(to_lang)
            if not translation:
                return {"error": f"No translation available for {source}->{target}"}
            
            translated = translation.translate(text)
            
            return {
                "translated_text": translated,
                "source_language": source,
                "target_language": target,
                "provider": "argos_local",
                "local": True
            }
            
        except Exception as e:
            return {"error": f"Argos failed: {str(e)}"}
    
    async def _deepl_translate(self, text: str, source: str, target: str) -> Dict:
        """Translate using DeepL API"""
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                data = {
                    "text": text,
                    "target_lang": target.upper()
                }
                if source != "auto":
                    data["source_lang"] = source.upper()
                
                async with session.post(
                    "https://api-free.deepl.com/v2/translate",
                    headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                    data=data
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return {"error": f"DeepL API error: {error}"}
                    
                    result = await resp.json()
                    translation = result["translations"][0]
                    
                    return {
                        "translated_text": translation["text"],
                        "detected_source": translation.get("detected_source_language"),
                        "source_language": source,
                        "target_language": target,
                        "provider": "deepl"
                    }
                    
        except Exception as e:
            return {"error": f"DeepL failed: {str(e)}"}
    
    async def _google_translate(self, text: str, source: str, target: str) -> Dict:
        """Free Google Translate (unofficial, no API key)"""
        try:
            import aiohttp
            
            params = {
                "client": "gtx",
                "sl": source if source != "auto" else "auto",
                "tl": target,
                "dt": "t",
                "q": text
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params=params
                ) as resp:
                    result = await resp.json()
                    
                    # Parse result
                    translated_parts = result[0]
                    translated = "".join([part[0] for part in translated_parts if part[0]])
                    detected = result[2]
                    
                    return {
                        "translated_text": translated,
                        "detected_source": detected,
                        "source_language": source,
                        "target_language": target,
                        "provider": "google_free"
                    }
                    
        except Exception as e:
            return {"error": f"Google Translate failed: {str(e)}"}
    
    async def _detect_language(self, data: Dict) -> Dict:
        """Detect language using langdetect"""
        text = data.get("text")
        
        try:
            from langdetect import detect, detect_langs
            
            lang = detect(text)
            probs = detect_langs(text)
            
            return {
                "detected_language": lang,
                "confidence": float(probs[0].prob),
                "all_probabilities": [{"lang": str(l.lang), "prob": float(l.prob)} for l in probs[:3]],
                "provider": "langdetect"
            }
            
        except ImportError:
            # Fallback - translate to English and get detected source
            result = await self._google_translate(text, "auto", "en")
            return {
                "detected_language": result.get("detected_source", "unknown"),
                "confidence": 0.8,
                "provider": "google_fallback"
            }
    
    async def _batch_translate(self, data: Dict) -> Dict:
        """Translate multiple texts"""
        texts = data.get("texts", [])
        target = data.get("target", "en")
        source = data.get("source", "auto")
        
        results = []
        for text in texts:
            result = await self._translate({"text": text, "target": target, "source": source})
            results.append(result.get("translated_text", ""))
        
        return {
            "translated_texts": results,
            "count": len(results),
            "target_language": target
        }
    
    async def _install_language(self, data: Dict) -> Dict:
        """Install Argos language package"""
        from_code = data.get("from")
        to_code = data.get("to")
        
        try:
            import argostranslate.package
            
            # Update package index
            argostranslate.package.update_package_index()
            
            # Get available packages
            available_packages = argostranslate.package.get_available_packages()
            
            # Find package
            package_to_install = next(
                filter(
                    lambda x: x.from_code == from_code and x.to_code == to_code,
                    available_packages
                ), None
            )
            
            if not package_to_install:
                return {"error": f"Package {from_code}->{to_code} not found"}
            
            # Install
            argostranslate.package.install_from_path(package_to_install.download())
            
            # Reload languages
            import argostranslate.translate
            self._argos_languages = argostranslate.translate.get_installed_languages()
            self._argos_loaded = True
            
            return {
                "installed": True,
                "from": from_code,
                "to": to_code,
                "available_packages": len(available_packages)
            }
            
        except ImportError:
            return {"error": "argostranslate not installed"}
        except Exception as e:
            return {"error": f"Installation failed: {str(e)}"}
    
    def health(self) -> Dict:
        h = super().health()
        h["languages"] = len(self.LANGUAGES)
        h["argos_loaded"] = self._argos_loaded
        h["argos_languages"] = len(self._argos_languages) if self._argos_languages else 0
        h["deepl_configured"] = self.api_key is not None
        return h
