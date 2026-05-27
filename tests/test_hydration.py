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


# ── Gap closure: drop-folder discovery ─────────────────────────────────────


def _make_dropbox_file(tmp_path, project_id: str, filename: str, content: bytes = b"x"):
    """Create a file under the convention path the discovery helper walks."""
    import os

    dropbox = os.path.join(str(tmp_path), "projects", project_id, "dropbox")
    os.makedirs(dropbox, exist_ok=True)
    p = os.path.join(dropbox, filename)
    with open(p, "wb") as f:
        f.write(content)
    return p


def _make_project(name: str = "test-project") -> str:
    """Create a real project row and return its generated id."""
    from app.core import projects as projects_store

    projects_store.init_db()
    p = projects_store.create_project(name=name, user_id="u1")
    return p["id"]


def test_discover_attaches_new_files(isolated_data_dir, monkeypatch):
    """Files dropped under the convention path get auto-attached to the project."""
    from app.core import projects as projects_store
    from app.blocks.hydration import _discover_local_drive_files

    monkeypatch.setenv("LOCAL_DRIVE_ROOT", str(isolated_data_dir))
    pid = _make_project("X")

    _make_dropbox_file(isolated_data_dir, pid, "spec.pdf", b"%PDF-1.4\n")
    _make_dropbox_file(isolated_data_dir, pid, "plan.dwg", b"x")
    _make_dropbox_file(isolated_data_dir, pid, "ignored.exe", b"x")  # disallowed
    _make_dropbox_file(isolated_data_dir, pid, ".hidden", b"x")      # dotfile

    count, errors = _discover_local_drive_files(pid)
    assert count == 2, f"expected 2 attached, got {count} (errors: {errors})"
    assert errors == []

    docs = projects_store.list_documents(pid)
    names = sorted(d["original_name"] for d in docs)
    assert names == ["plan.dwg", "spec.pdf"]


def test_discover_is_idempotent(isolated_data_dir, monkeypatch):
    """Running discovery twice in a row does not re-attach the same files."""
    from app.blocks.hydration import _discover_local_drive_files

    monkeypatch.setenv("LOCAL_DRIVE_ROOT", str(isolated_data_dir))
    pid = _make_project("I")
    _make_dropbox_file(isolated_data_dir, pid, "a.pdf", b"%PDF\n")

    first, _ = _discover_local_drive_files(pid)
    second, _ = _discover_local_drive_files(pid)
    assert first == 1
    assert second == 0


def test_discover_skips_when_dropbox_missing(isolated_data_dir, monkeypatch):
    from app.blocks.hydration import _discover_local_drive_files

    monkeypatch.setenv("LOCAL_DRIVE_ROOT", str(isolated_data_dir))
    count, errors = _discover_local_drive_files("nope")
    assert count == 0
    assert errors == []


def test_discover_oversize_file_recorded_as_error(isolated_data_dir, monkeypatch):
    from app.blocks.hydration import _discover_local_drive_files

    monkeypatch.setenv("LOCAL_DRIVE_ROOT", str(isolated_data_dir))
    monkeypatch.setenv("HYDRATION_MAX_ATTACH_SIZE", "100")
    pid = _make_project("O")
    _make_dropbox_file(isolated_data_dir, pid, "big.pdf", b"x" * 500)

    count, errors = _discover_local_drive_files(pid)
    assert count == 0
    assert any("oversize" in e for e in errors)


# ── Gap closure: heuristic summary fallback ────────────────────────────────


