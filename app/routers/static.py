from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.blocks import BLOCK_REGISTRY

router = APIRouter()


@router.get("/", response_class=FileResponse)
async def root():
    """Serve Block Store UI."""
    return FileResponse(
        "app/static/index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )


@router.get("/landing", response_class=FileResponse)
async def landing():
    """Serve legacy landing page."""
    return FileResponse(
        "app/static/landing/index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )


@router.get("/api")
def api_info():
    """API info."""
    return {
        "name": "Cerebrum Blocks",
        "version": "2.0.0",
        "tagline": "Build AI Like Lego",
        "blocks": len(BLOCK_REGISTRY),
        "endpoints": {
            "blocks": "/v1/blocks",
            "execute": "/v1/execute",
            "chain": "/v1/chain",
            "chat": "/v1/chat",
            "health": "/v1/health",
        },
    }
