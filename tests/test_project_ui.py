"""Smoke checks for the project-chat UI — Reasoning Engine Plan 6
and Roadmap V2 · Epic 4 Slice B (conversational-UI polish)."""

from pathlib import Path

import pytest

_HTML = Path("app/static/index.html").read_text(encoding="utf-8")


# ── Plan 6 — project reasoning wiring ───────────────────────────────────────

def test_ui_has_a_project_mode_toggle():
    assert 'id="projectModeToggle"' in _HTML


def test_ui_has_an_askProject_function():
    assert "function askProject" in _HTML or "askProject =" in _HTML


def test_ui_posts_to_the_project_endpoint():
    assert "/v1/project/ask" in _HTML


def test_ui_generates_a_project_session_id():
    assert "projectSessionId" in _HTML


def test_sendMessage_routes_to_project_mode():
    # sendMessage must branch to askProject when project mode is on.
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
