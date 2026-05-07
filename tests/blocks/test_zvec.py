"""Tests for Zvec Block."""

import pytest
from app.blocks import ZvecBlock


@pytest.fixture
def zvec_block():
    return ZvecBlock()


@pytest.mark.asyncio
async def test_zvec_block_execute_structure(zvec_block):
    """Test that Zvec block returns standardized JSON structure."""
    result = await zvec_block.execute(
        "Hello world",
        {"operation": "embed"}
    )
    
    # Assert standardized keys
    assert "block" in result
    assert result["block"] == "zvec"
    assert "request_id" in result
    assert "status" in result
    assert "result" in result
    assert "confidence" in result
    assert "metadata" in result
    assert "source_id" in result
    assert "processing_time_ms" in result


@pytest.mark.asyncio
async def test_zvec_block_metadata(zvec_block):
    """Test Zvec block metadata."""
    assert zvec_block.name == "zvec"
    assert zvec_block.config.version == "1.0"
    assert "embeddings" in zvec_block.config.supported_outputs
    assert "classifications" in zvec_block.config.supported_outputs
    assert zvec_block.config.requires_api_key == False


@pytest.mark.asyncio
async def test_zvec_block_embed(zvec_block):
    """Test Zvec block embed operation."""
    result = await zvec_block.execute(
        "Test text",
        {"operation": "embed"}
    )
    
    assert result["block"] == "zvec"
    assert result["result"]["operation"] == "embed"
    assert "embeddings" in result["result"]


@pytest.mark.asyncio
async def test_zvec_block_classify(zvec_block):
    """Test Zvec block classify operation."""
    result = await zvec_block.execute(
        "This is a great product!",
        {
            "operation": "classify",
            "labels": ["positive", "negative", "neutral"]
        }
    )
    
    assert result["block"] == "zvec"
    assert result["result"]["operation"] == "classify"
    assert "top_label" in result["result"]
    assert "top_score" in result["result"]


@pytest.mark.asyncio
async def test_zvec_block_similarity(zvec_block):
    """Test Zvec block similarity operation."""
    result = await zvec_block.execute(
        ["apple", "banana", "fruit"],
        {"operation": "similarity"}
    )
    
    assert result["block"] == "zvec"
    assert result["result"]["operation"] == "similarity"
    assert "similarity_matrix" in result["result"]


@pytest.mark.asyncio
async def test_zvec_block_search(zvec_block):
    """Test Zvec block search operation."""
    corpus = [
        "Python programming tutorial",
        "Machine learning basics",
        "Cooking recipes"
    ]
    
    result = await zvec_block.execute(
        "machine learning",
        {"operation": "search", "corpus": corpus, "top_k": 2}
    )
    
    assert result["block"] == "zvec"
    assert result["result"]["operation"] == "search"
    assert "matches" in result["result"]
