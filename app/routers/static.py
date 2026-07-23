import os

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

from app.blocks import BLOCK_REGISTRY

router = APIRouter()

_REACT_INDEX = "frontend/dist/index.html"
_LEGACY_INDEX = "app/static/index.html"

# Path prefixes the SPA fallback must NOT shadow. Anything under these is
# either an API endpoint, a mounted StaticFiles directory, or a framework
# route (docs, openapi). Listed without leading slash because we match
# against full_path which FastAPI strips the leading slash from.
_RESERVED_PREFIXES: tuple[str, ...] = (
    "v1/", "api", "static/", "dashboard/", "assets/",
    "health", "docs", "redoc", "openapi.json", "mcp",
)


def _index_path() -> str:
    return _REACT_INDEX if os.path.isfile(_REACT_INDEX) else _LEGACY_INDEX


def _serve_spa() -> FileResponse:
    return FileResponse(
        _index_path(),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@router.get("/", response_class=FileResponse)
async def root():
    return _serve_spa()


@router.get("/api")
def api_info():
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


# SPA fallback: any GET that isn't an API/static path returns the React
# app's index.html so client-side routes like /login or /projects/:id
# resolve to the bundle and React Router takes over.
@router.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if any(full_path.startswith(p) for p in _RESERVED_PREFIXES):
        return Response(status_code=404)
    return _serve_spa()
