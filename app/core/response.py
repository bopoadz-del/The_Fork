"""Standardized response format."""

from typing import Any, Dict
from pydantic import BaseModel


class StandardResponse(BaseModel):
    """Standard response format for all blocks."""
    block: str
    request_id: str
    status: str
    result: Dict[str, Any]
    confidence: float = 1.0
    metadata: Dict[str, Any] = {}
    source_id: str
    processing_time_ms: int
