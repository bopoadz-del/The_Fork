from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Extract and validate API key from Authorization header.
    
    Never grants admin access by default. If auth block is unavailable,
    reject the request with 503.
    """
    blocks = request.app.state.blocks
    api_key = credentials.credentials
    
    # Validate key
    auth = blocks.get("auth")
    if not auth:
        # No auth block = service unavailable, NOT dev mode
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    
    validation = await auth.execute({
        "action": "validate",
        "api_key": api_key
    })
    
    if not validation.get("valid"):
        raise HTTPException(status_code=401, detail=validation.get("reason", "invalid_key"))
    
    # Check rate limit
    rate_check = await auth.execute({
        "action": "check_rate_limit",
        "api_key": api_key
    })
    
    if not rate_check.get("allowed"):
        raise HTTPException(
            status_code=429, 
            detail={
                "error": "rate_limit_exceeded",
                "retry_after": rate_check.get("retry_after", 3600)
            }
        )
    
    # Add rate limit headers to response
    request.state.rate_limit = rate_check
    
    return {
        "api_key": api_key,
        "role": validation.get("role"),
        "owner": validation.get("owner"),
        "rate_limit": rate_check
    }

async def check_block_permission(request: Request, user: dict, block_name: str):
    """Check if user can access specific block"""
    blocks = request.app.state.blocks
    auth = blocks.get("auth")
    
    if not auth:
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    
    permission = await auth.execute({
        "action": "check_permission",
        "api_key": user["api_key"],
        "block": block_name
    })
    
    if not permission.get("allowed"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": permission.get("reason"),
                "role": user["role"],
                "upgrade_required": permission.get("reason") == "insufficient_permissions"
            }
        )
    
    return True
