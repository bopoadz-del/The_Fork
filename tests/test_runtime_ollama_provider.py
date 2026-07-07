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
import json
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


# ── native /api/chat protocol support ──────────────────────────────────────────

from app.agents.runtime import (
    _is_native_ollama,
    _native_ollama_payload,
    _ollama_message_to_openai,
)


def test_ollama_native_url_preserved(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "https://ollama.com/api/chat")
    cfg = _llm_config()
    assert cfg["provider"] == "ollama"
    assert cfg["url"] == "https://ollama.com/api/chat"
    assert _is_native_ollama(cfg)


def test_native_ollama_payload_shape():
    payload = _native_ollama_payload(
        model="glm-5.2:cloud",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=2048,
        tools=[{"type": "function", "function": {"name": "search_project_documents"}}],
        stream=False,
    )
    assert payload["model"] == "glm-5.2:cloud"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0.7
    assert payload["options"]["num_predict"] == 2048
    assert payload["tools"][0]["function"]["name"] == "search_project_documents"


def test_ollama_message_to_openai_with_tool_calls():
    native = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "search_project_documents",
                    "arguments": {"query": "contract value", "top_k": 5},
                }
            }
        ],
    }
    openai_msg = _ollama_message_to_openai(native)
    assert openai_msg["role"] == "assistant"
    assert openai_msg["content"] == ""
    assert len(openai_msg["tool_calls"]) == 1
    assert openai_msg["tool_calls"][0]["function"]["name"] == "search_project_documents"
    # arguments must be serialised to JSON string for the rest of the runtime.
    args = json.loads(openai_msg["tool_calls"][0]["function"]["arguments"])
    assert args["query"] == "contract value"


def test_call_llm_uses_native_ollama_protocol(monkeypatch):
    """When OLLAMA_URL ends in /api/chat, _call_llm must POST a native Ollama
    payload (model/messages/stream/options/tools) and convert the native
    response back into an OpenAI-shaped choice."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "https://ollama.com/api/chat")
    monkeypatch.setenv("OLLAMA_API_KEY", "fake-token")

    agent = _agent()
    fake_client = MagicMock()
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "model": "glm-5.2:cloud",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "search_project_documents",
                        "arguments": {"query": "boq", "top_k": 5},
                    }
                }
            ],
        },
        "done": True,
    }
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.agents.runtime.httpx.AsyncClient", return_value=fake_client):
        resp = _run(agent._call_llm([{"role": "user", "content": "find boq"}], api_key="fake-token"))

    assert resp["status"] == "success"
    fake_client.post.assert_awaited_once()
    args, kwargs = fake_client.post.call_args
    assert args[0] == "https://ollama.com/api/chat"
    sent = kwargs["json"]
    assert sent["stream"] is False
    assert "options" in sent
    assert sent["options"]["num_predict"] == agent.max_tokens
    # tool_choice is not part of the native protocol.
    assert "tool_choice" not in sent

    msg = resp["choice"]["message"]
    assert msg["tool_calls"][0]["function"]["name"] == "search_project_documents"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"])["query"] == "boq"


def test_call_llm_native_ollama_no_tools(monkeypatch):
    """Native Ollama synthesis call (with_tools=False) must omit tools and
    return the content as the final answer."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "https://ollama.com/api/chat")

    agent = _agent()
    fake_client = MagicMock()
    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {
        "model": "glm-5.2:cloud",
        "message": {"role": "assistant", "content": "The contract value is $1M."},
        "done": True,
    }
    fake_client.post = AsyncMock(return_value=fake_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.agents.runtime.httpx.AsyncClient", return_value=fake_client):
        resp = _run(agent._call_llm([{"role": "user", "content": "value?"}], api_key="", with_tools=False))

    assert resp["status"] == "success"
    sent = fake_client.post.call_args.kwargs["json"]
    assert "tools" not in sent
    assert resp["choice"]["message"]["content"] == "The contract value is $1M."
