"""Tools register themselves where they are defined, under the package that owns them.

A tool is ``@tool("name", owner="base" | "<hat>")`` on an
``async def handle(call: ToolCall) -> dict`` in its owner's package
(``app.agents.base`` or ``app.agents.hats.<hat>``). The agent's tool loop
looks the name up here; a hat's manifest lists exactly the tools its package
registers. Adding a hat is a new package -- no core edit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

OWNERS = ("base", "commercial", "contracts", "design", "planning", "procurement",
          "qaqc", "quantities", "safety")


@dataclass
class ToolCall:
    """Everything a tool handler may read about the call it is answering."""
    agent: Any
    name: str
    args: Dict[str, Any]
    tool_call: Dict[str, Any] = field(default_factory=dict)
    api_key: Optional[str] = None
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None
    depth: int = 0
    call_stack: List[str] = field(default_factory=list)
    user_message: Optional[str] = None
    history: Optional[list] = None


Handler = Callable[[ToolCall], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    owner: str
    handler: Handler


_TOOLS: Dict[str, ToolSpec] = {}


def tool(name: str, *, owner: str) -> Callable[[Handler], Handler]:
    if owner not in OWNERS:
        raise ValueError(f"unknown tool owner {owner!r}; one of {OWNERS}")

    def register(fn: Handler) -> Handler:
        existing = _TOOLS.get(name)
        if existing is not None and existing.handler is not fn:
            raise ValueError(f"tool {name!r} is registered twice")
        _TOOLS[name] = ToolSpec(name, owner, fn)
        return fn
    return register


def get(name: str) -> Optional[ToolSpec]:
    load()
    return _TOOLS.get(name)


def tools_of(owner: str) -> List[str]:
    load()
    return sorted(n for n, s in _TOOLS.items() if s.owner == owner)


_loaded = False


def tool_modules() -> List[str]:
    """``app.agents.base.tools`` and every ``app.agents.hats.<hat>.tools`` that
    exists -- discovered, so a new hat is a new package and no core edit."""
    import importlib.util
    import pkgutil

    import app.agents.hats as hats

    mods = ["app.agents.base.tools"]
    for info in sorted(pkgutil.iter_modules(hats.__path__), key=lambda i: i.name):
        if info.ispkg:
            try:
                spec = importlib.util.find_spec(f"app.agents.hats.{info.name}.tools")
            except ModuleNotFoundError:
                spec = None
            if spec is not None:
                mods.append(f"app.agents.hats.{info.name}.tools")
    return mods


def load() -> None:
    global _loaded
    if _loaded:
        return
    import importlib

    for mod in tool_modules():
        importlib.import_module(mod)
    _loaded = True
