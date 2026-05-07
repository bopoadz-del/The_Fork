from blocks.base import LegoBlock
from typing import Dict, Any
import asyncio

class VoiceBlock(LegoBlock):
    """TTS/STT - Text-to-Speech, Speech-to-Text"""
    name = "voice"
    version = "1.0.0"
    requires = ["config"]
    layer = 4  # Utility layer
    tags = ["voice", "tts", "stt", "audio", "utility"]
    default_config = {
        "tts_engine": "edge",
        "stt_engine": "whisper",
        "voice": "en-US-AriaNeural"
    }
    
    PROVIDERS = {
        "elevenlabs": {"tts": True, "stt": False, "url": "https://api.elevenlabs.io/v1"},
        "whisper": {"tts": False, "stt": True, "url": "https://api.openai.com/v1/audio"},
        "edge_tts": {"tts": True, "stt": False, "url": "local"},
        "local_whisper": {"tts": False, "stt": True, "url": "local"}
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.api_key = config.get("elevenlabs_key") or config.get("openai_key")
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "tts":
            return await self._text_to_speech(input_data)
        elif action == "stt":
            return await self._speech_to_text(input_data)
        return {"error": "Unknown action"}
    
    async def _text_to_speech(self, data: Dict) -> Dict:
        text = data.get("text")
        voice_id = data.get("voice_id", "premade/echo")
        provider = data.get("provider", "edge_tts")
        
        if provider == "edge_tts":
            try:
                import edge_tts
                communicate = edge_tts.Communicate(text, voice_id)
                audio_bytes = b"".join([chunk async for chunk in communicate.stream()])
                return {"audio": audio_bytes, "format": "mp3", "provider": "edge_tts"}
            except ImportError:
                return {"error": "edge_tts not installed, run: pip install edge-tts"}
        
        elif provider == "elevenlabs":
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.PROVIDERS['elevenlabs']['url']}/text-to-speech/{voice_id}",
                    headers={"xi-api-key": self.api_key},
                    json={"text": text, "model_id": "eleven_monolingual_v1"}
                ) as resp:
                    audio = await resp.read()
                    return {"audio": audio, "format": "mp3", "provider": "elevenlabs"}
        
        return {"error": f"Provider {provider} not supported"}
    
    async def _speech_to_text(self, data: Dict) -> Dict:
        audio_bytes = data.get("audio")
        provider = data.get("provider", "whisper")
        
        if provider == "local_whisper":
            try:
                import whisper
                model = whisper.load_model("base")
                # Save temp file
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    f.write(audio_bytes)
                    temp_path = f.name
                result = model.transcribe(temp_path)
                import os
                os.unlink(temp_path)
                return {"text": result["text"], "provider": "local_whisper"}
            except ImportError:
                return {"error": "whisper not installed, run: pip install openai-whisper"}
        
        elif provider == "whisper":
            import aiohttp
            form = aiohttp.FormData()
            form.add_field("file", audio_bytes, filename="audio.mp3")
            form.add_field("model", "whisper-1")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=form
                ) as resp:
                    result = await resp.json()
                    return {"text": result.get("text", ""), "provider": "openai_whisper"}
        
        return {"error": f"Provider {provider} not supported"}
    
    def health(self) -> Dict:
        h = super().health()
        h["providers"] = list(self.PROVIDERS.keys())
        return h
