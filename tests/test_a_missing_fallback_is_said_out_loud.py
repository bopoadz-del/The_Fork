"""A chat with no usable fallback must say so -- at boot and on /health.

Live 2026-09-19: LLM_FALLBACK_PROVIDER on Render was `kimi`, a provider the
code had removed. It resolved back to the primary, so there was no fallback
at all, and nothing anywhere said so. An unset fallback key does the same.
"""
import logging

import pytest

from app.core.health_probes import probe_llm

_ENV = ("LLM_PROVIDER", "LLM_FALLBACK_PROVIDER", "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY", "OPENROUTER_MODEL")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")


def test_deepseek_with_an_openrouter_fallback_is_ready(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    assert probe_llm() == {"primary_ready": True, "fallback_ready": True}


@pytest.mark.parametrize("fallback,key", [
    ("kimi", "or-test"),        # the live value: a removed provider
    ("deepseek", "or-test"),    # names the primary itself
    ("openrouter", None),       # the right provider, no key
    (None, "or-test"),          # unset
])
def test_a_fallback_that_cannot_work_is_reported_as_not_ready(monkeypatch, fallback, key):
    if fallback:
        monkeypatch.setenv("LLM_FALLBACK_PROVIDER", fallback)
    if key:
        monkeypatch.setenv("OPENROUTER_API_KEY", key)
    assert probe_llm()["fallback_ready"] is False


def test_a_missing_primary_key_is_reported(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert probe_llm()["primary_ready"] is False


def test_boot_warns_when_there_is_no_usable_fallback(monkeypatch, caplog):
    from app.main import _warn_when_the_llm_has_no_fallback

    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "kimi")
    with caplog.at_level(logging.WARNING, logger="app.main"):
        _warn_when_the_llm_has_no_fallback()
    assert "NO USABLE FALLBACK" in caplog.text
    assert "ds-test" not in caplog.text


def test_boot_is_quiet_when_the_fallback_works(monkeypatch, caplog):
    from app.main import _warn_when_the_llm_has_no_fallback

    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    with caplog.at_level(logging.WARNING, logger="app.main"):
        _warn_when_the_llm_has_no_fallback()
    assert "FALLBACK" not in caplog.text


def test_health_shows_it_and_names_nothing(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "kimi")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    with TestClient(app) as c:
        body = c.get("/health").json()
    assert body["checks"]["llm"] == {"primary_ready": True, "fallback_ready": False}
    text = str(body["checks"]["llm"]).lower()
    for word in ("deepseek", "openrouter", "kimi", "key", "test"):
        assert word not in text.replace("_ready", ""), word
