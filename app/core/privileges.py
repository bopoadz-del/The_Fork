"""Admin-only blocks that execute arbitrary code.

Shared by /v1/execute, /v1/chain, the orchestrator, and MCP call_tool so the
403 gate cannot drift across surfaces.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Iterable, Optional

from fastapi import HTTPException

PRIVILEGED_BLOCKS = frozenset({"code", "sandbox"})

_caller_role: ContextVar[Optional[str]] = ContextVar("caller_role", default=None)


def set_caller_role(role: Optional[str]) -> None:
    _caller_role.set(role)


def caller_role() -> Optional[str]:
    return _caller_role.get()


def privileged_forbidden_detail(block_name: str) -> str:
    return f"Block '{block_name}' executes arbitrary code and is admin-only."


def raise_if_privileged_block(block_name: Optional[str], role: Optional[str] = None) -> None:
    if not block_name or block_name not in PRIVILEGED_BLOCKS:
        return
    effective = role if role is not None else _caller_role.get()
    if effective != "admin":
        raise HTTPException(403, privileged_forbidden_detail(block_name))


def raise_if_privileged_steps(steps: Optional[Iterable[Any]], role: Optional[str] = None) -> None:
    for step in steps or []:
        if isinstance(step, dict):
            name = step.get("block")
        else:
            name = getattr(step, "block", None)
        raise_if_privileged_block(name, role)
