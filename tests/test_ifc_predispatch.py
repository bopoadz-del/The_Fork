"""Deterministic file pre-dispatch (IFC census gate).

Three live rounds on the same request produced three failure shapes --
refusal (F36), narrated intention (F27), and code-generation returning
zeros -- because K2 rejects forced tool_choice and the model kept
choosing wrong. The runtime now runs the matching file tool ITSELF when
the request literally names a project file of a pre-dispatch type and
the agent holds the tool, injecting the result before the model answers.
Kill-switch: AGENT_FILE_PREDISPATCH=0.
"""
from __future__ import annotations

import pytest

from app.agents import runtime


class _FakeExtractor:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def execute(self, input_data, params):
        self.calls.append(input_data)
        return self._result


def _wire(monkeypatch, docs, result, tool="bim_extractor"):
    import app.core.projects as projects_mod
    monkeypatch.setattr(projects_mod, "list_documents", lambda pid: docs,
                        raising=False)
    fake = _FakeExtractor(result)
    monkeypatch.setattr(runtime, "block_instances", {tool: fake}, raising=False)
    monkeypatch.setattr(
        runtime, "_resolve_file_path",
        lambda pid, raw: "/app/data/e14_" + raw)
    return fake


def _agent(blocks=("bim_extractor", "construction")):
    return runtime.Agent(name="qa", description="", system_prompt="x",
                         allowed_blocks=list(blocks))


@pytest.mark.asyncio
async def test_named_ifc_is_predispatched_and_injected(monkeypatch):
    fake = _wire(monkeypatch, [{"original_name": "qa_building.ifc"}],
                 {"status": "success", "building_elements": [
                     {"ifc_type": "IfcWall"}] * 4})
    msgs = [{"role": "user",
             "content": "Element census of qa_building.ifc: how many walls?"}]
    rec = await runtime._predispatch_file_tool(_agent(), msgs, "p1")
    assert rec and rec["predispatched"] and rec["name"] == "bim_extractor"
    # the tool received the RESOLVED stored path, not the bare name
    assert fake.calls == [{"file_path": "/app/data/e14_qa_building.ifc"}]
    # and the result is folded into the turn as authoritative context
    assert "PLATFORM PRE-DISPATCH" in msgs[-1]["content"]
    assert "IfcWall" in msgs[-1]["content"]


@pytest.mark.asyncio
async def test_no_predispatch_when_file_not_named_or_tool_missing(monkeypatch):
    _wire(monkeypatch, [{"original_name": "qa_building.ifc"}],
          {"status": "success"})
    msgs = [{"role": "user", "content": "what is the retention percentage?"}]
    assert await runtime._predispatch_file_tool(_agent(), msgs, "p1") is None
    msgs = [{"role": "user", "content": "census of qa_building.ifc"}]
    assert await runtime._predispatch_file_tool(
        _agent(blocks=("boq_processor",)), msgs, "p1") is None
    assert await runtime._predispatch_file_tool(_agent(), msgs, None) is None
    assert len(msgs) == 1  # nothing injected on any silent path


@pytest.mark.asyncio
async def test_predispatch_failure_falls_through_to_the_normal_loop(monkeypatch):
    _wire(monkeypatch, [{"original_name": "qa_building.ifc"}],
          {"status": "error", "error": "boom"})
    msgs = [{"role": "user", "content": "census of qa_building.ifc"}]
    assert await runtime._predispatch_file_tool(_agent(), msgs, "p1") is None
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_kill_switch_disables_predispatch(monkeypatch):
    _wire(monkeypatch, [{"original_name": "qa_building.ifc"}],
          {"status": "success"})
    monkeypatch.setenv("AGENT_FILE_PREDISPATCH", "0")
    msgs = [{"role": "user", "content": "census of qa_building.ifc"}]
    assert await runtime._predispatch_file_tool(_agent(), msgs, "p1") is None
