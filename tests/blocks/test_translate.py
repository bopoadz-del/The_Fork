"""Tests for Translate Block."""

import pytest
from app.blocks import TranslateBlock


@pytest.fixture
def translate_block():
    return TranslateBlock()


@pytest.mark.asyncio
async def test_translate_block_execute_structure(translate_block):
    """Test that Translate block returns standardized JSON structure."""
    result = await translate_block.execute(
        "Hello world",
        {"target": "es", "provider": "mock"}
    )
    
    # Assert standardized keys
    assert "block" in result
    assert result["block"] == "translate"
    assert "request_id" in result
    assert "status" in result
    assert "result" in result
    assert "confidence" in result
    assert "metadata" in result
    assert "source_id" in result
    assert "processing_time_ms" in result


@pytest.mark.asyncio
async def test_translate_block_metadata(translate_block):
    """Test Translate block metadata."""
    assert translate_block.name == "translate"
    assert translate_block.config.version == "1.0"
    assert "translated_text" in translate_block.config.supported_outputs
    assert translate_block.config.requires_api_key == False


@pytest.mark.asyncio
async def test_translate_block_chaining(translate_block):
    """Test Translate block can receive output from previous block."""
    # Simulate previous block output
    previous_result = {
        "result": {
            "text": "This is a document in English."
        }
    }
    
    result = await translate_block.execute(
        previous_result,
        {"target": "fr", "provider": "mock"}
    )
    
    assert result["block"] == "translate"
    assert "result" in result
