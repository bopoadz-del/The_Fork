"""Tests for the hydration block, store, and scheduler.

Strategy:
- Point DATA_DIR at a per-test tmp dir so the SQLite stores are isolated.
- Stub the ChatBlock summarizer (``_call_chat``) so tests don't require a
  running LLM and run in milliseconds.
- Seed agent_memory with conversations + messages dated to the target window
  to exercise the project-activity-detection path.
- Cover the scheduler's enable/disable gate and the next-hour math so we
  don't have to wait for an actual midnight to verify the loop wiring.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest


# ── DATA_DIR isolation fixture ─────────────────────────────────────────────


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Each test gets its own clean DATA_DIR so SQLite files don't bleed."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield tmp_path


# ── Helpers ────────────────────────────────────────────────────────────────


def _seed_conversation(project_id: str, ts: str, user_msg: str, assistant_msg: str) -> str:
    """Insert a conversation + two messages directly via agent_memory's API.

    The messages get the API's "now" timestamp, so to land them in a target
    window we patch _now in the agent_memory module before each call.
    """
    import uuid as _uuid
    from app.core import agent_memory

    agent_memory.init_db()
    original = agent_memory._now
    agent_memory._now = lambda: ts  # type: ignore[assignment]
    try:
        conv_id = str(_uuid.uuid4())
        conv = agent_memory.get_or_create_conversation(
            conversation_id=conv_id,
            agent_name="chat",
            project_id=project_id,
        )
        agent_memory.append_message(conv["id"], "user", user_msg)
        agent_memory.append_message(conv["id"], "assistant", assistant_msg)
        return conv["id"]
    finally:
        agent_memory._now = original  # type: ignore[assignment]


# ── Store tests ────────────────────────────────────────────────────────────


def test_store_init_and_roundtrip(isolated_data_dir):
    from app.core import hydration_store

    hydration_store.init_db()
    rid = hydration_store.record_run(
        run_date="2026-05-26",
        scope="project",
        project_id="p1",
        summary_md="## Lessons\n- be more careful",
        facts={"messages_seen": 4, "files_indexed": 2, "files_skipped": 0, "file_errors": []},
        provider="offline_template",
    )
    assert rid

    row = hydration_store.get_latest("project", "p1")
    assert row is not None
    assert row["scope"] == "project"
    assert row["project_id"] == "p1"
    assert row["summary_md"].startswith("## Lessons")
    assert row["facts"]["messages_seen"] == 4
    assert row["provider"] == "offline_template"


def test_store_scope_validation(isolated_data_dir):
    from app.core import hydration_store

    hydration_store.init_db()
    with pytest.raises(ValueError):
        hydration_store.record_run(
            run_date="2026-05-26", scope="bogus", project_id="p1",
            summary_md="x", facts={}, provider="x",
        )
    with pytest.raises(ValueError):
        hydration_store.record_run(
            run_date="2026-05-26", scope="project", project_id=None,
            summary_md="x", facts={}, provider="x",
        )


def test_store_history_ordering(isolated_data_dir):
    from app.core import hydration_store

    hydration_store.init_db()
    for d in ("2026-05-24", "2026-05-25", "2026-05-26"):
        hydration_store.record_run(
            run_date=d, scope="global", project_id=None,
            summary_md=f"day {d}", facts={}, provider="x",
        )
    hist = hydration_store.list_history(scope="global", limit=10)
    assert len(hist) == 3
    # newest first
    assert hist[0]["run_date"] == "2026-05-26"


# ── Block-level tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_with_no_activity(isolated_data_dir, monkeypatch):
    """An empty day still produces a global row (with empty per-project list),
    and the per-project loop runs zero times."""
    from app.blocks.hydration import HydrationBlock
    from app.blocks import hydration as hydration_module
    from app.core import hydration_store

    # No conversations seeded; force a fixed target_date.
    async def fake_chat(prompt, max_tokens=600):
        return ("## Activity at a glance\n- none\n", "offline_template")

    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    block = HydrationBlock()
    envelope = await block.execute(
        {"operation": "run", "target_date": "2026-05-26"}, {}
    )
    assert envelope["status"] == "success"
    result = envelope["result"]
    assert result["projects_processed"] == 0
    assert result["files_indexed"] == 0

    g = hydration_store.get_latest("global")
    assert g is not None
    assert g["run_date"] == "2026-05-26"
    assert g["facts"]["projects_processed"] == 0


@pytest.mark.asyncio
async def test_run_summarizes_per_project_and_global(isolated_data_dir, monkeypatch):
    """Seed two projects with activity in the window; confirm one per-project
    row each + one global row, and that summary text reaches the store."""
    from app.blocks.hydration import HydrationBlock
    from app.blocks import hydration as hydration_module
    from app.core import hydration_store

    target = "2026-05-26"
    in_window_ts = "2026-05-26T10:00:00Z"

    _seed_conversation("alpha", in_window_ts, "how much rebar?", "120 kg/m3")
    _seed_conversation("beta", in_window_ts, "BOQ for L1?", "see attachment")

    captured_prompts = []

    async def fake_chat(prompt, max_tokens=600):
        captured_prompts.append(prompt)
        # Different text for project vs global so we can assert below
        if "global hydration rollup" in prompt:
            return ("## Activity at a glance\n- 2 projects active\n", "deepseek")
        return ("## What users asked for\n- rebar / BOQ\n", "deepseek")

    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    block = HydrationBlock()
    envelope = await block.execute(
        {"operation": "run", "target_date": target}, {}
    )
    assert envelope["status"] == "success"
    result = envelope["result"]
    assert result["projects_processed"] == 2

    alpha = hydration_store.get_latest("project", "alpha")
    beta = hydration_store.get_latest("project", "beta")
    glob = hydration_store.get_latest("global")
    assert alpha and beta and glob
    assert "rebar" in alpha["summary_md"] or "BOQ" in alpha["summary_md"]
    assert "2 projects active" in glob["summary_md"]
    assert alpha["provider"] == "deepseek"

    # The per-project prompts must include the project_id
    proj_prompts = [p for p in captured_prompts if "global hydration rollup" not in p]
    assert any("'alpha'" in p for p in proj_prompts)
    assert any("'beta'" in p for p in proj_prompts)


@pytest.mark.asyncio
async def test_run_isolates_project_failures(isolated_data_dir, monkeypatch):
    """A failure in one project must not abort the global pass."""
    from app.blocks.hydration import HydrationBlock
    from app.blocks import hydration as hydration_module

    target = "2026-05-26"
    in_window_ts = "2026-05-26T10:00:00Z"
    _seed_conversation("good", in_window_ts, "q", "a")
    _seed_conversation("bad", in_window_ts, "q", "a")

    async def fake_chat(prompt, max_tokens=600):
        if "'bad'" in prompt:
            raise RuntimeError("LLM unavailable for bad")
        return ("ok summary", "deepseek")

    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    block = HydrationBlock()
    envelope = await block.execute({"operation": "run", "target_date": target}, {})
    assert envelope["status"] == "success"
    result = envelope["result"]
    # 'bad' raises, so only 1 project succeeded; global must still record
    assert result["projects_processed"] == 1
    assert any("bad" in e for e in result.get("errors", []))


@pytest.mark.asyncio
async def test_latest_operation_via_block(isolated_data_dir, monkeypatch):
    """The block's 'latest' op is the same path the router uses."""
    from app.blocks.hydration import HydrationBlock
    from app.core import hydration_store

    hydration_store.record_run(
        run_date="2026-05-26", scope="global", project_id=None,
        summary_md="g", facts={"x": 1}, provider="local_ollama",
    )
    block = HydrationBlock()
    envelope = await block.execute({"operation": "latest", "scope": "global"}, {})
    assert envelope["status"] == "success"
    result = envelope["result"]
    assert result["status"] == "success"
    assert result["facts"]["x"] == 1


