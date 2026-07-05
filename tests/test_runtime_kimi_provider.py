"""The agent runtime supports Kimi (Moonshot) as a first-class provider.

When ``LLM_PROVIDER=kimi`` is set, the runtime:
- routes to the Moonshot native OpenAI-compatible endpoint
- authenticates with ``KIMI_API_KEY``
- defaults to ``kimi-k2.6`` (override with ``KIMI_MODEL``)

K2 is a *reasoning* model, so a specific/forced ``tool_choice`` is rejected by
Moonshot ("tool_choice 'specified' is incompatible with thinking enabled").
The runtime must therefore use ``tool_choice="auto"`` for kimi — same as it does
for Groq (where forcing made Scout emit the tool as prose -> 400). These tests
lock both behaviours in.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from app.agents.runtime import Agent, _llm_config


def _run(coro):
    return asyncio.run(coro)


def _pa_agent():
    # project-assistant carries the forced-tool logic that must NOT fire on kimi
    return Agent(name="project-assistant", description="pa",
                 system_prompt="x", allowed_blocks=["construction"])


# ── _llm_config ──────────────────────────────────────────────────────────────

def test_llm_config_picks_kimi_when_explicit(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.delenv("KIMI_MODEL", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert cfg["env_key"] == "KIMI_API_KEY"
    assert cfg["default_model"] == "kimi-k2.6"


def test_kimi_model_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_MODEL", "kimi-k2.5")
    assert _llm_config()["default_model"] == "kimi-k2.5"


# ── tool_choice must be "auto" for kimi (never forced) ───────────────────────

def test_kimi_tool_choice_is_auto_not_forced(monkeypatch):
    """A deliverable message on project-assistant would force a specific tool on
    a non-groq/non-kimi provider. On kimi it must post tool_choice='auto', or
    Moonshot 400s (forcing is incompatible with the thinking model)."""
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "test-key")

    agent = _pa_agent()
    fake_client = MagicMock()
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}],
        "model": "kimi-k2.6",
    }
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    # A clear deliverable message so _forced_specific_tool / requires_tool would
    # otherwise kick in.
    messages = [{"role": "user", "content": "Generate a commissioning checklist for the MV substation."}]
    with patch("app.agents.runtime.httpx.AsyncClient", return_value=fake_client):
        resp = _run(agent._call_llm(messages, api_key="test-key", project_id="p", with_tools=True))

    assert resp["status"] == "success"
    _, kwargs = fake_client.post.call_args
    payload = kwargs["json"]
    # Tools were offered, and the choice must be the plain string "auto".
    if "tools" in payload:
        assert payload.get("tool_choice") == "auto", payload.get("tool_choice")
