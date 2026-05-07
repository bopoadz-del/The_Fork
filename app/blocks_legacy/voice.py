"""Voice Block - Speech-to-text and text-to-speech."""

import os
import io
import base64
from typing import Any, Dict, Optional
from app.core.block import BaseBlock, BlockConfig


class VoiceBlock(BaseBlock):
    """Speech-to-text (STT) and text-to-speech (TTS) processing."""
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="voice",
            version="1.0",
            description="Speech-to-text and text-to-speech processing",
            supported_inputs=["audio", "text"],
            supported_outputs=["text", "audio"]
        ,
            layer=3,
            tags=["domain", "audio", "tts", "stt"]))
        self._openai_available = self._check_openai()
        self._speech_recognition_available = self._check_speech_recognition()
        self._gtts_available = self._check_gtts()
    
    def _check_openai(self) -> bool:
        try:
            import openai
            return True
        except ImportError:
            return False
    
    def _check_speech_recognition(self) -> bool:
        try:
            import speech_recognition as sr
            return True
        except ImportError:
            return False
    
    def _check_gtts(self) -> bool:
        try:
            from gtts import gTTS
            return True
        except ImportError:
            return False
    
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process voice request (STT or TTS)."""
        params = params or {}
        operation = params.get("operation", "auto")  # stt, tts, auto
        
        if operation == "auto":
            operation = self._detect_operation(input_data)
        
        if operation == "stt":
            return await self._speech_to_text(input_data, params)
        elif operation == "tts":
            return await self._text_to_speech(input_data, params)
        else:
            return {
                "error": "Unknown operation. Use 'stt' or 'tts'.",
                "confidence": 0.0
            }
    
    def _detect_operation(self, input_data: Any) -> str:
        """Auto-detect operation type from input."""
        if isinstance(input_data, str):
            return "tts"
        if isinstance(input_data, dict):
            if "audio" in input_data or "audio_path" in input_data:
                return "stt"
            if "text" in input_data:
                return "tts"
        return "stt"  # Default
    
    async def _speech_to_text(self, input_data: Any, params: Dict) -> Dict:
        """Convert speech to text."""
        provider = params.get("provider", "auto")
        language = params.get("language", "en-US")
        
        audio_data = self._get_audio_data(input_data)
        
        result = {
            "operation": "stt",
            "language": language,
        }
        
        if provider == "openai" and self._openai_available:
            import openai
            
            client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            
            try:
                audio_file = io.BytesIO(audio_data)
                audio_file.name = "audio.mp3"
                
                transcript = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language[:2] if len(language) > 2 else language
                )
                
                result["text"] = transcript.text
                result["provider"] = "openai"
                result["confidence"] = 0.92
                
            except Exception as e:
                result["text"] = f"[OpenAI STT Error: {str(e)}]"
                result["confidence"] = 0.0
                
        elif self._speech_recognition_available:
            import speech_recognition as sr
            
            recognizer = sr.Recognizer()
            
            try:
                # Convert bytes to AudioFile
                audio_file = io.BytesIO(audio_data)
                with sr.AudioFile(audio_file) as source:
                    audio = recognizer.record(source)
                
                text = recognizer.recognize_google(audio, language=language)
                
                result["text"] = text
                result["provider"] = "google_speech"
                result["confidence"] = 0.85
                
            except Exception as e:
                result["text"] = f"[Speech Recognition Error: {str(e)}]"
                result["confidence"] = 0.0
        else:
            result["text"] = "[No STT engine available]"
            result["confidence"] = 0.0
        
        return result
    
    async def _text_to_speech(self, input_data: Any, params: Dict) -> Dict:
        """Convert text to speech."""
        provider = params.get("provider", "auto")
        language = params.get("language", "en")
        voice = params.get("voice", "alloy")  # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer
        
        text = self._get_text(input_data)
        
        result = {
            "operation": "tts",
            "text": text[:100] + "..." if len(text) > 100 else text,
            "language": language,
        }
        
        if provider == "openai" and self._openai_available:
            import openai
            
            client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            
            try:
                response = await client.audio.speech.create(
                    model="tts-1",
                    voice=voice,
                    input=text
                )
                
                audio_data = response.read()
                
                result["audio_base64"] = base64.b64encode(audio_data).decode("utf-8")
                result["format"] = "mp3"
                result["size_bytes"] = len(audio_data)
                result["provider"] = "openai"
                result["confidence"] = 0.95
                
            except Exception as e:
                result["error"] = str(e)
                result["confidence"] = 0.0
                
        elif self._gtts_available:
            from gtts import gTTS
            
            try:
                tts = gTTS(text=text, lang=language[:2] if len(language) > 2 else language)
                
                mp3_fp = io.BytesIO()
                tts.write_to_fp(mp3_fp)
                mp3_fp.seek(0)
                audio_data = mp3_fp.read()
                
                result["audio_base64"] = base64.b64encode(audio_data).decode("utf-8")
                result["format"] = "mp3"
                result["size_bytes"] = len(audio_data)
                result["provider"] = "gtts"
                result["confidence"] = 0.80
                
            except Exception as e:
                result["error"] = str(e)
                result["confidence"] = 0.0
        else:
            result["error"] = "No TTS engine available"
            result["confidence"] = 0.0
        
        return result
    
    def _get_audio_data(self, input_data: Any) -> bytes:
        """Extract audio data from input."""
        if isinstance(input_data, dict):
            if "audio" in input_data and isinstance(input_data["audio"], bytes):
                return input_data["audio"]
            if "audio_base64" in input_data:
                return base64.b64decode(input_data["audio_base64"])
            if "audio_path" in input_data:
                with open(input_data["audio_path"], "rb") as f:
                    return f.read()
            if "source_id" in input_data:
                with open(f"/app/data/{input_data['source_id']}", "rb") as f:
                    return f.read()
        if isinstance(input_data, str) and os.path.exists(input_data):
            with open(input_data, "rb") as f:
                return f.read()
        raise ValueError("Invalid audio input")
    
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
