"""The agent runtime supports a self-hosted Ollama backend.

When ``LLM_PROVIDER=ollama`` is set, the runtime:
- routes to ``OLLAMA_URL`` (defaults to ``http://localhost:11434/...``)
- skips the env-key check entirely (Ollama has no auth)
- normalises the URL to end at ``/v1/chat/completions`` whether the
  operator passes a bare host, a ``/v1`` suffix, or the full path

This lets the operator point the platform at their own Ollama instance
(e.g. via Cloudflare Tunnel) and escape Groq's TPM rate-limit entirely.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.agents.runtime import Agent, _llm_config


def _agent():
    return Agent(
        name="ollama-test",
        description="ollama provider test",
        system_prompt="x",
        allowed_blocks=[],
    )


def _run(coro):
    return asyncio.run(coro)


# ── _llm_config ──────────────────────────────────────────────────────────────


def test_llm_config_picks_ollama_when_explicit(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://my-pc.tunnel.cf")
    cfg = _llm_config()
    assert cfg["provider"] == "ollama"
    assert cfg["env_key"] == ""
    assert cfg["default_model"]  # has a sensible default
    # Bare host should be normalised to the full OAI path.
    assert cfg["url"] == "http://my-pc.tunnel.cf/v1/chat/completions"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://localhost:11434", "http://localhost:11434/v1/chat/completions"),
        ("http://localhost:11434/", "http://localhost:11434/v1/chat/completions"),
        ("http://localhost:11434/v1", "http://localhost:11434/v1/chat/completions"),
        ("http://localhost:11434/v1/chat/completions", "http://localhost:11434/v1/chat/completions"),
        ("https://ollama.example.com", "https://ollama.example.com/v1/chat/completions"),
    ],
)
def test_ollama_url_normalisation(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", raw)
    cfg = _llm_config()
    assert cfg["url"] == expected


def test_ollama_model_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.3:70b-instruct-q4_K_M")
    cfg = _llm_config()
    assert cfg["default_model"] == "llama3.3:70b-instruct-q4_K_M"


# ── _call_llm: ollama skips the auth check ───────────────────────────────────


def test_call_llm_no_auth_required_for_ollama(monkeypatch):
    """With LLM_PROVIDER=ollama set and no api_key, _call_llm must NOT
    return the 'No GROQ_API_KEY configured' error path. It should
    proceed to make the HTTP call with an empty bearer."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_URL", "http://my-pc.tunnel.cf")

    agent = _agent()
    fake_client = MagicMock()
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "hello from ollama"}}],
        "model": "qwen2.5:7b-instruct",
    }
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.agents.runtime.httpx.AsyncClient", return_value=fake_client):
        resp = _run(agent._call_llm([], api_key=""))

    assert resp["status"] == "success"
    fake_client.post.assert_awaited_once()
    # The post call should have gone to the normalised Ollama URL.
    args, kwargs = fake_client.post.call_args
    assert args[0] == "http://my-pc.tunnel.cf/v1/chat/completions"


def test_groq_still_requires_api_key(monkeypatch):
    """Sanity: removing GROQ_API_KEY when LLM_PROVIDER=groq still errors,
    confirming the ollama skip is provider-scoped, not global."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    agent = _agent()
    # chat() returns the no-key error before any HTTP call. Use a stub
    # so an accidental network call would be visible as a failure.
    with patch("app.agents.runtime.httpx.AsyncClient") as mc:
        resp = _run(
            agent.chat(
                user_message="hi",
                history=[],
                project_id=None,
                conversation_id=None,
            )
        )
        mc.assert_not_called()

    assert resp["status"] == "error"
    assert "GROQ_API_KEY" in resp["error"]
