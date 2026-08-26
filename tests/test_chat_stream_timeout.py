"""``chat_stream`` wall-clock timeout + heartbeat + idle watchdog.

The hang scenario: the LLM provider drops the response mid-flight (Cloudflare
tunnel ephemeral, Ollama OOM, DeepSeek 503), ``httpx.post`` blocks past its own
timeout, and the agent loop sits stuck — the frontend reader waits forever on
``reader.read()`` because no SSE bytes arrive. The browser UX is an indefinite
spinner with no error.

The fix wraps ``_chat_stream_impl`` in a producer task that pushes events into
an ``asyncio.Queue``; a heartbeat task injects ``{"type": "heartbeat"}`` after
each ``CHAT_STREAM_HEARTBEAT_SECONDS`` of silence; the consumer reads with an
ABSOLUTE deadline of ``CHAT_STREAM_TIMEOUT_SECONDS`` (computed once, NOT reset
by events). When the deadline expires before the producer finishes, a
structured error event is emitted and the wrapper returns cleanly.

A7 idle-between-progress: heartbeats alone must not keep a hung turn spinning.
``CHAT_TURN_IDLE_TIMEOUT_SEC`` (default 120; ``0`` disables) ends the turn with
a visible timeout error when no *progress* event (token / tool_call / …)
arrives — heartbeats do not reset that clock.

Tests use short timeouts (1-3s) so the suite still runs in seconds, not the
production-default 240 / 120.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import patch

from app.agents.runtime import Agent


def _agent() -> Agent:
    return Agent(
        name="timeout-test",
        description="timeout test",
        system_prompt="x",
        allowed_blocks=[],
    )


def _collect(gen: AsyncIterator[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drain an async generator into a list of events."""
    async def _run():
        return [event async for event in gen]
    return asyncio.run(_run())