@pytest.mark.asyncio
async def test_offline_fallback_produces_heuristic_summary(isolated_data_dir, monkeypatch):
    """When ChatBlock returns offline_template, the row must contain a
    structured heuristic summary — not just the offline placeholder."""
    from app.blocks.hydration import HydrationBlock
    from app.blocks import hydration as hydration_module
    from app.core import hydration_store

    target = "2026-05-26"
    in_window = "2026-05-26T10:00:00Z"
    _seed_conversation("proj-h", in_window, "Why is my BOQ empty?", "Looking now")
    _seed_conversation("proj-h", in_window, "rebar quantity for level 3", "120 kg")

    async def offline_chat(prompt, max_tokens=600):
        return ("_offline placeholder_", "offline_template")

    monkeypatch.setattr(hydration_module, "_call_chat", offline_chat)

    block = HydrationBlock()
    await block.execute({"operation": "run", "target_date": target}, {})

    row = hydration_store.get_latest("project", "proj-h")
    assert row is not None
    md = row["summary_md"]
    # Heuristic markers
    assert "heuristic" in md.lower()
    assert "## What users asked for" in md
    assert "## Where they hit friction" in md
    assert "## Recurring patterns or themes" in md
    # Real signal extracted (one of the user messages used the word 'empty',
    # which the friction-signal regex flags as complaint language)
    assert "complaint" in md.lower()
    # Keyword frequencies must show domain words (rebar, boq, etc.) not stopwords
    assert "rebar" in md.lower() or "boq" in md.lower()
    # The original placeholder is replaced, not concatenated.
    assert "_offline placeholder_" not in md


@pytest.mark.asyncio
async def test_llm_path_uses_llm_summary_not_heuristic(isolated_data_dir, monkeypatch):
    """If ChatBlock returns a real provider (deepseek/local_*), the LLM output
    is preserved — the heuristic must NOT clobber it."""
    from app.blocks.hydration import HydrationBlock
    from app.blocks import hydration as hydration_module
    from app.core import hydration_store

    target = "2026-05-26"
    in_window = "2026-05-26T10:00:00Z"
    _seed_conversation("proj-l", in_window, "q", "a")

    async def real_chat(prompt, max_tokens=600):
        return ("## LLM-AUTHORED SUMMARY\n- one\n", "deepseek")

    monkeypatch.setattr(hydration_module, "_call_chat", real_chat)
    block = HydrationBlock()
    await block.execute({"operation": "run", "target_date": target}, {})

    row = hydration_store.get_latest("project", "proj-l")
    assert "LLM-AUTHORED SUMMARY" in row["summary_md"]
    assert "heuristic" not in row["summary_md"].lower()


def test_heuristic_friction_detection():
    """The friction-signal heuristic flags complaint language and repeats."""
    from app.blocks.hydration import _user_friction_signals

    msgs = [
        {"role": "user", "content": "Why is this broken?"},
        {"role": "assistant", "content": "Looking now"},
        {"role": "user", "content": "Why is this broken?"},  # repeat of the prefix
    ]
    signals = _user_friction_signals(msgs)
    assert any("complaint" in s.lower() for s in signals)
    assert any("repeat" in s.lower() for s in signals)


def test_heuristic_top_keywords_skips_stopwords():
    from app.blocks.hydration import _top_keywords

    msgs = [
        {"role": "user", "content": "the rebar and the concrete should be fine"},
        {"role": "user", "content": "rebar quantities for the concrete pour"},
    ]
    words = dict(_top_keywords(msgs))
    assert "the" not in words
    assert "and" not in words
    assert "rebar" in words
    assert "concrete" in words


# ── Gap closure: timezone support ──────────────────────────────────────────


def test_scheduler_tz_defaults_to_utc(monkeypatch):
    from datetime import timezone as dt_timezone
    from app.core import hydration_scheduler

    monkeypatch.delenv("HYDRATION_TZ", raising=False)
    assert hydration_scheduler._tz() is dt_timezone.utc


def test_scheduler_tz_accepts_iana_zone(monkeypatch):
    from app.core import hydration_scheduler

    monkeypatch.setenv("HYDRATION_TZ", "Asia/Dubai")
    tz = hydration_scheduler._tz()
    # ZoneInfo objects carry a `.key`; UTC fallback would not.
    assert getattr(tz, "key", None) == "Asia/Dubai"


