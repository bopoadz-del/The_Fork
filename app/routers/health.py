from datetime import datetime, timezone
from fastapi import APIRouter

from app.blocks import BLOCK_REGISTRY
from app.dependencies import block_instances, MONITORING_AVAILABLE, get_monitoring_block

router = APIRouter()


@router.get("/health")
def health():
    """Health check."""
    return {
        "status": "healthy",
        "blocks_loaded": len(block_instances),
        "blocks_available": len(BLOCK_REGISTRY),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stats")
def stats():
    """Platform stats."""
    return {
        "blocks": [name for name in BLOCK_REGISTRY.keys() if not name.startswith("container_")],
        "total_blocks": len(BLOCK_REGISTRY),
        "version": "2.0.0",
    }


@router.get("/v1/health")
def health_v1():
    """Health check for Render (v1 API)."""
    return health()


@router.get("/v1/system/health")
async def full_health():
    """Complete system health with predictions."""
    if not MONITORING_AVAILABLE:
        return health_v1()
    block = get_monitoring_block()
    return await block.execute({"action": "health_report"})
