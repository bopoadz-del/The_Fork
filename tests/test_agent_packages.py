"""F-DRIVER Phase A: tools live in their owner's package, hats never import each
other, and a new hat is a new package with no core edit."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

from app.agents.core import tool_registry as reg

ROOT = Path(__file__).resolve().parent.parent
HATS = ROOT / "app" / "agents" / "hats"


def test_every_tool_has_one_known_owner_and_a_handler():
    reg.load()
    for owner in reg.OWNERS:
        for name in reg.tools_of(owner):
            spec = reg.get(name)
            assert spec.owner == owner and callable(spec.handler)


def test_tools_live_in_their_owners_package():
    reg.load()
    for owner in reg.OWNERS:
        for name in reg.tools_of(owner):
            mod = reg.get(name).handler.__module__
            expected = "app.agents.base.tools" if owner == "base" else f"app.agents.hats.{owner}.tools"
            assert mod == expected, (name, mod)


def test_no_hat_imports_another_hat():
    offenders = []
    for hat_dir in sorted(p for p in HATS.iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        for py in hat_dir.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                for m in mods:
                    if m.startswith("app.agents.hats.") and not m.startswith(f"app.agents.hats.{hat_dir.name}"):
                        offenders.append(f"{py.relative_to(ROOT)} imports {m}")
    assert offenders == []


def test_a_new_hat_package_registers_its_tools_with_no_core_edit(tmp_path, monkeypatch):
    """A synthetic hat dropped into app/agents/hats is discovered and its tool runs."""
    import app.agents.hats as hats_pkg

    pkg = tmp_path / "synthetic_hat_zeta"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""synthetic test hat."""\n', encoding="utf-8")
    (pkg / "tools.py").write_text(
        "from app.agents.core.tool_registry import ToolCall, tool\n\n"
        "@tool('zeta_echo', owner='qaqc')\n"
        "async def handle(call: ToolCall) -> dict:\n"
        "    return {'name': call.name, 'ok': True, 'result': {'echo': call.args.get('x')}}\n",
        encoding="utf-8")
    monkeypatch.setattr(hats_pkg, "__path__", list(hats_pkg.__path__) + [str(tmp_path)])
    monkeypatch.setattr(reg, "_loaded", False)
    try:
        assert "app.agents.hats.synthetic_hat_zeta.tools" in reg.tool_modules()
        spec = reg.get("zeta_echo")
        assert spec is not None
        import asyncio
        out = asyncio.run(spec.handler(reg.ToolCall(agent=None, name="zeta_echo", args={"x": 7})))
        assert out["result"]["echo"] == 7
    finally:
        reg._TOOLS.pop("zeta_echo", None)
        for m in [m for m in sys.modules if m.startswith("app.agents.hats.synthetic_hat_zeta")]:
            del sys.modules[m]


def test_a_tool_cannot_be_registered_twice_or_by_an_unknown_owner():
    with pytest.raises(ValueError):
        reg.tool("x", owner="marketing")

    async def a(call):
        return {}

    async def b(call):
        return {}
    reg.tool("dup_probe", owner="base")(a)
    try:
        with pytest.raises(ValueError):
            reg.tool("dup_probe", owner="base")(b)
    finally:
        reg._TOOLS.pop("dup_probe", None)


def test_the_agent_dispatches_registered_tools_through_the_registry(monkeypatch):
    """_run_tool_call answers a registered tool from its package handler."""
    import asyncio

    from app.agents import runtime as rt

    seen = {}

    async def fake(call):
        seen["call"] = call
        return {"name": call.name, "ok": True, "result": {"from": "package"}}
    spec = reg.get("remember_fact")
    monkeypatch.setitem(reg._TOOLS, "remember_fact", reg.ToolSpec("remember_fact", spec.owner, fake))
    rt.load_agents()
    agent = rt.AGENT_REGISTRY["project-assistant"]
    out = asyncio.run(agent._run_tool_call(
        {"function": {"name": "remember_fact", "arguments": '{"key": "k", "value": "v"}'}},
        project_id="synthetic-p", conversation_id="c1", user_message="note this"))
    assert out["result"] == {"from": "package"}
    call = seen["call"]
    assert call.agent is agent and call.args == {"key": "k", "value": "v"}
    assert call.project_id == "synthetic-p" and call.conversation_id == "c1"