def _setup_provider(monkeypatch) -> None:
    """Bypass the api-key gate so the agent loop actually starts."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")


# ── test 1: hung LLM → wall-clock timeout fires ──────────────────────────────


def test_hung_llm_yields_timeout_error(monkeypatch):
    """When ``_call_llm`` never returns, the wall-clock deadline must trigger
    a structured error event whose message contains the substring "timeout"
    (so the frontend's friendlyErrorMessage maps it correctly)."""
    _setup_provider(monkeypatch)
    monkeypatch.setenv("CHAT_STREAM_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("CHAT_STREAM_HEARTBEAT_SECONDS", "1")

    a = _agent()

    async def _hang(*_args, **_kwargs):
        # Never resolves — simulates an upstream LLM that's accepted the
        # request and then went silent (no response, no error). Without the
        # wall-clock cap the agent loop sits here indefinitely.
        await asyncio.Future()

    start = asyncio.get_event_loop().time() if False else None  # not needed; pytest timeout will catch infinite loops

    with patch.object(a, "_call_llm", _hang):
        events = _collect(a.chat_stream(user_message="hello"))

    types = [e["type"] for e in events]
    assert "error" in types, f"timeout did not surface as error event: {types}"

    err = next(e for e in events if e["type"] == "error")
    # Substring "timeout" — friendlyErrorMessage matches r.includes('timeout').
    assert "timeout" in err["message"].lower(), (
        f"error message missing 'timeout' substring: {err['message']!r}"
    )

    # At least one heartbeat should have fired during the 2s wait — the
    # heartbeat task tick is 1s so by deadline expiry we expect ~1-2 of them.
    assert any(e["type"] == "heartbeat" for e in events), (
        f"no heartbeats during 2s hang: {types}"
    )


# ── test 2: slow LLM (~3s) → heartbeats appear, stream still completes ───────


def test_slow_llm_emits_heartbeats(monkeypatch):
    """A slow LLM (3s) under a generous 10s timeout with a 1s heartbeat
    should emit at least one heartbeat event AND still complete with an
    ``end`` event — no timeout error."""
    _setup_provider(monkeypatch)
    monkeypatch.setenv("CHAT_STREAM_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("CHAT_STREAM_HEARTBEAT_SECONDS", "1")

    a = _agent()

    async def _slow(*_args, **_kwargs):
        await asyncio.sleep(3)
        return {
            "status": "success",
            "choice": {"message": {"content": "the answer", "tool_calls": []}},
        }

    with patch.object(a, "_call_llm", _slow):
        events = _collect(a.chat_stream(user_message="hello"))

    types = [e["type"] for e in events]
    assert any(t == "heartbeat" for t in types), (
        f"slow LLM did not produce any heartbeat events: {types}"
    )
    assert "end" in types, f"slow LLM did not complete cleanly: {types}"
    assert "error" not in types, f"slow LLM erroneously errored out: {types}"


# ── test 3: fast LLM → no heartbeats, clean completion ────────────────────────


def test_fast_llm_completes_cleanly_with_no_heartbeats(monkeypatch):
    """A fast LLM (returns immediately) under a 1s heartbeat interval should
    complete before any heartbeat fires — output stream contains zero
    heartbeat events."""
    _setup_provider(monkeypatch)
    monkeypatch.setenv("CHAT_STREAM_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("CHAT_STREAM_HEARTBEAT_SECONDS", "1")

    a = _agent()

    async def _fast(*_args, **_kwargs):
        # No sleep at all — returns on the next event-loop tick.
        return {
            "status": "success",
            "choice": {"message": {"content": "instant reply", "tool_calls": []}},
        }

    with patch.object(a, "_call_llm", _fast):
        events = _collect(a.chat_stream(user_message="hello"))

    types = [e["type"] for e in events]
    assert "end" in types, f"fast LLM did not finish with end: {types}"
    assert all(t != "heartbeat" for t in types), (
        f"fast LLM produced heartbeats it shouldn't have: {types}"
    )
    assert "error" not in types, f"fast LLM erroneously errored: {types}"


# ── test 4: heartbeat-only hang → idle watchdog fires before wall-clock ───────


def test_idle_watchdog_fires_when_only_heartbeats(monkeypatch):
    """A hung LLM that only emits heartbeats must surface a visible timeout
    error via the idle-between-progress watchdog — not an indefinite spinner.

    Wall-clock is deliberately generous so idle (not wall) is what fires.
    """
    _setup_provider(monkeypatch)
    monkeypatch.setenv("CHAT_STREAM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("CHAT_STREAM_HEARTBEAT_SECONDS", "0.4")
    monkeypatch.setenv("CHAT_TURN_IDLE_TIMEOUT_SEC", "1.5")

    a = _agent()

    async def _hang(*_args, **_kwargs):
        await asyncio.Future()

    with patch.object(a, "_call_llm", _hang):
        events = _collect(a.chat_stream(user_message="hello"))

    types = [e["type"] for e in events]
    assert "error" in types, f"idle stall did not surface as error: {types}"
    err = next(e for e in events if e["type"] == "error")
    assert "timeout" in err["message"].lower(), err["message"]
    assert "idle" in err["message"].lower() or "progress" in err["message"].lower(), (
        f"expected idle/progress wording: {err['message']!r}"
    )
    assert any(e["type"] == "heartbeat" for e in events), (
        f"expected heartbeats during idle hang: {types}"
    )
    # Must not have waited for the 30s wall-clock.
    assert "wall-clock" not in err["message"].lower(), err["message"]


# ── test 5: progress events reset idle — slow-but-alive stream completes ─────


def test_progress_events_reset_idle_watchdog(monkeypatch):
    """Tool/progress events must reset the idle clock so a healthy multi-step
    turn spanning longer than the idle window still completes."""
    _setup_provider(monkeypatch)
    monkeypatch.setenv("CHAT_STREAM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("CHAT_STREAM_HEARTBEAT_SECONDS", "10")
    monkeypatch.setenv("CHAT_TURN_IDLE_TIMEOUT_SEC", "1.0")

    a = _agent()

    async def _progressing_impl(**_kwargs):
        # Total wall ~2.4s exceeds idle 1.0s; progress every 0.7s resets idle.
        yield {"type": "start", "agent": "timeout-test"}
        await asyncio.sleep(0.7)
        yield {"type": "tool_call", "tool": "search_project_documents",
               "args_preview": "{}"}
        await asyncio.sleep(0.7)
        yield {"type": "tool_result", "tool": "search_project_documents",
               "ok": True}
        await asyncio.sleep(0.7)
        yield {"type": "token", "content": "grounded answer"}
        yield {"type": "end", "iterations": 1, "sources": [], "tools": [
            "search_project_documents",
        ]}

    with patch.object(a, "_chat_stream_impl", _progressing_impl):
        events = _collect(a.chat_stream(user_message="hello"))

    types = [e["type"] for e in events]
    assert "end" in types, f"healthy progressing stream did not complete: {types}"
    assert "error" not in types, f"progressing stream wrongly errored: {types}"
    assert any(e.get("content") == "grounded answer" for e in events
               if e.get("type") == "token")


def test_idle_watchdog_disabled_by_zero(monkeypatch):
    """CHAT_TURN_IDLE_TIMEOUT_SEC=0 disables idle; only wall-clock applies."""
    _setup_provider(monkeypatch)
    monkeypatch.setenv("CHAT_STREAM_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("CHAT_STREAM_HEARTBEAT_SECONDS", "0.4")
    monkeypatch.setenv("CHAT_TURN_IDLE_TIMEOUT_SEC", "0")

    a = _agent()

    async def _hang(*_args, **_kwargs):
        await asyncio.Future()

    with patch.object(a, "_call_llm", _hang):
        events = _collect(a.chat_stream(user_message="hello"))

    err = next(e for e in events if e["type"] == "error")
    assert "wall-clock" in err["message"].lower(), err["message"]
