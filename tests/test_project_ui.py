"""Smoke checks for the project-chat UI — Reasoning Engine Plan 6."""

from pathlib import Path

import pytest

_HTML = Path("app/static/index.html").read_text(encoding="utf-8")


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
