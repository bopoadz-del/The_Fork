"""The agent runtime supports OpenRouter as a first-class provider.

When ``LLM_PROVIDER=openrouter`` is set, the runtime:
- routes to OpenRouter's OpenAI-compatible chat-completions endpoint
- authenticates with ``OPENROUTER_API_KEY``
- defaults to ``openrouter/free`` (override with ``OPENROUTER_MODEL``)
- refuses paid slugs unless ``OPENROUTER_ALLOW_PAID=1``
- caps outbound ``max_tokens`` (``OPENROUTER_MAX_TOKENS``, default 2048)
  so a $0-balance free account is not 402'd by agent YAML of 8192

No live OpenRouter calls. Keys in these tests are placeholders.
"""
from __future__ import annotations

from app.agents.runtime import (
    OPENROUTER_API_URL,
    OPENROUTER_DEFAULT_MAX_TOKENS,
    OPENROUTER_DEFAULT_MODEL,
    _llm_config,
    _provider_max_tokens,
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


def test_openrouter_caps_agent_max_tokens_to_default(monkeypatch):
    """Agent YAML of 8192 402s a $0 OpenRouter balance; clamp to the default."""
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_MAX_TOKENS", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "openrouter"
    assert _provider_max_tokens(cfg, 8192) == OPENROUTER_DEFAULT_MAX_TOKENS
    assert _provider_max_tokens(cfg, 8192) == 2048
    # A smaller agent budget is not lifted.
    assert _provider_max_tokens(cfg, 1024) == 1024


def test_openrouter_max_tokens_env_override(monkeypatch):
    """OPENROUTER_MAX_TOKENS wins over the default ceiling."""
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "2000")
    cfg = _llm_config()
    assert _provider_max_tokens(cfg, 8192) == 2000
    assert _provider_max_tokens({"provider": "openrouter"}, 8192) == 2000


def test_openrouter_max_tokens_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "not-a-number")
    assert _provider_max_tokens({"provider": "openrouter"}, 8192) == 2048
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "0")
    assert _provider_max_tokens({"provider": "openrouter"}, 8192) == 2048


def test_openrouter_ceiling_does_not_touch_other_providers(monkeypatch):
    """Kimi still wants high caps; the ceiling is OpenRouter-only."""
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "2000")
    assert _provider_max_tokens({"provider": "kimi"}, 8192) == 8192
    assert _provider_max_tokens({"provider": "groq"}, 8192) == 8192
    assert _provider_max_tokens({"provider": "kimi", "reasoning_min_tokens": 4096}, 8192) == 8192
