"""Default UI (project-assistant) gets hat kernels + SSE scores when live.

FORK_HATS_ENABLED=1 is on the live service, but project-assistant had no
``hats:`` frontmatter and the activation adapter was never called from the
default chat path. Specialty agents already bind kernels in config.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import AsyncMock, patch

from app.agents.runtime import (
    Agent,
    _apply_hat_activation,
    hat_scores_sse,
    hat_turn_system_note,
    hats_activation_enabled,
)


PLANNING_ASK = "What is the critical path for this project?"


def _collect(gen: AsyncIterator[Dict[str, Any]]) -> List[Dict[str, Any]]:
    async def _run():
        return [event async for event in gen]
    return asyncio.run(_run())


def _pa() -> Agent:
    return Agent(
        name="project-assistant",
        description="pa",
        system_prompt="you are project-assistant",
        allowed_blocks=[],
    )


def test_hat_scores_sse_absent_when_flag_off(monkeypatch):
    monkeypatch.delenv("FORK_HATS_ENABLED", raising=False)
    assert hats_activation_enabled() is False
    assert hat_scores_sse(PLANNING_ASK) is None
    assert hat_turn_system_note(PLANNING_ASK) is None


def test_hat_scores_sse_when_flag_on(monkeypatch):
    monkeypatch.setenv("FORK_HATS_ENABLED", "1")
    assert hats_activation_enabled() is True
    evt = hat_scores_sse(PLANNING_ASK)
    assert evt is not None
    assert evt["type"] == "hat_scores"
    assert evt["enabled"] is True
    assert evt["scores"]
    assert "planning" in evt["scores"]
    assert evt["selected"]
    assert evt["selected"].startswith("fork.hat.")


def test_hat_turn_note_names_the_winning_hat(monkeypatch):
    monkeypatch.setenv("FORK_HATS_ENABLED", "1")
    note = hat_turn_system_note(PLANNING_ASK)
    assert note is not None
    assert note["role"] == "system"
    assert "Active discipline hat this turn" in note["content"]
    assert "fork.hat." in note["content"]
    assert "planning=" in note["content"]


def test_apply_hat_activation_steers_project_assistant_only(monkeypatch):
    monkeypatch.setenv("FORK_HATS_ENABLED", "1")
    pa_msgs = [{"role": "user", "content": PLANNING_ASK}]
    evt = _apply_hat_activation(pa_msgs, PLANNING_ASK, "project-assistant")
    assert evt and evt["type"] == "hat_scores"
    assert pa_msgs[0]["role"] == "system"
    assert pa_msgs[-1]["role"] == "user"

    qs_msgs = [{"role": "user", "content": PLANNING_ASK}]
    _apply_hat_activation(qs_msgs, PLANNING_ASK, "quantity-surveyor")
    assert qs_msgs == [{"role": "user", "content": PLANNING_ASK}]


def test_project_assistant_stream_emits_hat_scores(monkeypatch):
    monkeypatch.setenv("FORK_HATS_ENABLED", "1")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key")
    agent = _pa()
    mock = AsyncMock(return_value={
        "status": "success",
        "choice": {"message": {"content": "Critical path runs through piling.", "tool_calls": []}},
    })
    with patch.object(agent, "_call_llm", mock):
        events = _collect(agent.chat_stream(user_message=PLANNING_ASK))
    types = [e["type"] for e in events]
    assert "start" in types
    assert "hat_scores" in types
    assert types.index("hat_scores") == types.index("start") + 1
    hat = next(e for e in events if e["type"] == "hat_scores")
    assert hat["scores"]
    assert hat["enabled"] is True
    # The turn note must reach the model on the default UI agent.
    sent = mock.await_args.args[0]
    assert any(
        m.get("role") == "system" and "Active discipline hat this turn" in (m.get("content") or "")
        for m in sent
    )


def test_project_assistant_stream_no_hat_scores_when_flag_off(monkeypatch):
    monkeypatch.delenv("FORK_HATS_ENABLED", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key")
    agent = _pa()
    mock = AsyncMock(return_value={
        "status": "success",
        "choice": {"message": {"content": "ok", "tool_calls": []}},
    })
    with patch.object(agent, "_call_llm", mock):
        events = _collect(agent.chat_stream(user_message=PLANNING_ASK))
    assert all(e["type"] != "hat_scores" for e in events)
    sent = mock.await_args.args[0]
    assert not any(
        "Active discipline hat this turn" in (m.get("content") or "")
        for m in sent
    )
