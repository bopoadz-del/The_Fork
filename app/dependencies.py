"""Shared dependencies and block instance management for FastAPI app."""

import asyncio
import inspect
import logging
import os
import sys
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.blocks import BLOCK_REGISTRY
from app.core.auth import auth as auth_manager
from app.core import jwt_auth
from app.core import users as users_store

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# HAL initialization
try:
    from blocks.hal.src.detector import HALBlock
    _hal = HALBlock()
except Exception as e:
    logger.warning("HALBlock not available during startup: %s", e)
    _hal = None

# Shared block instances
block_instances: Dict[str, Any] = {}


def _create_block_instance(block_class):
    """Create block instance with proper arguments."""
    sig = inspect.signature(block_class.__init__)
    params = list(sig.parameters.keys())

    if "hal_block" in params and "config" in params:
        instance = block_class(hal_block=_hal, config={})
    else:
        instance = block_class()

    if hasattr(instance, "set_platform"):
        try:
            instance.set_platform(BLOCK_REGISTRY, block_instances, _create_block_instance, get_memory_block)
        except Exception:
            pass

    return instance


def _wire_block_dependencies(instance, block_class, name: str = None):
    """Wire requires=[] dependencies into a platform block instance.

    Mirrors UniversalAssembler.inject() for the app/blocks/ layer.
    """
    requires = getattr(block_class, "requires", []) or []
    for dep_name in requires:
        if dep_name in block_instances:
            dep_instance = block_instances[dep_name]
            if hasattr(instance, "wire"):
                instance.wire(dep_name, dep_instance)
            elif hasattr(instance, "inject"):
                instance.inject(dep_name, dep_instance)
            else:
                setattr(instance, f"{dep_name}_block", dep_instance)


def get_block_instance(block_name: str) -> Any:
    if block_name not in block_instances:
        block_class = BLOCK_REGISTRY[block_name]
        block_instances[block_name] = _create_block_instance(block_class)
        _wire_block_dependencies(block_instances[block_name], block_class, block_name)
    return block_instances[block_name]


# Memory block
_memory_block = None

try:
    from blocks.memory.src.block import MemoryBlock

    def get_memory_block():
        global _memory_block
        if _memory_block is None:
            _memory_block = MemoryBlock(None, {"max_size": 10000, "default_ttl": 3600})
            asyncio.create_task(_memory_block.initialize())
        return _memory_block

    MEMORY_AVAILABLE = True
except Exception as e:
    MEMORY_AVAILABLE = False
    get_memory_block = None  # type: ignore[assignment]
    logger.warning("Memory block not available: %s", e)


# Monitoring block
_monitoring_block = None

try:
    from blocks.monitoring.src.block import MonitoringBlock

    def get_monitoring_block():
        global _monitoring_block
        if _monitoring_block is None:
            _monitoring_block = MonitoringBlock(None, {})
            _monitoring_block.memory_block = get_memory_block()
            asyncio.create_task(_monitoring_block.initialize())
        return _monitoring_block

    MONITORING_AVAILABLE = True
except Exception as e:
    MONITORING_AVAILABLE = False
    get_monitoring_block = None  # type: ignore[assignment]
    logger.warning("Monitoring block not available: %s", e)


# Auth block
_auth_block = None

try:
    from blocks.auth.src.block import AuthBlock

    def get_auth_block():
        global _auth_block
        if _auth_block is None:
            _auth_block = AuthBlock(None, {
                "rate_limit_default": 100,
                "rate_limit_window": 60,
                "master_key": os.getenv("CEREBRUM_MASTER_KEY"),
            })
            _auth_block.memory_block = get_memory_block()
            asyncio.create_task(_auth_block.initialize())
        return _auth_block

    AUTH_AVAILABLE = True
except Exception as e:
    AUTH_AVAILABLE = False
    get_auth_block = None  # type: ignore[assignment]
    logger.warning("Auth block not available: %s", e)


security = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Dict[str, Any]:
    """Require valid API key OR valid JWT for protected endpoints.

    Try JWT first: if the bearer token is a valid JWT, resolve the user and
    return a superset dict compatible with both legacy require_api_key callers
    (who read user/tier/valid) and require_user callers (who read user_id/role/
    email/auth_method).

    If JWT decode fails (InvalidTokenError), fall through to the legacy
    auth_manager.validate_key() path unchanged so cb_dev_key and real API keys
    keep working exactly as before.
    """
    if credentials is not None:
        try:
            payload = jwt_auth.decode_token(credentials.credentials)
        except jwt_auth.InvalidTokenError:
            payload = None

        if payload is not None:
            user = users_store.get_user_by_id(payload.get("user_id"))
            if not user:
                raise HTTPException(status_code=401, detail="Token user no longer exists")
            return {
                # Legacy require_api_key keys (callers read these)
                "user": user["email"],
                "tier": user.get("role") or "user",
                "valid": True,
                # require_user / admin-check keys
                "user_id": user["id"],
                "role": user["role"],
                "email": user["email"],
                "auth_method": "jwt",
            }

    # JWT decode failed or no credentials — fall through to legacy key validation.
    # validate_key(None) raises HTTPException(401) preserving the no-credentials behavior.
    return auth_manager.validate_key(credentials)


async def init_blocks():
    """Initialize all block instances at startup."""
    # Pass 1: instantiate
    for name, block_class in BLOCK_REGISTRY.items():
        try:
            if name not in block_instances:
                block_instances[name] = _create_block_instance(block_class)
        except Exception as e:
            logger.warning("Failed to initialize block %s: %s", name, e)

    # Pass 2: wire dependencies (universal connectors)
    for name, instance in block_instances.items():
        block_class = BLOCK_REGISTRY.get(name)
        if block_class:
            _wire_block_dependencies(instance, block_class, name)

    if get_memory_block:
        get_memory_block()
    if get_monitoring_block:
        get_monitoring_block()
    if get_auth_block:
        get_auth_block()


async def require_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """Resolve the caller to a user.

    Accepts a Bearer JWT (decoded -> user_id) OR a legacy API key
    (validated -> the singleton 'system' user). Returns at least
    {user_id, role, email, auth_method}.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = credentials.credentials

    # 1) Try JWT first.
    try:
        payload = jwt_auth.decode_token(token)
    except jwt_auth.InvalidTokenError:
        payload = None

    if payload is not None:
        user = users_store.get_user_by_id(payload.get("user_id"))
        if not user:
            raise HTTPException(status_code=401, detail="Token user no longer exists")
        return {
            "user_id": user["id"],
            "role": user["role"],
            "email": user["email"],
            "auth_method": "jwt",
        }

    # 2) Fall back to legacy API key -> system user.
    auth_manager.validate_key(credentials)  # raises 401/429 on bad key
    sys_user = users_store.get_user_by_id(users_store.SYSTEM_USER_ID)
    return {
        "user_id": users_store.SYSTEM_USER_ID,
        "role": sys_user["role"] if sys_user else "admin",
        "email": sys_user["email"] if sys_user else "system@local",
        "auth_method": "api_key",
    }
