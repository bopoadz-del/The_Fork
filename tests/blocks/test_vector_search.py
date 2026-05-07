"""Tests for Vector Search Block."""

import pytest
import os
from app.blocks import VectorSearchBlock


@pytest.fixture
def vector_search_block():
    return VectorSearchBlock()


@pytest.mark.asyncio
async def test_vector_search_block_execute_structure(vector_search_block):
    """Test that Vector Search block returns standardized JSON structure."""
    result = await vector_search_block.execute(
        "test query",
        {"operation": "search", "collection": "test"}
    )
    
    # Assert standardized keys
    assert "block" in result
    assert result["block"] == "vector_search"
    assert "request_id" in result
    assert "status" in result
    assert "result" in result
    assert "confidence" in result
    assert "metadata" in result
    assert "source_id" in result
    assert "processing_time_ms" in result


@pytest.mark.asyncio
async def test_vector_search_block_metadata(vector_search_block):
    """Test Vector Search block metadata."""
    assert vector_search_block.name == "vector_search"
    assert vector_search_block.config.version == "1.0"
    assert "results" in vector_search_block.config.supported_outputs
    assert "embeddings" in vector_search_block.config.supported_outputs
    assert vector_search_block.config.requires_api_key == False


@pytest.mark.asyncio
async def test_vector_search_embed_operation(vector_search_block):
    """Test Vector Search embed operation."""
    result = await vector_search_block.execute(
        "test text",
        {"operation": "embed"}
    )
    
    assert result["block"] == "vector_search"
    assert "result" in result


@pytest.mark.asyncio
async def test_vector_search_add_documents(vector_search_block):
    """Test Vector Search add operation."""
    documents = [
        {"text": "Document 1", "metadata": {"source": "test"}},
        {"text": "Document 2", "metadata": {"source": "test"}}
    ]
    
    result = await vector_search_block.execute(
        documents,
        {"operation": "add", "collection": "test"}
    )
    
    assert result["block"] == "vector_search"
    assert "result" in result
