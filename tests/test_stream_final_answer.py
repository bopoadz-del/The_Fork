"""PR-A: the no-tool final answer streams (ORDERS_0110Z/0150Z), with the
display-name transform applied per-segment so no raw id leaks."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agents.runtime import Agent
from tests._fixture_values import STUB_TOKEN_04

_DEEPSEEK_CFG = {
    "provider": "deepseek",
    "url": "https://api.deepseek.com/v1/chat/completions",
    "env_key": "DEEPSEEK_API_KEY",
    "default_model": "deepseek-chat",
}


@pytest.fixture
def deepseek_streaming(monkeypatch):
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: dict(_DEEPSEEK_CFG))
    monkeypatch.setenv("DEEPSEEK_API_KEY", STUB_TOKEN_04)
    monkeypatch.setenv("SYNTHESIS_STREAMING", "1")


def _pa_agent():
    return Agent(name="project-assistant", description="pa",
                 system_prompt="x", allowed_blocks=["construction"])


def _drain(agen):
    async def go():
        return [ev async for ev in agen]
    return asyncio.run(go())


def _run_turn(agent):
    return _drain(agent.chat_stream(
        user_message="What is the defects liability period?",
        history=[], project_id=None, conversation_id=None, user_id=None,
    ))


def _tokens(events):
    return "".join(e.get("content", "") for e in events if e.get("type") == "token")


def _final_no_tools(text):
    async def fake(_self, messages, api_key, **kwargs):
        return {"status": "success", "choice": {"message": {
            "role": "assistant", "content": text}}}
    return fake


def _mk_stream(deltas):
    async def fake_stream(self, messages, api_key, **kwargs):
        for d in deltas:
            yield d
    return fake_stream


def _stream_must_not_run():
    async def fake(self, *a, **k):
        raise AssertionError("streaming path taken while SYNTHESIS_STREAMING=0")
        yield ""  # pragma: no cover
    return fake


def test_no_tool_final_answer_streams_not_chunked(deepseek_streaming):
    lump = "The non streamed lump answer is here and complete."
    deltas = ["The streamed answer\n", "arrives in several\n", "pieces as produced.\n"]
    with patch.object(Agent, "_call_llm", _final_no_tools(lump)), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(deltas)):
        events = _run_turn(_pa_agent())
    text = _tokens(events)
    assert "streamed answer" in text and "as produced" in text
    assert "lump" not in text
    types = [e.get("type") for e in events]
    assert types.count("token") >= 2
    assert "end" in types and types.index("token") < types.index("end")


def test_kill_switch_keeps_the_non_streamed_path(monkeypatch):
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: dict(_DEEPSEEK_CFG))
    monkeypatch.setenv("DEEPSEEK_API_KEY", STUB_TOKEN_04)
    monkeypatch.setenv("SYNTHESIS_STREAMING", "0")
    lump = "The whole answer computed up front and then chunked."
    with patch.object(Agent, "_call_llm", _final_no_tools(lump)), \
         patch.object(Agent, "_stream_synthesis", _stream_must_not_run()):
        events = _run_turn(_pa_agent())
    assert "whole answer computed up front" in _tokens(events)


def test_streamed_final_answer_converts_a_split_identifier(deepseek_streaming):
    """An id split across two provider deltas must reach the user as the display
    name, never raw -- DisplayNameStreamer on the streamed path."""
    from app.lib.source_labels import formula_display_name
    shown = formula_display_name("delay_damages_daily")
    if not shown:
        pytest.skip("registry has no display name for the probe id")
    deltas = ["The rate is delay_", "damages_daily per the contract.\n"]
    with patch.object(Agent, "_call_llm", _final_no_tools("lump")), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(deltas)):
        events = _run_turn(_pa_agent())
    text = _tokens(events)
    assert "delay_damages_daily" not in text
    assert shown in text
