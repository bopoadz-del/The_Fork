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
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import requires_construction_kit


# ── DATA_DIR isolation fixture ─────────────────────────────────────────────


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Each test gets its own clean DATA_DIR so SQLite files don't bleed.

    Also forces agent_memory and projects re-init: both modules cache an
    ``_initialized`` flag at the module level, which stays True across tests
    even when DATA_DIR points at a fresh dir. Without resetting that flag,
    ``_ensure_db`` skips schema creation and queries fail with
    "no such table". This is a workaround for a real pre-existing bug;
    the right fix would be path-aware caching in those modules."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Reset module-level init flags so the fresh DATA_DIR actually gets a schema
    from app.core import agent_memory as _am
    from app.core import hydration_store as _hs
    from app.core import projects as _proj
    if hasattr(_am, "_initialized"):
        _am._initialized = False
    if hasattr(_hs, "_initialized"):
        _hs._initialized = False
    if hasattr(_hs, "_initialized_for_url"):
        _hs._initialized_for_url = None
    if hasattr(_proj, "_initialized"):
        _proj._initialized = False
    yield tmp_path


# ── Helpers ────────────────────────────────────────────────────────────────


def _ensure_project_row(project_id: str) -> None:
    """Stub project row for hydration_runs FK (unified schema)."""
    from app.core import projects as projects_store, users as users_mod
    from app.core.db import SessionLocal
    from app.core.models import Project

    projects_store.init_db()
    users_mod.ensure_user_exists("system")
    with SessionLocal() as session:
        if session.get(Project, project_id) is None:
            session.add(
                Project(
                    id=project_id,
                    name=project_id,
                    user_id="system",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            session.commit()


def _seed_conversation(project_id: str, ts: str, user_msg: str, assistant_msg: str) -> str:
    """Insert a conversation + two messages directly via agent_memory's API.

    The messages get the API's "now" timestamp, so to land them in a target
    window we patch _now in the agent_memory module before each call.
    """
    import uuid as _uuid
    from app.core import agent_memory

    _ensure_project_row(project_id)
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

    _ensure_project_row("p1")
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

    _ensure_project_row("p1")
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
    from app.core.learning import hydration as hydration_module
    from app.core import hydration_store

    # No conversations seeded; force a fixed target_date.
    async def fake_chat(prompt, max_tokens=600):
        return ("## Activity at a glance\n- none\n", "offline_template")

    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    envelope = await hydration_module.run(target_date="2026-05-26")
    envelope = {"status": "success", "result": envelope}
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
    from app.core.learning import hydration as hydration_module
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

    envelope = await hydration_module.run(target_date=target)
    envelope = {"status": "success", "result": envelope}
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
    from app.core.learning import hydration as hydration_module

    target = "2026-05-26"
    in_window_ts = "2026-05-26T10:00:00Z"
    _seed_conversation("good", in_window_ts, "q", "a")
    _seed_conversation("bad", in_window_ts, "q", "a")

    async def fake_chat(prompt, max_tokens=600):
        if "'bad'" in prompt:
            raise RuntimeError("LLM unavailable for bad")
        return ("ok summary", "deepseek")

    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    envelope = await hydration_module.run(target_date=target)
    envelope = {"status": "success", "result": envelope}
    assert envelope["status"] == "success"
    result = envelope["result"]
    # 'bad' raises, so only 1 project succeeded; global must still record
    assert result["projects_processed"] == 1
    assert any("bad" in e for e in result.get("errors", []))


@pytest.mark.asyncio
async def test_latest_operation_via_block(isolated_data_dir, monkeypatch):
    """The module's get_latest() — same path the router uses."""
    from app.core import hydration_store
    from app.core.learning import hydration as hydration_module

    hydration_store.record_run(
        run_date="2026-05-26", scope="global", project_id=None,
        summary_md="g", facts={"x": 1}, provider="local_ollama",
    )
    result = hydration_module.get_latest(scope="global")
    assert result["status"] == "success"
    assert result["facts"]["x"] == 1


@pytest.mark.asyncio
async def test_latest_returns_empty_when_no_runs(isolated_data_dir):
    from app.core.learning import hydration as hydration_module

    result = hydration_module.get_latest(scope="global")
    assert result["status"] == "empty"


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


# ── Registry sanity (post-merge) ───────────────────────────────────────────


@requires_construction_kit
def test_standalone_hydration_block_is_retired():
    """After the merge into learning_engine, there is no `hydration` block —
    only the `hydrate` operation on learning_engine."""
    from app.blocks import BLOCK_REGISTRY

    assert "hydration" not in BLOCK_REGISTRY, (
        "Standalone hydration block was supposed to be retired in the merge"
    )
    assert "learning_engine" in BLOCK_REGISTRY, (
        "learning_engine block must be present — it now owns hydrate"
    )


@requires_construction_kit
@pytest.mark.asyncio
async def test_hydrate_operation_on_learning_engine(isolated_data_dir, monkeypatch):
    """The merged path: call the public learning_engine block with operation=hydrate."""
    from app.blocks import BLOCK_REGISTRY
    from app.core.learning import hydration as hydration_module

    async def fake_chat(prompt, max_tokens=600):
        return ("## stub", "offline_template")
    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    cls = BLOCK_REGISTRY["learning_engine"]
    le = cls()
    envelope = await le.execute(
        {"operation": "hydrate", "target_date": "2026-05-26"}, {}
    )
    # learning_engine's execute() wraps process() in the standard envelope
    assert envelope["status"] == "success", f"envelope: {envelope}"
    inner = envelope["result"]
    assert inner["status"] == "success", f"inner: {inner}"
    assert inner["run_date"] == "2026-05-26"


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
    from app.core import users as users_mod

    projects_store.init_db()
    users_mod.ensure_user_exists("u1")
    p = projects_store.create_project(name=name, user_id="u1")
    return p["id"]


def test_discover_attaches_new_files(isolated_data_dir, monkeypatch):
    """Files dropped under the convention path get auto-attached to the project."""
    from app.core import projects as projects_store
    from app.core.learning.hydration import _discover_local_drive_files

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
    from app.core.learning.hydration import _discover_local_drive_files

    monkeypatch.setenv("LOCAL_DRIVE_ROOT", str(isolated_data_dir))
    pid = _make_project("I")
    _make_dropbox_file(isolated_data_dir, pid, "a.pdf", b"%PDF\n")

    first, _ = _discover_local_drive_files(pid)
    second, _ = _discover_local_drive_files(pid)
    assert first == 1
    assert second == 0


def test_discover_skips_when_dropbox_missing(isolated_data_dir, monkeypatch):
    from app.core.learning.hydration import _discover_local_drive_files

    monkeypatch.setenv("LOCAL_DRIVE_ROOT", str(isolated_data_dir))
    count, errors = _discover_local_drive_files("nope")
    assert count == 0
    assert errors == []


def test_discover_oversize_file_recorded_as_error(isolated_data_dir, monkeypatch):
    from app.core.learning.hydration import _discover_local_drive_files

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
    from app.core.learning import hydration as hydration_module
    from app.core import hydration_store

    target = "2026-05-26"
    in_window = "2026-05-26T10:00:00Z"
    _seed_conversation("proj-h", in_window, "Why is my BOQ empty?", "Looking now")
    _seed_conversation("proj-h", in_window, "rebar quantity for level 3", "120 kg")

    async def offline_chat(prompt, max_tokens=600):
        return ("_offline placeholder_", "offline_template")

    monkeypatch.setattr(hydration_module, "_call_chat", offline_chat)

    await hydration_module.run(target_date=target)

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
    from app.core.learning import hydration as hydration_module
    from app.core import hydration_store

    target = "2026-05-26"
    in_window = "2026-05-26T10:00:00Z"
    _seed_conversation("proj-l", in_window, "q", "a")

    async def real_chat(prompt, max_tokens=600):
        return ("## LLM-AUTHORED SUMMARY\n- one\n", "deepseek")

    monkeypatch.setattr(hydration_module, "_call_chat", real_chat)
    await hydration_module.run(target_date=target)

    row = hydration_store.get_latest("project", "proj-l")
    assert "LLM-AUTHORED SUMMARY" in row["summary_md"]
    assert "heuristic" not in row["summary_md"].lower()


def test_heuristic_friction_detection():
    """The friction-signal heuristic flags complaint language and repeats."""
    from app.core.learning.hydration import _user_friction_signals

    msgs = [
        {"role": "user", "content": "Why is this broken?"},
        {"role": "assistant", "content": "Looking now"},
        {"role": "user", "content": "Why is this broken?"},  # repeat of the prefix
    ]
    signals = _user_friction_signals(msgs)
    assert any("complaint" in s.lower() for s in signals)
    assert any("repeat" in s.lower() for s in signals)


def test_heuristic_top_keywords_skips_stopwords():
    from app.core.learning.hydration import _top_keywords

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
    from app.core.learning.hydration import _discover_gdrive_files

    monkeypatch.delenv("GDRIVE_PROJECT_FOLDERS", raising=False)
    monkeypatch.delenv("GDRIVE_SERVICE_ACCOUNT_JSON", raising=False)
    pid = _make_project("Q")
    count, errors = _discover_gdrive_files(pid)
    assert count == 0
    assert errors == []


def test_gdrive_mapping_without_key_reports_error(monkeypatch, isolated_data_dir):
    """Folder mapping set but key missing → recorded as a non-fatal error."""
    from app.core.learning.hydration import _discover_gdrive_files

    pid = _make_project("K")
    monkeypatch.setenv("GDRIVE_PROJECT_FOLDERS", f"{pid}:abc123")
    monkeypatch.delenv("GDRIVE_SERVICE_ACCOUNT_JSON", raising=False)
    count, errors = _discover_gdrive_files(pid)
    assert count == 0
    assert any("GDRIVE_SERVICE_ACCOUNT_JSON" in e for e in errors)


def test_gdrive_happy_path_attaches_and_dedupes(monkeypatch, isolated_data_dir):
    """With list/download stubbed, two new files get attached on the first
    pass; a second pass attaches zero (sidecar dedup)."""
    from app.core.learning import hydration as hydration_module
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
    from app.core.learning import hydration as hydration_module
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
    from app.core.learning import hydration as hydration_module
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


# ── Arbitrary local folders (LOCAL_PROJECT_FOLDERS) ────────────────────────


def test_local_folders_env_parsing(monkeypatch):
    """First-colon split keeps Windows-style paths intact; whitespace and
    empties are ignored; relative paths get resolved (warning logged)."""
    from app.core.learning.hydration import _parse_local_project_folders

    monkeypatch.setenv(
        "LOCAL_PROJECT_FOLDERS",
        " p1:/tmp/a, p2:/tmp/b , , p3:relative/path, malformed-entry",
    )
    m = _parse_local_project_folders()
    assert m["p1"] == "/tmp/a"
    assert m["p2"] == "/tmp/b"
    # Relative resolved to absolute (OS-normalised separator)
    assert os.path.isabs(m["p3"]) and m["p3"].endswith(os.path.normpath("relative/path"))
    assert "malformed-entry" not in m


def test_local_folders_attaches_recursively(tmp_path, monkeypatch, isolated_data_dir):
    """A configured folder gets walked recursively; allowed files attach,
    disallowed extensions and hidden files are skipped."""
    from app.core.learning.hydration import _discover_arbitrary_local_folders
    from app.core import projects as projects_store

    laptop = tmp_path / "MyProject"
    (laptop / "subdir").mkdir(parents=True)
    (laptop / "spec.pdf").write_bytes(b"%PDF-1.4")
    (laptop / "subdir" / "plan.dwg").write_bytes(b"\x00\x00")
    (laptop / "secret.exe").write_bytes(b"x")        # disallowed
    (laptop / ".hidden.pdf").write_bytes(b"x")        # dotfile
    (laptop / "subdir" / ".cache").mkdir()             # hidden subdir
    (laptop / "subdir" / ".cache" / "x.pdf").write_bytes(b"x")  # under hidden subdir

    pid = _make_project("L")
    monkeypatch.setenv("LOCAL_PROJECT_FOLDERS", f"{pid}:{laptop}")

    count, errors = _discover_arbitrary_local_folders(pid)
    assert count == 2, f"expected 2, got {count} (errors: {errors})"

    docs = projects_store.list_documents(pid)
    names = sorted(d["original_name"] for d in docs)
    assert names == ["plan.dwg", "spec.pdf"]


def test_local_folders_idempotent(tmp_path, monkeypatch, isolated_data_dir):
    """Second pass attaches zero — realpath compare against documents.file_path
    catches the dupes without any sidecar state."""
    from app.core.learning.hydration import _discover_arbitrary_local_folders

    laptop = tmp_path / "ProjectI"
    laptop.mkdir()
    (laptop / "spec.pdf").write_bytes(b"%PDF-1.4")

    pid = _make_project("I2")
    monkeypatch.setenv("LOCAL_PROJECT_FOLDERS", f"{pid}:{laptop}")

    first, _ = _discover_arbitrary_local_folders(pid)
    second, _ = _discover_arbitrary_local_folders(pid)
    assert first == 1 and second == 0


def test_local_folders_missing_folder_reports_error(tmp_path, monkeypatch, isolated_data_dir):
    from app.core.learning.hydration import _discover_arbitrary_local_folders

    pid = _make_project("M")
    monkeypatch.setenv("LOCAL_PROJECT_FOLDERS", f"{pid}:{tmp_path}/does-not-exist")
    count, errors = _discover_arbitrary_local_folders(pid)
    assert count == 0
    assert any("not found" in e for e in errors)


def test_local_folders_unconfigured_is_silent(monkeypatch, isolated_data_dir):
    from app.core.learning.hydration import _discover_arbitrary_local_folders

    monkeypatch.delenv("LOCAL_PROJECT_FOLDERS", raising=False)
    pid = _make_project("N")
    count, errors = _discover_arbitrary_local_folders(pid)
    assert count == 0 and errors == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require elevated privileges on Windows")
def test_local_folders_symlink_escape_blocked(tmp_path, monkeypatch, isolated_data_dir):
    """A symlink inside the configured folder that resolves outside it must
    NOT be attached — protects against `~/Documents/Project/leak -> /etc`."""
    from app.core.learning.hydration import _discover_arbitrary_local_folders

    laptop = tmp_path / "ProjectS"
    laptop.mkdir()
    # A normal file (should be attached) plus a symlink to an outside file
    # with an allowed extension (must NOT be attached).
    (laptop / "ok.pdf").write_bytes(b"%PDF-1.4")
    outside = tmp_path / "secrets.pdf"
    outside.write_bytes(b"%PDF-secret")
    os.symlink(str(outside), str(laptop / "leak.pdf"))

    pid = _make_project("S")
    monkeypatch.setenv("LOCAL_PROJECT_FOLDERS", f"{pid}:{laptop}")
    count, _errors = _discover_arbitrary_local_folders(pid)
    # Only the in-folder file should be attached; the symlink escape is dropped.
    assert count == 1
    from app.core import projects as projects_store
    names = [d["original_name"] for d in projects_store.list_documents(pid)]
    assert names == ["ok.pdf"]


def test_local_folders_oversize_recorded(tmp_path, monkeypatch, isolated_data_dir):
    from app.core.learning.hydration import _discover_arbitrary_local_folders

    laptop = tmp_path / "ProjectB"
    laptop.mkdir()
    (laptop / "big.pdf").write_bytes(b"x" * 500)

    pid = _make_project("B")
    monkeypatch.setenv("LOCAL_PROJECT_FOLDERS", f"{pid}:{laptop}")
    monkeypatch.setenv("HYDRATION_MAX_ATTACH_SIZE", "100")

    count, errors = _discover_arbitrary_local_folders(pid)
    assert count == 0
    assert any("oversize" in e for e in errors)


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


# ── The feedback loop — proves hydration actually changes tomorrow's chat ──


@pytest.mark.asyncio
async def test_hydration_writes_back_to_project_facts(isolated_data_dir, monkeypatch):
    """After a hydration pass, the project's project_facts table contains
    hydration:topics / friction / last_run rows — which the chat router
    surfaces via project_memory.build_memory_context."""
    from app.core import projects as projects_store, agent_memory
    from app.core.learning import hydration as hydration_module

    async def fake_chat(prompt, max_tokens=600):
        return ("## ok", "offline_template")
    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    pid = _make_project("WB-P")
    in_window = "2026-05-26T10:00:00Z"
    # Two complaint-style asks plus a topic-rich ask. With the friction regex
    # ("why|broken|empty|...") this should produce at least one friction signal.
    _seed_conversation_with_project(pid, in_window, "why is BOQ empty?", "looking")
    _seed_conversation_with_project(pid, in_window, "rebar quantities for level 3?", "120 kg/m3")

    await hydration_module.run(target_date="2026-05-26")

    facts = {f["key"]: f["value"] for f in projects_store.list_facts(pid)}
    assert "hydration:last_run" in facts
    assert facts["hydration:last_run"] == "2026-05-26"
    assert "hydration:topics" in facts  # at least one user-message keyword survived stopword filtering
    # 'why' / 'empty' should fire the friction regex on at least one msg
    assert "hydration:friction" in facts


@pytest.mark.asyncio
async def test_hydration_writes_back_to_agent_facts(isolated_data_dir, monkeypatch):
    """The runtime agent path (app/agents/runtime.py:548) reads
    agent_memory.list_agent_facts('chat', project_id). Hydration must
    populate that store so the runtime agent benefits next session."""
    import json
    from app.core import agent_memory
    from app.core.learning import hydration as hydration_module

    async def fake_chat(prompt, max_tokens=600):
        return ("## ok", "offline_template")
    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    pid = _make_project("WB-A")
    in_window = "2026-05-26T10:00:00Z"
    _seed_conversation_with_project(pid, in_window, "concrete pour schedule for foundation?", "tomorrow at 8")

    await hydration_module.run(target_date="2026-05-26")

    facts = agent_memory.list_agent_facts("chat", pid)
    keys = {f["key"] for f in facts}
    assert "hydration:last_brief" in keys, (
        "Expected hydration:last_brief in agent_facts so runtime.py's "
        "fact-loading loop picks it up next session"
    )
    blob_value = next(f["value"] for f in facts if f["key"] == "hydration:last_brief")
    payload = json.loads(blob_value)
    assert payload["run_date"] == "2026-05-26"
    assert "topics" in payload
    assert "asks" in payload
    # the user message should appear in the asks
    assert any("concrete" in a.lower() for a in payload["asks"])


@requires_construction_kit
@pytest.mark.asyncio
async def test_hydration_records_friction_patterns_on_learning_engine(
    isolated_data_dir, monkeypatch, tmp_path
):
    """Each friction signal becomes a row in learning_engine's patterns
    corpus, durable across hydration runs."""
    from app.blocks import BLOCK_REGISTRY
    from app.core.learning import hydration as hydration_module

    # Isolate learning_engine state per test
    monkeypatch.setenv("LEARNING_ENGINE_STORAGE", str(tmp_path / "le_state.json"))

    async def fake_chat(prompt, max_tokens=600):
        return ("## ok", "offline_template")
    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    pid = _make_project("WB-L")
    in_window = "2026-05-26T10:00:00Z"
    _seed_conversation_with_project(pid, in_window, "why is the export broken?", "investigating")
    _seed_conversation_with_project(pid, in_window, "why is the export broken?", "still")  # repeat → friction

    await hydration_module.run(target_date="2026-05-26")

    cls = BLOCK_REGISTRY["learning_engine"]
    le = cls()
    envelope = await le.execute(
        {"operation": "list_patterns", "project_id": pid, "category": "friction"}, {}
    )
    inner = envelope["result"]
    assert inner["status"] == "success"
    assert inner["count"] >= 1, "Expected at least one friction pattern recorded"


@pytest.mark.asyncio
async def test_writeback_failures_are_non_fatal(isolated_data_dir, monkeypatch):
    """If any writeback target raises, the per-project row still gets written
    and the writeback errors are recorded in facts.writeback.skipped."""
    from app.core import projects as projects_store, hydration_store
    from app.core.learning import hydration as hydration_module

    async def fake_chat(prompt, max_tokens=600):
        return ("## ok", "offline_template")
    monkeypatch.setattr(hydration_module, "_call_chat", fake_chat)

    # Break project_facts writeback by pointing set_fact at a raiser
    def boom(*a, **k):
        raise RuntimeError("simulated writeback failure")
    monkeypatch.setattr(projects_store, "set_fact", boom)

    pid = _make_project("WB-F")
    _seed_conversation_with_project(pid, "2026-05-26T10:00:00Z", "ask?", "answer")
    await hydration_module.run(target_date="2026-05-26")

    row = hydration_store.get_latest("project", pid)
    assert row is not None
    skipped = row["facts"].get("writeback", {}).get("skipped", [])
    assert any("simulated writeback failure" in s for s in skipped)


def _seed_conversation_with_project(project_id: str, ts: str, user_msg: str, assistant_msg: str) -> str:
    """Like _seed_conversation but assumes the project row already exists.
    Used by the writeback tests which create real projects via _make_project."""
    return _seed_conversation(project_id, ts, user_msg, assistant_msg)
