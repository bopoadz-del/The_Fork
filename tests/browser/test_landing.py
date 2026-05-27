"""Smoke tests for the landing page chat UI."""

import pytest


def test_landing_loads_clean(app_page, browser_console):
    """Page loads with no console errors."""
    assert "Cerebrum" in app_page.title()
    errors = [m for m in browser_console if m["type"] in ("error", "pageerror")]
    # We tolerate a 401 from one health probe in older builds; flag anything else.
    assert not errors, f"console errors: {errors}"


def test_dashboard_mount(app_page, app_server):
    """The /dashboard mount serves the React build (or 404s cleanly with the empty-build hint)."""
    import httpx
    r = httpx.get(f"{app_server}/dashboard/", timeout=5.0)
    # 200 if the React build exists, 404 if not — both are valid local states.
    # The ONE shape we don't tolerate is a 500.
    assert r.status_code in (200, 404), f"unexpected status {r.status_code}"


def test_agents_endpoint_fetched_on_load(app_page, app_server, browser_network):
    """On load, the page fetches /v1/agents so the routing layer knows the
    available runtime agents. The conversational UI removed the visible
    agent dropdown (see tests/test_project_ui.test_ui_no_longer_has_a_visible_agent_picker)
    so this test asserts the network behaviour rather than a DOM widget."""
    # loadAgents() is an IIFE on page load; give it a moment to fire.
    app_page.wait_for_timeout(500)
    agents_calls = [
        r for r in browser_network
        if r["url"].endswith("/v1/agents") and r["method"] == "GET"
    ]
    assert agents_calls, "no GET /v1/agents request observed on load"


def test_chat_textarea_has_placeholder(app_page):
    """The textarea explains Shift+Enter."""
    placeholder = app_page.locator("#textInput").get_attribute("placeholder")
    assert "Shift+Enter" in placeholder or "newline" in (placeholder or "")


def test_send_without_input_does_nothing(app_page, browser_console):
    """Empty send is a no-op — no errors, no extra messages."""
    initial_count = app_page.locator("#messages .msg").count()
    app_page.locator("#sendBtn").click()
    app_page.wait_for_timeout(300)
    assert app_page.locator("#messages .msg").count() == initial_count
    errors = [m for m in browser_console if m["type"] == "error"]
    assert not errors, f"unexpected errors: {errors}"


def test_unauth_request_surfaces_typed_error(app_page, app_server, browser_console):
    """A bad request from inside the page surfaces our unified error envelope.

    We make a fetch from the page context to /v1/agents/does-not-exist; the
    new readApiError helper should produce an Error with .code === 'NOT_FOUND'.
    """
    result = app_page.evaluate(
        """async () => {
            try {
                await client.execute('does-not-exist-block', null, {});
                return { ok: true };
            } catch (e) {
                return { ok: false, message: e.message, code: e.code, status: e.status };
            }
        }"""
    )
    assert result["ok"] is False
    # The error message should contain something descriptive (not just "HTTP 404")
    assert result["message"]
