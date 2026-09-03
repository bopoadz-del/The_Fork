"""A turn ends with an answer or with a reason — never with silence.

F-SILENT-1, ``FLEET_OPS/artifacts/gate_battery_13b2bf7_2026-08-31.md``,
quoted verbatim:

    a turn that ends with neither an answer nor an error (B6, first pass).
    119 seconds, three events (``start``, ``route``, ``start``), then
    nothing: no tokens, no ``end`` content, no error, HTTP 200. Recorded
    even though the retest passed, because a silent zero-output turn is the
    failure mode #456/#462 were built to end, arriving by another door.

WHY THE EXISTING GUARD DID NOT CATCH IT. ``Agent.chat_stream`` already wraps
``_chat_stream_impl`` with a wall-clock deadline, a heartbeat and a trailing
safety net that synthesises ``end``. That guard begins INSIDE the agent. B6's
last event was the agent's own ``start``, and the turn died with the router's
generator still holding the connection -- past ``route``, outside the agent's
net. Any path the router takes that is not ``Agent.chat_stream`` (the
predefined-workflow path, a routing branch that returns early, an exception
in the router's own frame) has no guard at all.

So the guard is applied where the CONNECTION is, at the SSE boundary, which
is the only place that can see the whole turn. It is deliberately a second
net rather than a replacement: its default timeout sits ABOVE the agent's, so
on the agent path the agent's own structured timeout still wins and this one
never fires. It exists for the paths that have nothing.

WHAT IT WILL NOT DO. It cannot make a stalled turn answer. It converts an
unexplained silence into a named failure, which is the difference between a
bug you can find and a bug you cannot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import AsyncIterator, Callable, Optional

_LOG = logging.getLogger(__name__)

#: SSE event types that legitimately end a turn.
TERMINAL_TYPES = frozenset({"end", "error"})

#: Shown in the bubble so a failed turn is never an empty one.
SILENT_TURN_FALLBACK = (
    "The assistant did not produce an answer for this turn."
)

DEFAULT_WATCHDOG_SECONDS = 300.0


def watchdog_enabled() -> bool:
    """ON by default; ``CHAT_SSE_WATCHDOG=0`` is the kill switch."""
    return str(os.getenv("CHAT_SSE_WATCHDOG", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )


def watchdog_seconds() -> float:
    """Wall clock for a whole turn at the SSE boundary.

    Above ``CHAT_STREAM_TIMEOUT_SECONDS`` (the agent's own, default 240) on
    purpose: where the agent has a guard, the agent's structured timeout must
    be what the user sees. A bad value falls back to the default rather than
    breaking the stream -- a watchdog that raises is worse than none.
    """
    try:
        value = float(os.getenv("CHAT_SSE_WATCHDOG_SECONDS") or DEFAULT_WATCHDOG_SECONDS)
    except (TypeError, ValueError):
        return DEFAULT_WATCHDOG_SECONDS
    return value if value > 0 else DEFAULT_WATCHDOG_SECONDS


def frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def event_type(raw: str) -> Optional[str]:
    """The ``type`` of one SSE frame, or None when it is not one we wrote.

    Tolerant on purpose: a heartbeat comment, a keep-alive newline or a frame
    from another producer must not make the watchdog think a turn ended.
    """
    if not raw:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[len("data:"):].strip()
        if not body or body == "[DONE]":
            return None
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if isinstance(data, dict):
            got = data.get("type")
            return str(got) if got is not None else None
    return None


async def guarantee_terminal(
    frames: AsyncIterator[str],
    *,
    request_id: str = "",
    timeout_s: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[str]:
    """Pass frames through; guarantee the turn ends with ``end`` or ``error``.

    Three exits, all named:

    * the producer finishes having emitted a terminal frame -- nothing added;
    * the producer finishes WITHOUT one (B6) -- a token fallback if the
      bubble is still empty, then an ``error`` naming the last event seen,
      then ``end``;
    * the wall clock runs out -- the same shape, naming the timeout.

    The last-event name is carried because "no answer and no error" is not
    actionable while "ended after ``start`` with no answer" says where to
    look. B6's answer to that question was ``start``.
    """
    if not watchdog_enabled():
        async for raw in frames:
            yield raw
        return

    limit = watchdog_seconds() if timeout_s is None else timeout_s
    deadline = clock() + limit
    saw_terminal = False
    saw_token = False
    last_type = "nothing"
    iterator = frames.__aiter__()

    def _close(reason: str) -> list[str]:
        out: list[str] = []
        if not saw_token:
            out.append(frame({"type": "token", "content": SILENT_TURN_FALLBACK}))
        out.append(frame({
            "type": "error",
            "message": reason,
            "request_id": request_id,
        }))
        out.append(frame({"type": "end", "iterations": 0, "sources": [],
                          "tools": []}))
        return out

    try:
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                _LOG.warning(
                    "sse watchdog: turn exceeded %.0fs after %r (request_id=%s)",
                    limit, last_type, request_id,
                )
                for out in _close(
                    "Response timeout — the turn produced no answer within "
                    "%.0fs; the last event was %r." % (limit, last_type)
                ):
                    yield out
                saw_terminal = True
                return
            try:
                raw = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                continue  # loop top decides, so the deadline stays absolute
            kind = event_type(raw)
            if kind:
                last_type = kind
                if kind == "token":
                    saw_token = True
                if kind in TERMINAL_TYPES:
                    saw_terminal = True
            yield raw
    # No `except asyncio.CancelledError: raise` clause here, deliberately: on
    # Python 3.8+ CancelledError inherits from BaseException, not Exception,
    # so the handler below CANNOT swallow it and a client that went away
    # still cancels cleanly. Verified rather than assumed -- and fenced, so
    # that widening this to `except BaseException` (which WOULD answer an
    # absent client and eat the cancellation) fails a test.
    except Exception as exc:  # noqa: BLE001 - the last net must not have holes
        _LOG.exception("sse watchdog: producer escaped (request_id=%s)", request_id)
        for out in _close(
            "The assistant is temporarily unavailable. Please try again. "
            "(%s after %r)" % (type(exc).__name__, last_type)
        ):
            yield out
        return

    if not saw_terminal:
        _LOG.warning(
            "sse watchdog: turn ended with no terminal event after %r "
            "(request_id=%s)", last_type, request_id,
        )
        for out in _close(
            "The turn ended after %r with no answer and no error. This is a "
            "platform fault, not a refusal — please retry." % last_type
        ):
            yield out
