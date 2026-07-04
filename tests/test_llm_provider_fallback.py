"""Provider fallback: a Groq-primary deploy degrades to Ollama/gpt-oss.

When ``LLM_FALLBACK_PROVIDER`` is set, ``_call_llm`` retries the request on a
second provider on a RETRYABLE failure (413 request-too-large, 429 rate-limit,
5xx, network/timeout) — but NOT on auth/validation errors (400/401/403/404),
where a different provider can't help. This is the resilience that makes a
Groq-primary switch safe: the operator's "keep gpt-oss as a fallback to Groq".
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.agents.runtime import Agent, _llm_config, _llm_fallback_config


def _agent():
    return Agent(
        name="fallback-test",
        description="provider fallback test",
        system_prompt="x",
        allowed_blocks=[],
    )


def _run(coro):
    return asyncio.run(coro)


def _resp(status, *, json_body=None, text=""):
    r = MagicMock(status_code=status)
    r.text = text
    r.json.return_value = json_body or {}
    return r


def _client_with(responses):
    """An httpx.AsyncClient stub whose .post yields ``responses`` in order."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=responses)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _groq_primary_ollama_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gk-test")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://fallback.tunnel.cf")


# ── _llm_fallback_config ────────────────────────────────────────────────────


def test_fallback_config_none_when_unset(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    assert _llm_fallback_config({"provider": "groq"}) is None


def test_fallback_config_none_when_same_as_primary(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "groq")
    assert _llm_fallback_config({"provider": "groq"}) is None


def test_fallback_config_none_when_key_missing(monkeypatch):
    # Fallback=groq but no GROQ_API_KEY → unusable, so None.
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert _llm_fallback_config({"provider": "ollama"}) is None


def test_fallback_config_resolves_ollama(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://fb.tunnel.cf")
    cfg = _llm_fallback_config({"provider": "groq"})
    assert cfg is not None
    assert cfg["provider"] == "ollama"
    assert cfg["url"] == "http://fb.tunnel.cf/v1/chat/completions"


# ── _call_llm: retryable failure falls back ─────────────────────────────────


def test_413_falls_back_to_second_provider(monkeypatch):
    _groq_primary_ollama_fallback(monkeypatch)
    agent = _agent()
    client = _client_with([
        _resp(413, text="request too large: TPM limit 30000"),
        _resp(200, json_body={"choices": [{"message": {"content": "ok from ollama"}}], "model": "gpt-oss"}),
    ])
    with patch("app.agents.runtime.httpx.AsyncClient", return_value=client):
        resp = _run(agent._call_llm([], api_key="gk-test", with_tools=False))

    assert resp["status"] == "success"
    assert resp["choice"]["message"]["content"] == "ok from ollama"
    assert client.post.await_count == 2
    # Second attempt went to the fallback (Ollama) URL.
    second_url = client.post.await_args_list[1].args[0]
    assert second_url == "http://fallback.tunnel.cf/v1/chat/completions"


def test_429_falls_back(monkeypatch):
    _groq_primary_ollama_fallback(monkeypatch)
    agent = _agent()
    client = _client_with([
        _resp(429, text="rate limited"),
        _resp(200, json_body={"choices": [{"message": {"content": "recovered"}}]}),
    ])
    with patch("app.agents.runtime.httpx.AsyncClient", return_value=client):
        resp = _run(agent._call_llm([], api_key="gk-test", with_tools=False))
    assert resp["status"] == "success"
    assert client.post.await_count == 2


def test_network_error_falls_back(monkeypatch):
    _groq_primary_ollama_fallback(monkeypatch)
    import httpx
    agent = _agent()
    client = _client_with([
        httpx.ConnectError("connection refused"),
        _resp(200, json_body={"choices": [{"message": {"content": "recovered"}}]}),
    ])
    with patch("app.agents.runtime.httpx.AsyncClient", return_value=client):
        resp = _run(agent._call_llm([], api_key="gk-test", with_tools=False))
    assert resp["status"] == "success"
    assert client.post.await_count == 2


# ── _call_llm: non-retryable failure does NOT fall back ─────────────────────


def test_401_does_not_fall_back(monkeypatch):
    """Auth errors are the caller's config problem — a second provider can't
    fix them, so we must NOT waste a fallback call; return the error."""
    _groq_primary_ollama_fallback(monkeypatch)
    agent = _agent()
    client = _client_with([
        _resp(401, text="invalid api key"),
        _resp(200, json_body={"choices": [{"message": {"content": "should NOT reach here"}}]}),
    ])
    with patch("app.agents.runtime.httpx.AsyncClient", return_value=client):
        resp = _run(agent._call_llm([], api_key="gk-test", with_tools=False))

    assert resp["status"] == "error"
    assert "401" in resp["error"]
    assert client.post.await_count == 1  # no fallback attempt


# ── _call_llm: no fallback configured → single attempt ──────────────────────


def test_no_fallback_single_attempt(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gk-test")
    monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
    agent = _agent()
    client = _client_with([_resp(413, text="request too large")])
    with patch("app.agents.runtime.httpx.AsyncClient", return_value=client):
        resp = _run(agent._call_llm([], api_key="gk-test", with_tools=False))

    assert resp["status"] == "error"
    assert "413" in resp["error"]
    assert client.post.await_count == 1


def test_primary_success_no_fallback_call(monkeypatch):
    """When the primary succeeds, the fallback is never touched."""
    _groq_primary_ollama_fallback(monkeypatch)
    agent = _agent()
    client = _client_with([
        _resp(200, json_body={"choices": [{"message": {"content": "primary ok"}}], "model": "llama-4-scout"}),
        _resp(200, json_body={"choices": [{"message": {"content": "SHOULD NOT REACH"}}]}),
    ])
    with patch("app.agents.runtime.httpx.AsyncClient", return_value=client):
        resp = _run(agent._call_llm([], api_key="gk-test", with_tools=False))

    assert resp["status"] == "success"
    assert resp["choice"]["message"]["content"] == "primary ok"
    assert client.post.await_count == 1
