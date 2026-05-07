"""Tests for Code Block."""

import pytest
from app.blocks import CodeBlock


@pytest.fixture
def code_block():
    return CodeBlock()


@pytest.mark.asyncio
async def test_code_block_execute_structure(code_block):
    """Test that Code block returns standardized JSON structure."""
    result = await code_block.execute(
        "print('hello')",
        {"operation": "execute", "language": "python"}
    )
    
    # Assert standardized keys
    assert "block" in result
    assert result["block"] == "code"
    assert "request_id" in result
    assert "status" in result
    assert "result" in result
    assert "confidence" in result
    assert "metadata" in result
    assert "source_id" in result
    assert "processing_time_ms" in result


@pytest.mark.asyncio
async def test_code_block_metadata(code_block):
    """Test Code block metadata."""
    assert code_block.name == "code"
    assert code_block.config.version == "1.0"
    assert "result" in code_block.config.supported_outputs
    assert "analysis" in code_block.config.supported_outputs
    assert code_block.config.requires_api_key == False


@pytest.mark.asyncio
async def test_code_block_analyze(code_block):
    """Test Code block analyze operation."""
    code = """
def hello():
    return "world"
"""
    result = await code_block.execute(
        code,
        {"operation": "analyze", "language": "python"}
    )
    
    assert result["block"] == "code"
    assert result["result"]["operation"] == "analyze"


@pytest.mark.asyncio
async def test_code_block_lint(code_block):
    """Test Code block lint operation."""
    result = await code_block.execute(
        "x = 1; y = 2",
        {"operation": "lint", "language": "python"}
    )
    
    assert result["block"] == "code"
    assert result["result"]["operation"] == "lint"
