from typing import Optional

from fastapi import APIRouter, HTTPException, Depends

from app.dependencies import AUTH_AVAILABLE, get_auth_block, require_api_key

router = APIRouter()


def _require_admin(auth_result: dict):
    """Ensure the authenticated key has admin role."""
    if auth_result.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


@router.post("/v1/auth/validate")
async def validate_key(request: dict, auth: dict = Depends(require_api_key)):
    """Validate an API key"""
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    block = get_auth_block()
    api_key = request.get("api_key") or request.get("key")
    if not api_key:
        raise HTTPException(status_code=422, detail="api_key or key required")
    return await block.execute({"action": "validate", "api_key": api_key})


@router.post("/v1/auth/keys")
async def create_key(request: dict, auth: dict = Depends(require_api_key)):
    """Create a new API key (admin only)"""
    _require_admin(auth)
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    block = get_auth_block()
    return await block.execute({"action": "create_key", **request})


@router.delete("/v1/auth/keys/{api_key}")
async def delete_key(api_key: str, auth: dict = Depends(require_api_key)):
    """Delete (revoke) an API key by URL (admin only)"""
    _require_admin(auth)
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    block = get_auth_block()
    return await block.execute({"action": "revoke_key", "api_key": api_key})


@router.get("/v1/auth/keys")
async def list_keys(admin_key: Optional[str] = None, auth: dict = Depends(require_api_key)):
    """List all API keys (admin only)"""
    _require_admin(auth)
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    block = get_auth_block()
    return await block.execute({"action": "list_keys", "admin_key": admin_key})


@router.post("/v1/auth/keys/revoke")
async def revoke_key(request: dict, auth: dict = Depends(require_api_key)):
    """Revoke an API key (admin only)"""
    _require_admin(auth)
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    block = get_auth_block()
    api_key = request.get("api_key") or request.get("key")
    if not api_key:
        raise HTTPException(status_code=422, detail="api_key or key required")
    return await block.execute({"action": "revoke_key", "api_key": api_key})


@router.post("/v1/auth/keys/rotate")
async def rotate_key(request: dict, auth: dict = Depends(require_api_key)):
    """Rotate an API key (admin only)"""
    _require_admin(auth)
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    block = get_auth_block()
    api_key = request.get("api_key") or request.get("key")
    if not api_key:
        raise HTTPException(status_code=422, detail="api_key or key required")
    return await block.execute({"action": "rotate_key", "api_key": api_key})


@router.post("/v1/auth/check")
async def check_permission(request: dict, auth: dict = Depends(require_api_key)):
    """Check if key has a permission"""
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    block = get_auth_block()
    api_key = request.get("api_key") or request.get("key")
    if not api_key:
        raise HTTPException(status_code=422, detail="api_key or key required")
    return await block.execute({
        "action": "check_permission",
        "api_key": api_key,
        "block": request.get("block")
    })


@router.get("/v1/auth/usage")
async def get_usage(key: Optional[str] = None, api_key: Optional[str] = None, auth: dict = Depends(require_api_key)):
    """Get usage stats for a key"""
    if not AUTH_AVAILABLE:
        raise HTTPException(status_code=503, detail="Auth not available")
    target = api_key or key
    if not target:
        raise HTTPException(status_code=422, detail="key or api_key query param required")
    block = get_auth_block()
    return await block.execute({"action": "get_usage", "api_key": target})
