"""The agent runtime supports OpenRouter as a first-class provider.

When ``LLM_PROVIDER=openrouter`` is set, the runtime:
- routes to OpenRouter's OpenAI-compatible chat-completions endpoint
- authenticates with ``OPENROUTER_API_KEY``
- defaults to ``openrouter/free`` (override with ``OPENROUTER_MODEL``)
- refuses paid slugs unless ``OPENROUTER_ALLOW_PAID=1``

No live OpenRouter calls. Keys in these tests are placeholders.
"""
from __future__ import annotations

from app.agents.runtime import (
    OPENROUTER_API_URL,
    OPENROUTER_DEFAULT_MODEL,
    _llm_config,
    _resolve_openrouter_model,
)


def test_llm_config_picks_openrouter_when_explicit(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "openrouter"
    assert cfg["url"] == OPENROUTER_API_URL
    assert cfg["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert cfg["env_key"] == "OPENROUTER_API_KEY"
    assert cfg["default_model"] == OPENROUTER_DEFAULT_MODEL
    assert cfg["default_model"] == "openrouter/free"


def test_openrouter_model_override_free_slug(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    assert _llm_config()["default_model"] == "meta-llama/llama-3.3-70b-instruct:free"


def test_openrouter_missing_key_still_returns_config(monkeypatch):
    """Callers check env_key themselves; missing key must not drop the branch."""
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "openrouter"
    assert cfg["env_key"] == "OPENROUTER_API_KEY"
    assert cfg["url"] == OPENROUTER_API_URL


def test_openrouter_refuses_paid_slug_unless_allowlisted(monkeypatch):
    monkeypatch.delenv("OPENROUTER_ALLOW_PAID", raising=False)
    assert (
        _resolve_openrouter_model("anthropic/claude-3.5-sonnet")
        == OPENROUTER_DEFAULT_MODEL
    )
    monkeypatch.setenv("OPENROUTER_ALLOW_PAID", "1")
    assert (
        _resolve_openrouter_model("anthropic/claude-3.5-sonnet")
        == "anthropic/claude-3.5-sonnet"
    )


def test_openrouter_paid_slug_in_config_falls_back_to_free_router(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o")
    monkeypatch.delenv("OPENROUTER_ALLOW_PAID", raising=False)
    assert _llm_config()["default_model"] == "openrouter/free"


def test_openrouter_has_no_fixed_temperature(monkeypatch):
    """OpenRouter is Groq-shaped: the agent keeps its own temperature."""
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    assert "fixed_temperature" not in _llm_config()
