"""Admin-only blocks: the ones that reach past the app itself.

Shared by /v1/execute, /v1/chain, the orchestrator, MCP call_tool AND the
agent runtime, so the 403 gate cannot drift across surfaces. An agent is only
as safe as the blocks it may call: the runtime refuses these blocks to a
non-admin caller, and an agent that holds one cannot be pinned, delegated to
or chatted with by a non-admin.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Iterable, Optional

from fastapi import HTTPException

PRIVILEGED_BLOCKS = frozenset({
    "code",          # runs Python / JavaScript it is handed
    "sandbox",       # same, behind a policy
    "mcp_consumer",  # starts whatever command / args it is handed
    "local_drive",   # reads, lists and WRITES the data disk -- every user's uploads
    "web",           # fetches any URL from inside the network
    "webhook",       # sends a request to any URL from inside the network
})

_caller_role: ContextVar[Optional[str]] = ContextVar("caller_role", default=None)


def set_caller_role(role: Optional[str]) -> None:
    _caller_role.set(role)


def caller_role() -> Optional[str]:
    return _caller_role.get()


def privileged_forbidden_detail(block_name: str) -> str:
    return f"Block '{block_name}' executes arbitrary code and is admin-only."


def caller_may_use_block(block_name: Optional[str], role: Optional[str] = None) -> bool:
    """False for a privileged block unless the caller is an admin. An unknown
    caller is NOT an admin: this fails closed."""
    if not block_name or block_name not in PRIVILEGED_BLOCKS:
        return True
    effective = role if role is not None else _caller_role.get()
    return effective == "admin"


def raise_if_privileged_block(block_name: Optional[str], role: Optional[str] = None) -> None:
    if not caller_may_use_block(block_name, role):
        raise HTTPException(403, privileged_forbidden_detail(block_name))


def agent_is_privileged(agent: Any) -> bool:
    """True when any block the agent may call is admin-only."""
    return any(b in PRIVILEGED_BLOCKS for b in (getattr(agent, "allowed_blocks", None) or ()))


def caller_may_use_agent(agent: Any, role: Optional[str] = None) -> bool:
    if not agent_is_privileged(agent):
        return True
    effective = role if role is not None else _caller_role.get()
    return effective == "admin"


def agent_forbidden_detail(agent_name: str) -> str:
    return f"Agent '{agent_name}' is not available."


def raise_if_privileged_steps(steps: Optional[Iterable[Any]], role: Optional[str] = None) -> None:
    for step in steps or []:
        if isinstance(step, dict):
            name = step.get("block")
        else:
            name = getattr(step, "block", None)
        raise_if_privileged_block(name, role)
