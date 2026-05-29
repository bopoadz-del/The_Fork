"""Translate Block - deep-translator (Google Translate, no API key)"""

import asyncio
from typing import Any, Dict

from app.core.universal_base import UniversalBlock

_LANG_CODES = {
    "english": "en", "spanish": "es", "arabic": "ar", "french": "fr",
    "german": "de", "chinese": "zh-CN", "japanese": "ja", "hindi": "hi",
    "portuguese": "pt", "russian": "ru", "turkish": "tr", "korean": "ko",
    "italian": "it", "dutch": "nl", "polish": "pl", "thai": "th",
    "vietnamese": "vi", "indonesian": "id", "malay": "ms", "ukrainian": "uk",
}


def _normalize_lang(lang: str) -> str:
    if not lang:
        return "en"
    l = lang.lower().strip()
    return _LANG_CODES.get(l, l)


def _translate_sync(text: str, source: str, target: str) -> tuple[str, str]:
    from deep_translator import GoogleTranslator, single_detection
    detected = source
    if source == "auto":
        try:
            detected = single_detection(text[:200], api_key=None)
        except Exception:
            detected = "unknown"
    translator = GoogleTranslator(source=source, target=target)
    translated = translator.translate(text)
    return translated, detected


class TranslateBlock(UniversalBlock):
    """Multi-language translation via Google Translate (deep-translator, no API key)"""

    name = "translate"
    version = "2.0"
    description = "Translate text between 20+ languages — no API key needed"
    layer = 3
    tags = ["domain", "nlp", "translation"]
    requires = []

    # Canonical text key for chain unwrapping — overrides the orchestrator's
    # global priority list. Without this, a translate -> chat chain leans on
    # the fact that "translated" happens to be in _TEXT_OUTPUT_FIELDS; with
    # this, the contract is explicit and survives any list reordering.
    # See CONTRIBUTING.md "Block output contracts".
    text_output_field = "translated"

    ui_schema = {
        "input": {
            "type": "text",
            "accept": None,
            "placeholder": "Enter text to translate...",
            "multiline": True,
        },
        "output": {
            "type": "text",
            "fields": [
                {"name": "translated", "type": "text", "label": "Translation"},
                {"name": "source_language", "type": "text", "label": "From"},
                {"name": "target_language", "type": "text", "label": "To"},
            ],
        },
        "quick_actions": [
            {"icon": "🇪🇸", "label": "To Spanish", "prompt": "Translate to Spanish: "},
            {"icon": "🇸🇦", "label": "To Arabic", "prompt": "Translate to Arabic: "},
            {"icon": "🇫🇷", "label": "To French", "prompt": "Translate to French: "},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}

        text = ""
        if isinstance(input_data, str):
            text = input_data
        elif isinstance(input_data, dict):
            text = input_data.get("text") or input_data.get("input") or ""
        text = text.strip()

        if not text:
            return {"status": "error", "error": "Text is required"}

        if params.get("operation") == "languages":
            return {"status": "success", "languages": _LANG_CODES}

        target = _normalize_lang(params.get("target") or params.get("target_language") or "es")
        source = _normalize_lang(params.get("source") or params.get("source_language") or "auto")

        try:
            loop = asyncio.get_event_loop()
            translated, detected = await loop.run_in_executor(
                None, _translate_sync, text[:5000], source, target
            )
            return {
                "status": "success",
                "original": text,
                "translated": translated,
                "source_language": detected if source == "auto" else source,
                "target_language": target,
                "char_count": len(text),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "target": target}
