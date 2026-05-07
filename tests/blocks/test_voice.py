"""Tests for Voice Block."""

import pytest
from app.blocks import VoiceBlock


@pytest.fixture
def voice_block():
    return VoiceBlock()


@pytest.mark.asyncio
async def test_voice_block_execute_structure(voice_block):
    """Test that Voice block returns standardized JSON structure."""
    result = await voice_block.execute(
        "Hello world",
        {"operation": "tts", "provider": "mock"}
    )
    
    # Assert standardized keys
    assert "block" in result
    assert result["block"] == "voice"
    assert "request_id" in result
    assert "status" in result
    assert "result" in result
    assert "confidence" in result
    assert "metadata" in result
    assert "source_id" in result
    assert "processing_time_ms" in result


@pytest.mark.asyncio
async def test_voice_block_metadata(voice_block):
    """Test Voice block metadata."""
    assert voice_block.name == "voice"
    assert voice_block.config.version == "1.0"
    assert "text" in voice_block.config.supported_outputs
    assert "audio" in voice_block.config.supported_outputs
    assert voice_block.config.requires_api_key == False


@pytest.mark.asyncio
async def test_voice_block_tts(voice_block):
    """Test Voice block text-to-speech."""
    result = await voice_block.execute(
        "Hello world",
        {"operation": "tts", "provider": "mock"}
    )
    
    assert result["block"] == "voice"
    assert "result" in result
    assert result["result"]["operation"] == "tts"


@pytest.mark.asyncio
async def test_voice_block_stt(voice_block):
    """Test Voice block speech-to-text."""
    # Mock audio input
    result = await voice_block.execute(
        {"audio_base64": "bW9ja19hdWRpb19kYXRh"},  # base64 encoded mock data
        {"operation": "stt"}
    )
    
    assert result["block"] == "voice"
    assert "result" in result