def test_scheduler_tz_falls_back_on_invalid(monkeypatch):
    from datetime import timezone as dt_timezone
    from app.core import hydration_scheduler

    monkeypatch.setenv("HYDRATION_TZ", "Not/A_Zone")
    assert hydration_scheduler._tz() is dt_timezone.utc


def test_seconds_until_next_uses_local_clock(monkeypatch):
    """1 AM in Asia/Dubai is a different absolute moment than 1 AM UTC. The
    delay computation must respect the chosen zone, not silently use UTC."""
    from app.core import hydration_scheduler

    monkeypatch.setenv("HYDRATION_TZ", "Asia/Dubai")
    dubai_tz = hydration_scheduler._tz()
    utc_delay = hydration_scheduler._seconds_until_next(1, None)
    dubai_delay = hydration_scheduler._seconds_until_next(1, dubai_tz)
    # The two should not be equal (within a few seconds) — Dubai is +04:00,
    # so its "1 AM local" is a different wall-clock moment than 1 AM UTC.
    assert abs(utc_delay - dubai_delay) > 60


# ── Google Drive service-account discovery ─────────────────────────────────


def test_gdrive_parse_project_folder_map(monkeypatch):
    from app.core import gdrive_service

    monkeypatch.setenv("GDRIVE_PROJECT_FOLDERS", "p1:folder1, p2:folder2,, bad-entry, : ,p3:f3")
    m = gdrive_service.parse_project_folder_map()
    assert m == {"p1": "folder1", "p2": "folder2", "p3": "f3"}


def test_gdrive_not_configured_is_silent(monkeypatch, isolated_data_dir):
    """No env vars set → discover returns (0, []) and makes no API calls."""
    from app.blocks.hydration import _discover_gdrive_files

    monkeypatch.delenv("GDRIVE_PROJECT_FOLDERS", raising=False)
    monkeypatch.delenv("GDRIVE_SERVICE_ACCOUNT_JSON", raising=False)
    pid = _make_project("Q")
    count, errors = _discover_gdrive_files(pid)
    assert count == 0
    assert errors == []


def test_gdrive_mapping_without_key_reports_error(monkeypatch, isolated_data_dir):
    """Folder mapping set but key missing → recorded as a non-fatal error."""
    from app.blocks.hydration import _discover_gdrive_files

    pid = _make_project("K")
    monkeypatch.setenv("GDRIVE_PROJECT_FOLDERS", f"{pid}:abc123")
    monkeypatch.delenv("GDRIVE_SERVICE_ACCOUNT_JSON", raising=False)
    count, errors = _discover_gdrive_files(pid)
    assert count == 0
    assert any("GDRIVE_SERVICE_ACCOUNT_JSON" in e for e in errors)


