"""Smoke checks for the project-chat UI — Reasoning Engine Plan 6
and Roadmap V2 · Epic 4 Slice B (conversational-UI polish)."""

from pathlib import Path

import pytest

_HTML = Path("app/static/index.html").read_text(encoding="utf-8")


# ── Plan 6 — project reasoning wiring ───────────────────────────────────────
#
# The project-mode toggle and agent picker were intentionally removed from the
# UI (the header now shows only the title). Reason: a returning user who had
# left "Project mode" on, or who selected a sidebar project, would be quietly
# routed to /v1/project/ask, which only knows about loaded schedules — not
# just-uploaded files. The fix was to remove the path entirely and route every
# message through the default chat (which sees activeFileContext).
#
# The `askProject`, `projectMode`, `projectSessionId` and `/v1/project/ask`
# symbols still exist in the JS for backward-compat — they're just not wired
# to any UI control anymore. The tests below assert that scaffold is still
# present so a future re-introduction of project-mode routing has something to
# hook into.


def test_ui_no_longer_has_a_visible_project_mode_toggle():
    """The header-level toggle must be gone — see file header comment."""
    assert 'id="projectModeToggle"' not in _HTML


def test_ui_no_longer_has_a_visible_agent_picker():
    """The agent dropdown must be gone — auto-routing replaces it."""
    assert 'id="agentPicker"' not in _HTML


def test_ui_has_an_askProject_function():
    assert "function askProject" in _HTML or "askProject =" in _HTML


def test_ui_posts_to_the_project_endpoint():
    assert "/v1/project/ask" in _HTML


def test_ui_generates_a_project_session_id():
    assert "projectSessionId" in _HTML


def test_sendMessage_still_has_project_mode_branch():
    # The branch in sendMessage is now dead code (projectMode never flips true
    # without the UI control), but the scaffold remains for future re-wiring.
    assert "askProject" in _HTML and "projectMode" in _HTML


# ── Epic 4 Slice B — sidebar wired to real projects ─────────────────────────

def test_sidebar_no_longer_hardcodes_demo_projects():
    # The hardcoded Diriyah/Qiddam/KAUST décor must be gone.
    assert "Diriyah Phase 1" not in _HTML
    assert "Qiddam Tower" not in _HTML
    assert "KAUST Lab" not in _HTML


def test_ui_fetches_the_real_projects_list():
    # Sidebar is populated from GET /v1/projects.
    assert "/v1/projects" in _HTML
    assert "function loadProjects" in _HTML
    assert "function renderProjects" in _HTML


def test_ui_can_create_a_project():
    # Project creation via POST /v1/projects from the sidebar.
    assert "function createProject" in _HTML
    assert "method: 'POST'" in _HTML  # createProject posts to /v1/projects


def test_ui_has_a_new_project_button():
    assert "createProject()" in _HTML


def test_selecting_a_project_scopes_the_chat():
    # selectProject sets activeProjectId; askProject uses it as the session id.
    assert "function selectProject" in _HTML
    assert "activeProjectId" in _HTML
    assert "activeProjectId || projectSessionId" in _HTML


# ── Epic 4 Slice B — on-demand artifacts panel, no dashboard ────────────────

def test_side_panel_is_hidden_by_default():
    # The artifacts panel must start hidden (display:none) — not an always-on
    # dashboard. It opens only when a reply carries an artifact.
    assert ".outcomes { " in _HTML and "display: none;" in _HTML
    assert ".outcomes.open { display: block; }" in _HTML


def test_panel_opens_on_demand_only():
    # Helpers exist to open/close the panel explicitly.
    assert "function openArtifactPanel" in _HTML
    assert "function closeArtifactPanel" in _HTML
    assert "function showArtifacts" in _HTML


def test_artifacts_panel_opens_when_reply_carries_artifacts():
    # askProject opens the panel only when data.artifacts is non-empty.
    assert "showArtifacts(data.artifacts" in _HTML


def test_plain_chat_does_not_open_the_panel():
    # The plain chat onDone handler must NOT push a 'Latest answer' dashboard
    # card — a plain question stays a plain reply in the thread.
    assert "Latest answer" not in _HTML


def test_panel_has_a_close_button():
    assert "closeArtifactPanel()" in _HTML


# ── Task 6 — Connect Drive modal + Drive file browser ───────────────────────

def test_ui_has_drive_status_check():
    html = open("app/static/index.html", encoding="utf-8").read()
    assert "/v1/drive/status" in html
    assert "/v1/drive/connect" in html
    assert "/v1/drive/files" in html
    assert "/drive/import" in html
