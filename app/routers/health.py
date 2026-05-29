from datetime import datetime, timezone
from fastapi import APIRouter

from app.blocks import BLOCK_REGISTRY, FAILED_BLOCKS
from app.dependencies import block_instances, MONITORING_AVAILABLE, get_monitoring_block

router = APIRouter()


@router.get("/health")
def health():
    """Health check.

    Reports block-load failures so a deploy that's missing optional deps
    (and silently dropped a block at import time) is visible without
    grepping logs. ``status`` stays "healthy" — failed blocks are by
    design non-fatal (PR #8) — but ``blocks_failed`` lets operators
    notice if e.g. all ML blocks dropped because requirements-ml.txt
    wasn't installed.
    """
    return {
        "status": "healthy",
        "blocks_loaded": len(block_instances),
        "blocks_available": len(BLOCK_REGISTRY),
        "blocks_failed": {
            name: reason for name, reason in sorted(FAILED_BLOCKS.items())
        },
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
    """Health check (v1 API)."""
    return health()


@router.get("/v1/system/health")
async def full_health():
    """Complete system health with predictions."""
    if not MONITORING_AVAILABLE:
        return health_v1()
    block = get_monitoring_block()
    return await block.execute({"action": "health_report"})
