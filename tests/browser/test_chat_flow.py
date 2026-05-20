"""Browser tests for the chat flow — typing, sending, error toast."""

import pytest


def test_send_message_streams_to_outcomes_panel(app_page, browser_console, browser_network):
    """Typing + send shows the user message and updates the outcomes panel.

    Without DEEPSEEK_API_KEY configured, the assistant turn will surface a typed
    error via surfaceError — that's fine; we're testing the wire-up, not the LLM.
    """
    app_page.locator("#textInput").fill("hello")
    app_page.locator("#sendBtn").click()
    app_page.wait_for_timeout(2000)

    user_messages = app_page.locator("#messages .msg.user").count()
    assert user_messages >= 1

    # Either a streamed answer arrived OR a clean error toast appeared.
    has_answer = app_page.locator("#messages .msg.assistant").count() >= 1
    has_toast = app_page.locator("#toastStack > div").count() >= 1
    assert has_answer or has_toast


def test_plain_question_opens_no_panel(app_page):
    """A plain chat question gets a plain reply — the side panel stays closed.

    Roadmap V2 · Epic 4: the always-on results dashboard is removed; the UI
    reads as a chatbot. The artifacts panel opens only when a reply carries an
    artifact, never on an ordinary question.
    """
    app_page.locator("#textInput").fill("a plain question")
    app_page.locator("#sendBtn").click()
    app_page.wait_for_timeout(2000)

    panel = app_page.locator("#outcomesPanel")
    assert not panel.evaluate("el => el.classList.contains('open')")
    assert panel.evaluate("el => getComputedStyle(el).display") == "none"


def test_clear_chat_resets_history(app_page):
    """🗑 button clears the chat and outcomes panels."""
    app_page.locator("#textInput").fill("disposable")
    app_page.locator("#sendBtn").click()
    app_page.wait_for_timeout(1000)

    app_page.locator("#resetBtn").click()
    app_page.wait_for_timeout(300)

    # After reset, only the welcome bubble remains.
    msgs = app_page.locator("#messages .msg").count()
    assert msgs <= 1

    outcomes_html = app_page.locator("#outcomes").inner_html()
    assert outcomes_html.strip() == "" or "Latest answer" not in outcomes_html


def test_history_persists_across_reload(app_page, app_server):
    """Chat history is saved to localStorage and rehydrated on page reload."""
    app_page.locator("#textInput").fill("remember me")
    app_page.locator("#sendBtn").click()
    app_page.wait_for_timeout(1500)

    app_page.goto(f"{app_server}/", wait_until="networkidle")
    app_page.wait_for_timeout(500)

    # The "remember me" message should still be there after reload.
    body = app_page.locator("#messages").inner_text()
    assert "remember me" in body
