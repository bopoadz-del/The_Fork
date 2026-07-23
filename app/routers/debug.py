import os

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_api_key

router = APIRouter()


def _is_production() -> bool:
    env = os.getenv("ENV", os.getenv("ENVIRONMENT", "production")).strip().lower()
    return env == "production"


def _require_non_production():
    if _is_production():
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/debug/env")
def debug_env(auth: dict = Depends(require_api_key)):
    """Debug endpoint — gated to non-production + admin only."""
    _require_non_production()
    if auth.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return {
        "environment": os.getenv("ENV", "unknown"),
        "data_dir": os.getenv("DATA_DIR", "not_set"),
    }


@router.get("/v1/debug/env")
def debug_env_v1(auth: dict = Depends(require_api_key)):
    """Debug endpoint (v1 alias)."""
    return debug_env(auth)
