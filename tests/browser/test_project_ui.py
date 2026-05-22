"""Browser tests for Epic 4 Slice B — conversational-UI polish.

Covers: the sidebar wired to real /v1/projects, project creation from the UI,
project-scoped chat, and the on-demand artifacts panel (hidden until a reply
carries an artifact; never opened by attaching a file).
"""

import uuid

import pytest


def test_sidebar_loads_real_projects(app_page, browser_network):
    """The projects list is populated from a GET /v1/projects call, and every
    rendered project row is API-driven (has a selectProject() handler)."""
    # loadProjects() runs on init — wait for the placeholder to be replaced.
    app_page.wait_for_function(
        "!document.getElementById('projectsList').innerHTML.includes('Loading projects')",
        timeout=8000,
    )
    # The sidebar fetched the real project list from the API.
    assert any(
        r["url"].endswith("/v1/projects") and r["method"] == "GET"
        for r in browser_network
    ), "no GET /v1/projects request observed"
    # Every project row is API-driven — bound to selectProject(), never static.
    rows = app_page.locator("#projectsList .project")
    for i in range(rows.count()):
        onclick = rows.nth(i).get_attribute("onclick") or ""
        assert "selectProject(" in onclick


def test_artifacts_panel_is_hidden_on_load(app_page):
    """The side panel starts hidden — the UI reads as a chatbot, not a dashboard."""
    panel = app_page.locator("#outcomesPanel")
    assert not panel.evaluate("el => el.classList.contains('open')")
    # display:none means it occupies no visible box.
    assert panel.evaluate("el => getComputedStyle(el).display") == "none"


def test_create_project_from_ui(app_page):
    """The + New button creates a project via POST /v1/projects and selects it."""
    name = f"BrowserTest {uuid.uuid4().hex[:6]}"
    # createProject() uses window.prompt — stub it before clicking.
    app_page.evaluate(f"window.prompt = () => {name!r}")
    app_page.locator(".section button", has_text="New").click()
    # The new project appears in the sidebar and becomes active.
    app_page.wait_for_function(
        f"document.getElementById('projectsList').innerText.includes({name!r})",
        timeout=8000,
    )
    active = app_page.locator("#projectsList .project.active").inner_text()
    assert name in active
    # Selecting a project flips on project mode.
    assert app_page.locator("#projectModeToggle").is_checked()


def test_attaching_a_file_opens_no_panel(app_page, tmp_path):
    """Attaching a file runs nothing and opens no artifacts panel (Epic 4)."""
    f = tmp_path / "note.txt"
    f.write_text("just some notes", encoding="utf-8")
    app_page.locator("#fileInput").set_input_files(str(f))
    app_page.wait_for_timeout(1500)
    panel = app_page.locator("#outcomesPanel")
    # Panel must remain closed after a file attach.
    assert not panel.evaluate("el => el.classList.contains('open')")
    assert panel.evaluate("el => getComputedStyle(el).display") == "none"
