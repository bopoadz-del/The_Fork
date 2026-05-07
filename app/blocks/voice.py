"""Voice Block - gTTS (TTS) + SpeechRecognition/Google (STT)"""

import asyncio
import base64
import os
import tempfile
from typing import Any, Dict

from app.core.universal_base import UniversalBlock

_SUPPORTED_LANGS = {
    "en": "English", "es": "Spanish", "ar": "Arabic", "fr": "French",
    "de": "German", "zh": "Chinese", "ja": "Japanese", "hi": "Hindi",
    "pt": "Portuguese", "ru": "Russian", "tr": "Turkish", "ko": "Korean",
    "it": "Italian", "nl": "Dutch", "pl": "Polish",
}


def _tts_sync(text: str, lang: str) -> tuple[bytes, str]:
    from gtts import gTTS
    tts = gTTS(text=text, lang=lang, slow=False)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tts.save(f.name)
        tmp = f.name
    try:
        with open(tmp, "rb") as f:
            data = f.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return data, "mp3"


def _stt_sync(file_path: str) -> str:
    import speech_recognition as sr
    r = sr.Recognizer()
    with sr.AudioFile(file_path) as source:
        r.adjust_for_ambient_noise(source, duration=0.5)
        audio = r.record(source)
    # Uses Google's free STT API — no key required
    return r.recognize_google(audio)


class VoiceBlock(UniversalBlock):
    """TTS via gTTS (free, no key) · STT via Google SpeechRecognition (free, no key)"""

    name = "voice"
    version = "2.0"
    description = "Text-to-speech (gTTS) and speech-to-text (Google STT) — no API key needed"
    layer = 3
    tags = ["domain", "audio", "tts", "stt"]
    requires = []

    ui_schema = {
        "input": {
            "type": "audio",
            "accept": [".mp3", ".wav", ".webm", ".m4a"],
            "placeholder": "Record or upload audio...",
            "multiline": False,
        },
        "output": {
            "type": "text",
            "fields": [{"name": "text", "type": "text", "label": "Transcription"}],
        },
        "quick_actions": [
            {"icon": "🔊", "label": "Text to Speech", "prompt": "Convert this to speech"},
            {"icon": "🎤", "label": "Transcribe", "prompt": "Transcribe audio"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        operation = params.get("operation", params.get("action", "tts"))

        # Resolve text / file_path from various input shapes
        text = ""
        file_path = None
        if isinstance(input_data, str):
            if os.path.exists(input_data):
                file_path = input_data
            else:
                text = input_data
        elif isinstance(input_data, dict):
            text = input_data.get("text") or input_data.get("input") or ""
            file_path = input_data.get("file_path") or input_data.get("path")
            operation = input_data.get("operation", operation)

        # ── TTS ──────────────────────────────────────────────────────────────
        if operation == "tts":
            if not text:
                return {"status": "error", "error": "Text required for TTS"}
            lang = params.get("lang", params.get("language", "en"))
            if lang not in _SUPPORTED_LANGS:
                lang = "en"

            try:
                loop = asyncio.get_event_loop()
                audio_bytes, fmt = await loop.run_in_executor(None, _tts_sync, text[:3000], lang)
                b64 = base64.b64encode(audio_bytes).decode()
                return {
                    "status": "success",
                    "operation": "tts",
                    "audio_base64": b64,
                    "format": fmt,
                    "lang": lang,
                    "lang_name": _SUPPORTED_LANGS[lang],
                    "chars": len(text),
                    "size_bytes": len(audio_bytes),
                    "note": "Decode audio_base64 and play as MP3",
                }
            except Exception as e:
                return {"status": "error", "error": str(e), "operation": "tts"}

        # ── STT ──────────────────────────────────────────────────────────────
        elif operation in ("stt", "transcribe"):
            if not file_path:
                return {"status": "error", "error": "file_path required for STT (WAV/AIFF/FLAC supported)"}
            if not os.path.exists(file_path):
                return {"status": "error", "error": f"Audio file not found: {file_path}"}
            try:
                loop = asyncio.get_event_loop()
                transcript = await loop.run_in_executor(None, _stt_sync, file_path)
                return {
                    "status": "success",
                    "operation": "stt",
                    "text": transcript,
                    "file": os.path.basename(file_path),
                }
            except Exception as e:
                return {"status": "error", "error": str(e), "operation": "stt"}

        # ── Languages list ────────────────────────────────────────────────────
        elif operation == "languages":
            return {"status": "success", "languages": _SUPPORTED_LANGS}

        return {"status": "error", "error": f"Unknown operation: {operation}. Use: tts, stt, languages"}
