"""OpenRouter is a first-class OpenAI-compatible LLM provider.

When ``LLM_PROVIDER=openrouter`` is set, the runtime:
- routes to https://openrouter.ai/api/v1/chat/completions
- authenticates with ``OPENROUTER_API_KEY``
- defaults to ``openrouter/free`` (override with ``OPENROUTER_MODEL``)

Config-only — no live HTTP, no invented production keys. Presence tokens
used to exercise auto-pick / fallback-key gates are the same style as the
kimi/groq suites (``x``), never a real secret.
"""
from __future__ import annotations

from app.agents.runtime import (
    OPENROUTER_API_URL,
    OPENROUTER_DEFAULT_MODEL,
    _llm_config,
    _llm_fallback_config,
    _provider_temperature,
)

_PRESENCE = "x"


def test_llm_config_picks_openrouter_when_explicit(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "openrouter"
    assert cfg["url"] == OPENROUTER_API_URL
    assert cfg["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert cfg["env_key"] == "OPENROUTER_API_KEY"
    assert cfg["default_model"] == "openrouter/free"
    assert cfg["default_model"] == OPENROUTER_DEFAULT_MODEL
    assert "fixed_temperature" not in cfg


def test_openrouter_model_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    assert _llm_config()["default_model"] == "meta-llama/llama-3.3-70b-instruct:free"


def test_openrouter_is_unconstrained_temperature(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    cfg = _llm_config()
    assert _provider_temperature(cfg, 0.3) == 0.3


def test_unset_provider_prefers_openrouter_when_key_present(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", _PRESENCE)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert _llm_config()["provider"] == "openrouter"


def test_unset_provider_without_keys_is_openrouter(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "openrouter"
    assert cfg["env_key"] == "OPENROUTER_API_KEY"


def test_unrecognised_provider_resolves_to_openrouter(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    assert _llm_config()["provider"] == "openrouter"


def test_explicit_kimi_still_wins_over_openrouter_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("OPENROUTER_API_KEY", _PRESENCE)
    assert _llm_config()["provider"] == "kimi"


def test_fallback_config_resolves_openrouter(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", _PRESENCE)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    cfg = _llm_fallback_config({"provider": "kimi"})
    assert cfg is not None
    assert cfg["provider"] == "openrouter"
    assert cfg["url"] == OPENROUTER_API_URL
    assert cfg["env_key"] == "OPENROUTER_API_KEY"
    assert cfg["default_model"] == "openrouter/free"


def test_fallback_config_openrouter_none_without_key(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert _llm_fallback_config({"provider": "kimi"}) is None


def test_fallback_config_openrouter_none_when_it_names_the_primary(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", _PRESENCE)
    assert _llm_fallback_config({"provider": "openrouter"}) is None
