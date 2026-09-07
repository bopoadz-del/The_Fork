"""Leftover E4 must not hang on a streamed 'Let me validate…' promise.

#492 already pins construction_calc → 945. Live leftover E4 on ``907f6cd``
(cold New-chat) streamed ``Let me validate…`` and never emitted 945: the
search-preamble detector did not treat ``validate`` as a dangling promise,
and the forced no-tools retry was another LLM hop that never finished.

These pin both halves: the promise is never flushed, and compose from the
ask's own L×W×T skips the retry. No live LLM. Leftover L6 / L4 / E1
wording is the fence.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agents.runtime import Agent
from tests.test_e4_waste_factor import E4_ASK_ASCII, L6_ASK

_GROQ_CFG = {
    "provider": "groq",
    "url": "https://api.groq.com/openai/v1/chat/completions",
    "env_key": "GROQ_API_KEY",
    "default_model": "llama-3.3-70b-versatile",
}

LIVE_VALIDATE = "Let me validate…"
HANG_RETRY = "SHOULD NOT BE USED — validate retry hung"


@pytest.fixture(autouse=True)
def _no_predispatch(monkeypatch):
    monkeypatch.setenv("AGENT_COMMISSIONING_PREDISPATCH", "0")


@pytest.fixture
def groq_streaming(monkeypatch):
    monkeypatch.setattr("app.agents.runtime._llm_config", lambda: dict(_GROQ_CFG))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("SYNTHESIS_STREAMING", "1")
    monkeypatch.setenv("CHAT_STREAM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("CHAT_STREAM_HEARTBEAT_SECONDS", "20")
    monkeypatch.setenv("FORCED_RETRY_MIN_SECONDS", "1")


def _pm_agent():
    return Agent(
        name="construction-pm",
        description="pm",
        system_prompt="x",
        allowed_blocks=["construction"],
    )


def _drain(agen):
    async def go():
        return [ev async for ev in agen]
    return asyncio.run(go())


def _tool_then(retry_text):
    """First call -> deliverable tool_call; every later call -> `retry_text`."""
    state = {"n": 0, "with_tools": []}

    async def fake(_self, messages, api_key, **kwargs):
        state["n"] += 1
        state["with_tools"].append(kwargs.get("with_tools", True))
        if state["n"] == 1:
            return {"status": "success", "choice": {"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "commissioning_checklist",
                    "arguments": '{"systems":["electrical"]}'}}]}}}
        return {"status": "success", "choice": {"message": {
            "role": "assistant", "content": retry_text}}}

    fake.state = state
    return fake


async def _tool_ok(self, tool_call, **kwargs):
    return {
        "name": "commissioning_checklist",
        "ok": True,
        "result": {"status": "success", "checklists_by_system": {"electrical": []}},
    }


def _mk_stream(text):
    async def fake_stream(self, messages, api_key, **kwargs):
        if text:
            yield text
    return fake_stream


def _run(ask, streamed, retry_text):
    call_llm = _tool_then(retry_text)
    with patch.object(Agent, "_call_llm", call_llm), \
         patch.object(Agent, "_run_tool_call", _tool_ok), \
         patch.object(Agent, "_stream_synthesis", _mk_stream(streamed)):
        events = _drain(_pm_agent().chat_stream(
            user_message=ask,
            history=[], project_id=None, conversation_id=None, user_id=None,
        ))
    return events, call_llm.state


def _tokens(events):
    return "".join(e.get("content", "") for e in events if e.get("type") == "token")


def test_validate_promise_is_never_flushed_and_945_is_emitted(groq_streaming):
    """Live leftover E4. User must see 945, never the validate spinner line."""
    events, state = _run(E4_ASK_ASCII, LIVE_VALIDATE, HANG_RETRY)
    shown = _tokens(events)
    assert "Let me validate" not in shown, shown
    assert "945" in shown
    assert "waste" in shown.lower()
    assert HANG_RETRY not in shown
    assert state["n"] == 1, (
        f"E4 compose must skip the forced LLM retry; n={state['n']}"
    )


def test_validate_sentence_form_also_composes(groq_streaming):
    events, state = _run(
        E4_ASK_ASCII,
        "Let me validate the waste factor against the pipeline.",
        HANG_RETRY,
    )
    shown = _tokens(events)
    assert "945" in shown
    assert "Let me validate" not in shown
    assert state["n"] == 1


def test_a_real_945_stream_is_untouched(groq_streaming):
    already = (
        "Concrete volume including the documented 5% waste factor "
        "is 945 m³ (net 900 m³ × 1.05)."
    )
    events, state = _run(E4_ASK_ASCII, already, HANG_RETRY)
    shown = _tokens(events)
    assert "945" in shown
    assert HANG_RETRY not in shown
    assert state["n"] == 1


def test_leftover_l6_validate_promise_is_not_stolen_as_e4(groq_streaming):
    """Fence: earthwork L6 must not pick up the raft 945 compose."""
    events, state = _run(L6_ASK, LIVE_VALIDATE, "Bank volume is 81.2 m³.")
    shown = _tokens(events)
    assert "945" not in shown
    assert "81.2" in shown
    assert state["n"] >= 2
