from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import MEMORY_AVAILABLE, get_memory_block, require_api_key

router = APIRouter()

_MEMORY_ACTIONS = {"get", "set", "delete", "flush", "keys", "exists"}


class MemoryOpRequest(BaseModel):
    """Typed body for a memory cache operation. Only these fields reach the
    block — arbitrary client keys are no longer splatted into execute()."""
    key: Optional[str] = None
    value: Optional[Any] = None
    ttl: Optional[int] = None


@router.get("/v1/memory/stats")
async def memory_stats(auth: dict = Depends(require_api_key)):
    """Get memory cache statistics"""
    if not MEMORY_AVAILABLE:
        raise HTTPException(status_code=503, detail="Memory block not available")
    block = get_memory_block()
    return await block.execute({"action": "stats"})


@router.post("/v1/memory/{action}")
async def memory_operation(
    action: str,
    request: MemoryOpRequest,
    auth: dict = Depends(require_api_key),
):
    """Memory cache operations: get, set, delete, flush, keys, exists"""
    if not MEMORY_AVAILABLE:
        raise HTTPException(status_code=503, detail="Memory block not available")

    if action not in _MEMORY_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    block = get_memory_block()
    payload = {"action": action, **request.model_dump(exclude_none=True)}
    return await block.execute(payload)
