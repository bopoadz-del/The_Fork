from fastapi import APIRouter, HTTPException

from app.dependencies import MEMORY_AVAILABLE, get_memory_block

router = APIRouter()


@router.get("/v1/memory/stats")
async def memory_stats():
    """Get memory cache statistics"""
    if not MEMORY_AVAILABLE:
        raise HTTPException(status_code=503, detail="Memory block not available")
    block = get_memory_block()
    return await block.execute({"action": "stats"})


@router.post("/v1/memory/{action}")
async def memory_operation(action: str, request: dict):
    """Memory cache operations: get, set, delete, flush, keys"""
    if not MEMORY_AVAILABLE:
        raise HTTPException(status_code=503, detail="Memory block not available")

    if action not in ["get", "set", "delete", "flush", "keys", "exists"]:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    block = get_memory_block()
    return await block.execute({"action": action, **request})
