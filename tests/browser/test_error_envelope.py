"""Browser-level tests for the unified error envelope (the API skill commit)."""

import pytest


def test_validation_error_in_browser(app_page, app_server):
    """A 422 from /v1/execute carries the typed envelope into the page's Error object."""
    result = app_page.evaluate(
        """async () => {
            try {
                // Send an invalid body — missing 'block' field
                await client._request('POST', '/v1/execute', { badfield: 1 });
                return { ok: true };
            } catch (e) {
                return { code: e.code, status: e.status, message: e.message };
            }
        }"""
    )
    assert result.get("status") == 422
    assert result.get("code") == "VALIDATION_ERROR"


def test_not_found_envelope(app_page):
    """Hitting a missing agent surfaces NOT_FOUND."""
    result = app_page.evaluate(
        """async () => {
            try {
                await client._request('GET', '/v1/agents/totally-missing');
                return { ok: true };
            } catch (e) {
                return { code: e.code, status: e.status };
            }
        }"""
    )
    assert result.get("status") == 404
    assert result.get("code") == "NOT_FOUND"


def test_surface_error_renders_toast(app_page):
    """surfaceError() actually appends to #toastStack."""
    app_page.evaluate("surfaceError(new Error('test toast'), 'unit-test')")
    app_page.wait_for_timeout(100)
    toasts = app_page.locator("#toastStack > div").count()
    assert toasts >= 1
    text = app_page.locator("#toastStack > div").last.inner_text()
    assert "test toast" in text
    assert "unit-test" in text