@pytest.mark.asyncio
async def test_latest_returns_empty_when_no_runs(isolated_data_dir):
    from app.blocks.hydration import HydrationBlock

    block = HydrationBlock()
    envelope = await block.execute({"operation": "latest", "scope": "global"}, {})
    # The outer envelope always reports success (process did not raise),
    # but the inner status carries the semantic "no data yet" signal.
    assert envelope["status"] == "success"
    assert envelope["result"]["status"] == "empty"


# ── Scheduler tests ────────────────────────────────────────────────────────


def test_scheduler_disabled_when_env_false(monkeypatch):
    from app.core import hydration_scheduler

    monkeypatch.setenv("HYDRATION_ENABLED", "false")
    assert hydration_scheduler._enabled() is False


def test_scheduler_enabled_by_default(monkeypatch):
    from app.core import hydration_scheduler

    monkeypatch.delenv("HYDRATION_ENABLED", raising=False)
    assert hydration_scheduler._enabled() is True


def test_scheduler_seconds_until_next_is_in_range(monkeypatch):
    from app.core import hydration_scheduler

    # Always positive, never > 24h
    s = hydration_scheduler._seconds_until_next(1)
    assert 0 < s <= 24 * 3600 + 1


def test_scheduler_hour_clamped_to_valid_range(monkeypatch):
    from app.core import hydration_scheduler

    monkeypatch.setenv("HYDRATION_HOUR_UTC", "99")
    assert hydration_scheduler._hour_utc() == 23
    monkeypatch.setenv("HYDRATION_HOUR_UTC", "-5")
    assert hydration_scheduler._hour_utc() == 0
    monkeypatch.setenv("HYDRATION_HOUR_UTC", "not-a-number")
    assert hydration_scheduler._hour_utc() == 1  # default


# ── Registry sanity ────────────────────────────────────────────────────────


def test_block_is_registered():
    from app.blocks import BLOCK_REGISTRY

    assert "hydration" in BLOCK_REGISTRY, (
        "HydrationBlock should be in BLOCK_REGISTRY — check app/blocks/__init__.py"
    )