def test_gdrive_happy_path_attaches_and_dedupes(monkeypatch, isolated_data_dir):
    """With list/download stubbed, two new files get attached on the first
    pass; a second pass attaches zero (sidecar dedup)."""
    from app.blocks import hydration as hydration_module
    from app.core import gdrive_service, projects as projects_store

    pid = _make_project("D")
    monkeypatch.setenv("GDRIVE_PROJECT_FOLDERS", f"{pid}:driveFolderId")
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", "{}")  # any truthy value; gdrive_service.is_configured checks env, not content

    # Stub the gdrive_service surface so the test doesn't need a real key.
    monkeypatch.setattr(gdrive_service, "is_configured", lambda: True)
    monkeypatch.setattr(
        gdrive_service,
        "list_folder_files",
        lambda folder_id, page_size=100: (
            [
                {"id": "f1", "name": "spec.pdf", "mimeType": "application/pdf", "size": "12"},
                {"id": "f2", "name": "plan.dwg", "mimeType": "application/octet-stream", "size": "5"},
                {"id": "f3", "name": "design.gdoc", "mimeType": "application/vnd.google-apps.document"},
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        gdrive_service,
        "download_file_bytes",
        lambda fid: (b"%PDF-1.4\nhello" if fid == "f1" else b"binary", None),
    )

    # Avoid double-encrypting in the test
    monkeypatch.setattr("app.core.file_crypto.write_document",
                        lambda path, data: open(path, "wb").write(data))

    count1, errors1 = hydration_module._discover_gdrive_files(pid)
    assert count1 == 2, f"expected 2 attached, got {count1} (errors: {errors1})"
    # The Google-native doc must be silently skipped (it's not a real download target)
    assert all("design.gdoc" not in e for e in errors1)

    docs = projects_store.list_documents(pid)
    names = sorted(d["original_name"] for d in docs)
    assert names == ["plan.dwg", "spec.pdf"]

    # Second pass: same listing, but sidecar now remembers f1+f2+f3 → zero new
    count2, errors2 = hydration_module._discover_gdrive_files(pid)
    assert count2 == 0
    assert errors2 == []


def test_gdrive_oversize_advertised_is_skipped(monkeypatch, isolated_data_dir):
    """Files whose Drive metadata advertises a size above the cap never get
    downloaded — the size check happens before the download call."""
    from app.blocks import hydration as hydration_module
    from app.core import gdrive_service

    pid = _make_project("OS")
    monkeypatch.setenv("GDRIVE_PROJECT_FOLDERS", f"{pid}:driveFolderId")
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setenv("HYDRATION_MAX_ATTACH_SIZE", "100")
    monkeypatch.setattr(gdrive_service, "is_configured", lambda: True)
    monkeypatch.setattr(
        gdrive_service,
        "list_folder_files",
        lambda folder_id, page_size=100: (
            [{"id": "fbig", "name": "huge.pdf", "mimeType": "application/pdf", "size": "9999999"}],
            None,
        ),
    )
    downloads = []
    monkeypatch.setattr(
        gdrive_service,
        "download_file_bytes",
        lambda fid: (downloads.append(fid), (b"x", None))[1],
    )

    count, errors = hydration_module._discover_gdrive_files(pid)
    assert count == 0
    assert any("oversize" in e for e in errors)
    assert downloads == [], "oversize file must be skipped before download is called"


def test_gdrive_list_error_is_non_fatal(monkeypatch, isolated_data_dir):
    """A Drive API failure during list must surface as an error string, not
    raise — hydration's per-project loop catches but the inner helper should
    return cleanly anyway."""
    from app.blocks import hydration as hydration_module
    from app.core import gdrive_service

    pid = _make_project("L")
    monkeypatch.setenv("GDRIVE_PROJECT_FOLDERS", f"{pid}:driveFolderId")
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setattr(gdrive_service, "is_configured", lambda: True)
    monkeypatch.setattr(
        gdrive_service,
        "list_folder_files",
        lambda folder_id, page_size=100: ([], "403: insufficient permissions"),
    )

    count, errors = hydration_module._discover_gdrive_files(pid)
    assert count == 0
    assert any("403" in e for e in errors)


def test_gdrive_load_service_account_info_handles_inline_and_file(tmp_path, monkeypatch):
    """The key env var may be a path OR inline JSON. Malformed → returns None."""
    from app.core import gdrive_service

    # Inline JSON
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", '{"type": "service_account", "client_email": "x@y.iam"}')
    info = gdrive_service._load_service_account_info()
    assert info and info["client_email"] == "x@y.iam"

    # File path
    keyfile = tmp_path / "key.json"
    keyfile.write_text('{"type": "service_account", "client_email": "f@g.iam"}')
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", str(keyfile))
    info = gdrive_service._load_service_account_info()
    assert info and info["client_email"] == "f@g.iam"

    # Malformed → None (warning logged, no crash)
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", "not-json-and-not-a-path")
    assert gdrive_service._load_service_account_info() is None

    # Unset → None
    monkeypatch.delenv("GDRIVE_SERVICE_ACCOUNT_JSON", raising=False)
    assert gdrive_service._load_service_account_info() is None
