"""The two Kimi 'hiccup' fixes (2026-07-25, from live logs).

Live logs showed every empty-answer failure was one of two Kimi issues,
never a content bug:
  1. `llm: kimi timed out — falling back to groq` — the hardcoded 120s
     HTTP timeout gave up on Kimi's reasoning model before it finished on
     large grounded turns; Groq then 413'd, so the turn produced nothing.
  2. `synthesis ... failed: kimi HTTP 400` — llm_client.complete sent
     temperature=0.0, which Kimi k2.6 rejects (reasoning models require
     temperature=1); synthesis silently fell back to deterministic render.
"""

from __future__ import annotations

import os

import pytest


class TestLlmHttpTimeout:
    def test_default_is_above_120s(self, monkeypatch):
        monkeypatch.delenv("LLM_HTTP_TIMEOUT_SECONDS", raising=False)
        from app.agents.runtime import _llm_http_timeout
        # Must exceed the old 120s and stay below the 240s stream deadline.
        assert 120 < _llm_http_timeout() <= 240

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LLM_HTTP_TIMEOUT_SECONDS", "180")
        from app.agents.runtime import _llm_http_timeout
        assert _llm_http_timeout() == 180.0

    def test_garbage_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_HTTP_TIMEOUT_SECONDS", "not-a-number")
        from app.agents.runtime import _llm_http_timeout
        assert _llm_http_timeout() == 200.0


class TestMoonshotModelFallback:
    """Kimi k2.6 (slow reasoning, temp=1) falls back to a fast, temp-flexible,
    big-context Moonshot model on the SAME key — not to Groq, whose free tier
    413s on the large grounded payloads that trigger the fallback."""

    def test_kimi_fallback_model_is_same_provider_no_fixed_temp(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "kimi")
        monkeypatch.setenv("KIMI_API_KEY", "x")
        monkeypatch.setenv("KIMI_FALLBACK_MODEL", "moonshot-v1-128k")
        from app.agents.runtime import _llm_config, _llm_fallback_config
        p = _llm_config()
        f = _llm_fallback_config(p)
        assert f is not None
        assert f["provider"] == "kimi"
        assert f["default_model"] == "moonshot-v1-128k"
        assert f["env_key"] == "KIMI_API_KEY"          # same key
        assert "fixed_temperature" not in f            # accepts caller temperature

    def test_kimi_model_fallback_beats_cross_provider(self, monkeypatch):
        # With both set, the same-provider Moonshot model wins (Groq is bypassed).
        monkeypatch.setenv("LLM_PROVIDER", "kimi")
        monkeypatch.setenv("KIMI_API_KEY", "x")
        monkeypatch.setenv("KIMI_FALLBACK_MODEL", "moonshot-v1-128k")
        monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "g")
        from app.agents.runtime import _llm_config, _llm_fallback_config
        f = _llm_fallback_config(_llm_config())
        assert f["provider"] == "kimi" and f["default_model"] == "moonshot-v1-128k"

    def test_no_fallback_model_unset(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "kimi")
        monkeypatch.setenv("KIMI_API_KEY", "x")
        monkeypatch.delenv("KIMI_FALLBACK_MODEL", raising=False)
        monkeypatch.delenv("LLM_FALLBACK_PROVIDER", raising=False)
        from app.agents.runtime import _llm_config, _llm_fallback_config
        assert _llm_fallback_config(_llm_config()) is None


class TestKimiFixedTemperature:
    @pytest.mark.asyncio
    async def test_complete_honors_provider_fixed_temperature(self, monkeypatch):
        """llm_client.complete must send the provider's fixed_temperature
        (Kimi=1), not the passed 0.0, or Kimi 400s."""
        import app.core.llm_client as lc

        captured = {}

        class _FakeResp:
            status_code = 200
            request = None
            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class _FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                captured["temperature"] = json.get("temperature")
                return _FakeResp()

        monkeypatch.setattr(lc.httpx, "AsyncClient", _FakeClient)
        # Force the primary config to look like Kimi (fixed_temperature=1).
        import app.agents.runtime as rt
        monkeypatch.setattr(rt, "_llm_config", lambda: {
            "provider": "kimi", "url": "http://x", "env_key": "KIMI_API_KEY",
            "default_model": "kimi-k2.6", "fixed_temperature": 1,
        })
        monkeypatch.setattr(rt, "_llm_fallback_config", lambda cfg: None)
        monkeypatch.setenv("KIMI_API_KEY", "test")

        out = await lc.complete([{"role": "user", "content": "hi"}], temperature=0.0)
        assert out == "ok"
        assert captured["temperature"] == 1  # NOT the passed 0.0

    @pytest.mark.asyncio
    async def test_complete_uses_raw_temperature_when_no_fixed(self, monkeypatch):
        import app.core.llm_client as lc

        captured = {}

        class _FakeResp:
            status_code = 200
            request = None
            def json(self): return {"choices": [{"message": {"content": "ok"}}]}

        class _FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                captured["temperature"] = json.get("temperature")
                return _FakeResp()

        monkeypatch.setattr(lc.httpx, "AsyncClient", _FakeClient)
        import app.agents.runtime as rt
        monkeypatch.setattr(rt, "_llm_config", lambda: {
            "provider": "groq", "url": "http://x", "env_key": "GROQ_API_KEY",
            "default_model": "m",
        })
        monkeypatch.setattr(rt, "_llm_fallback_config", lambda cfg: None)
        monkeypatch.setenv("GROQ_API_KEY", "test")

        await lc.complete([{"role": "user", "content": "hi"}], temperature=0.3)
        assert captured["temperature"] == 0.3
