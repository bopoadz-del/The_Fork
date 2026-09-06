"""The agent runtime supports DeepSeek as a first-class cloud provider.

When ``LLM_PROVIDER=deepseek`` is set, the runtime:
- routes to DeepSeek's native OpenAI-compatible chat-completions endpoint
- authenticates with ``DEEPSEEK_API_KEY``
- defaults to ``deepseek-chat`` (override with ``DEEPSEEK_MODEL``)
- accepts ``deepseek-reasoner`` (and any other catalogue id) when set
- does NOT pin ``fixed_temperature`` (unlike Moonshot K2)

DeepSeek is explicit-only: a bare ``DEEPSEEK_API_KEY`` does not steal the
unset / unrecognised ``LLM_PROVIDER`` fallthrough (that stays Kimi).

No live DeepSeek calls. Keys in these tests are placeholders.
"""
from __future__ import annotations

from app.agents.runtime import (
    DEEPSEEK_API_URL,
    DEEPSEEK_DEFAULT_MODEL,
    KIMI_API_URL,
    _llm_config,
    _provider_temperature,
)


def test_llm_config_picks_deepseek_when_explicit(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "deepseek"
    assert cfg["url"] == DEEPSEEK_API_URL
    assert cfg["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert cfg["env_key"] == "DEEPSEEK_API_KEY"
    assert cfg["default_model"] == DEEPSEEK_DEFAULT_MODEL
    assert cfg["default_model"] == "deepseek-chat"


def test_deepseek_model_override_reasoner(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    assert _llm_config()["default_model"] == "deepseek-reasoner"


def test_deepseek_model_override_catalogue_id(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    assert _llm_config()["default_model"] == "deepseek-v4-flash"


def test_deepseek_missing_key_still_returns_config(monkeypatch):
    """Callers check env_key themselves; missing key must not drop the branch."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "deepseek"
    assert cfg["env_key"] == "DEEPSEEK_API_KEY"
    assert cfg["url"] == DEEPSEEK_API_URL


def test_deepseek_has_no_fixed_temperature(monkeypatch):
    """DeepSeek accepts the agent's temperature; do not copy Kimi's pin."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    cfg = _llm_config()
    assert "fixed_temperature" not in cfg
    assert _provider_temperature(cfg, 0.3) == 0.3


def test_deepseek_does_not_steal_unset_provider(monkeypatch):
    """A leftover DEEPSEEK_API_KEY must not become the implicit primary."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = _llm_config()
    assert cfg["provider"] == "kimi"
    assert cfg["url"] == KIMI_API_URL


def test_unrecognised_provider_still_falls_through_to_kimi(monkeypatch):
    """``deepseek`` must resolve; leftover ``openai`` / junk must not."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key")
    cfg = _llm_config()
    assert cfg["provider"] == "kimi"
    monkeypatch.setenv("LLM_PROVIDER", "nonsense")
    assert _llm_config()["provider"] == "kimi"
