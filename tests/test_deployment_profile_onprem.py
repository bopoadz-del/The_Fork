"""STEP 2 — DEPLOYMENT_PROFILE=onprem zero-egress profile.

Two invariants per gate:
  * Under ``DEPLOYMENT_PROFILE=onprem`` the would-egress feature returns an
    honest-unavailable payload and makes NO network attempt.
  * Under the default (cloud) profile every gate is a strict no-op — the path
    is byte-for-byte today's behaviour (merge-safety: this ships to the cloud
    dev/demo on main).

Plus the boot assertion: on-prem refuses to start on a config that would leak
(cloud LLM provider, offline flags unset, Sentry on).

No network, no LLM, no hardware needed — gates short-circuit before any call.
"""
from __future__ import annotations

import pytest

from app.core import deployment_profile as dp


# ── profile switch ─────────────────────────────────────────────────────────

def test_default_profile_is_cloud(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_PROFILE", raising=False)
    assert dp.profile() == "cloud"
    assert dp.is_onprem() is False


def test_onprem_selected(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "onprem")
    assert dp.is_onprem() is True


def test_onprem_unavailable_shape():
    out = dp.onprem_unavailable("Widget")
    assert out["status"] == "error"
    assert out["onprem_unavailable"] is True
    assert "Widget" in out["error"]


# ── boot assertion ─────────────────────────────────────────────────────────

def _clean_env(monkeypatch):
    for k in ("LLM_PROVIDER", "LLM_FALLBACK_PROVIDER", "OLLAMA_API_KEY",
              "SENTRY_DSN"):
        monkeypatch.delenv(k, raising=False)


def test_assert_noop_under_cloud(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_PROFILE", raising=False)
    # Even a cloud provider + no offline flags is fine under the cloud profile.
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    dp.assert_onprem_ready()  # must not raise
    assert dp.check_onprem_ready() == []


@pytest.mark.parametrize("cloud_provider", ["groq", "kimi", "openrouter"])
def test_assert_raises_on_cloud_llm_provider(monkeypatch, cloud_provider):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "onprem")
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", cloud_provider)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="cloud provider"):
        dp.assert_onprem_ready()


def test_assert_raises_on_missing_offline_flags(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "onprem")
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    problems = dp.check_onprem_ready()
    assert any("HF_HUB_OFFLINE" in p for p in problems)
    assert any("TRANSFORMERS_OFFLINE" in p for p in problems)


def test_assert_raises_on_sentry_and_cloud_tunnel(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "onprem")
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("SENTRY_DSN", "https://x@y.ingest.sentry.io/1")
    monkeypatch.setenv("OLLAMA_API_KEY", "cloudkey")
    problems = dp.check_onprem_ready()
    assert any("SENTRY_DSN" in p for p in problems)
    assert any("OLLAMA_API_KEY" in p for p in problems)


def test_assert_passes_on_valid_onprem_config(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "onprem")
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    dp.assert_onprem_ready()  # must not raise
    assert dp.check_onprem_ready() == []


# ── block gates: onprem → unavailable, no network ──────────────────────────
# Tests are async; pytest.ini sets asyncio_mode=auto so `async def test_*` runs.

@pytest.fixture
def onprem(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "onprem")


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_PROFILE", raising=False)


def _assert_gated(result, feature_substr):
    assert isinstance(result, dict)
    assert result.get("onprem_unavailable") is True
    assert result.get("status") == "error"
    assert feature_substr.lower() in result.get("error", "").lower()


async def test_translate_block_gated(onprem):
    from app.blocks.translate import TranslateBlock
    _assert_gated(await TranslateBlock().process("hello", {"target": "spanish"}), "on-prem")


async def test_web_block_gated(onprem):
    from app.blocks.web import WebBlock
    _assert_gated(await WebBlock().process({"url": "https://example.com"},
                                           {"operation": "fetch"}), "on-prem")


async def test_search_block_gated(onprem):
    from app.blocks.search import SearchBlock
    _assert_gated(await SearchBlock().process("bridges", {}), "on-prem")


async def test_onedrive_block_gated(onprem):
    from app.blocks.onedrive import OneDriveBlock
    _assert_gated(await OneDriveBlock().process({}, {"operation": "list"}), "on-prem")


async def test_google_drive_block_gated(onprem):
    from app.blocks.google_drive import GoogleDriveBlock
    _assert_gated(await GoogleDriveBlock().process({}, {"operation": "list"}), "on-prem")


async def test_mcp_consumer_gated(onprem):
    from app.blocks.mcp_consumer import MCPConsumerBlock
    _assert_gated(await MCPConsumerBlock().process({"server": "github", "tool": "x"}, {}),
                  "on-prem")


async def test_webhook_send_gated(onprem):
    # Skip the heavy ctor (hal_block/config) — the send gate returns before any
    # instance state is touched. register/list stay local (not gated in code).
    from app.blocks.webhook import WebhookBlock
    b = WebhookBlock.__new__(WebhookBlock)
    _assert_gated(await b.process({"action": "send", "url": "https://x.com"}, {}), "on-prem")
    trig = await b.process({"action": "trigger", "event": "e"}, {})
    assert trig.get("onprem_unavailable") is True


async def test_weather_gated(onprem):
    # Skip the heavy container __init__; _fetch_weather uses no instance state.
    from app.containers.construction import ConstructionContainer
    c = ConstructionContainer.__new__(ConstructionContainer)
    out = await c._fetch_weather("Riyadh", "2026-01-01")
    assert out["status"] == "unavailable"
    assert "on-prem" in out["reason"].lower()


# ── merge-safety: cloud profile leaves every gate inert ────────────────────

async def test_cloud_profile_gates_are_noops(cloud):
    """Under the default profile the gates do not fire (they fall through to the
    real block logic). The block may still error for other reasons (bad input),
    but never with the onprem_unavailable marker."""
    from app.blocks.translate import TranslateBlock
    from app.blocks.mcp_consumer import MCPConsumerBlock
    r1 = await TranslateBlock().process("", {})          # empty -> its own error
    assert not r1.get("onprem_unavailable")
    r2 = await MCPConsumerBlock().process({}, {})        # missing server/tool
    assert not r2.get("onprem_unavailable")


# ── boot manifest + disk-survival canary ───────────────────────────────────

def test_boot_manifest_shape(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "onprem")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    m = dp.boot_manifest()
    assert m["profile"] == "onprem"
    assert m["llm_provider"] == "ollama"
    assert "embedder" in m and "model" in m["embedder"]
    assert set(m["offline_flags"]) == {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}
    assert "db_backend" in m


def test_disk_canary_seeds_then_persists(tmp_path):
    d = str(tmp_path / "data")
    first = dp.disk_survival_canary(d)
    assert first["ok"] is True
    assert "seeded" in first["note"]
    ts = first["first_seen"]
    # Second boot on the same dir: sentinel survived, same first_seen.
    second = dp.disk_survival_canary(d)
    assert second["ok"] is True
    assert "persisted" in second["note"]
    assert second["first_seen"] == ts


async def test_weather_not_gated_under_cloud(cloud, monkeypatch):
    """Cloud profile: the weather gate is inert — the method proceeds past it
    (fails later on its own validation, not with the on-prem reason)."""
    from app.containers.construction import ConstructionContainer
    c = ConstructionContainer.__new__(ConstructionContainer)
    out = await c._fetch_weather("", "2026-01-01")   # empty location -> own reason
    assert "on-prem" not in out.get("reason", "").lower()
