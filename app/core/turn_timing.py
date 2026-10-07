"""Where a turn's time goes, from the moment the request arrived.

The old TIMING lines started the clock after retrieval and pre-dispatch, so on
live a turn read "2.9 s" while the user waited 18.9 s for the first token. The
chat endpoint marks arrival first thing; every stage then logs

    TIMING turn stage=<name> dur=<seconds> at=<seconds since arrival>

(queue, retrieval, predispatch, postprocess, first_token, ...), and the model
calls' existing ``cum=`` figures count from arrival too. Lines are written
when ``AGENT_TIMING_LOG`` is on, like the other TIMING lines.
"""
from __future__ import annotations

import contextvars
import logging
import os
import time
from contextlib import contextmanager
from typing import Iterator, Optional

_LOG = logging.getLogger("app.agents.runtime")
_STATE: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("turn_timing", default=None)


def _on() -> bool:
    return (os.getenv("AGENT_TIMING_LOG") or "").strip().lower() in ("1", "true", "yes", "on")


def mark_arrival() -> None:
    """Start this turn's clock (call first thing in the request handler)."""
    now = time.monotonic()
    _STATE.set({"t0": now, "last": now, "first_token": False})


def arrived_at() -> Optional[float]:
    st = _STATE.get()
    return st["t0"] if st else None


def since_arrival() -> Optional[float]:
    st = _STATE.get()
    return time.monotonic() - st["t0"] if st else None


def log_stage(name: str, dur: float) -> None:
    if not _on():
        return
    at = since_arrival()
    _LOG.warning("TIMING turn stage=%s dur=%.2fs at=%s", name, dur,
                 f"{at:.2f}s" if at is not None else "n/a")


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time a block (it may contain awaits) and log it as one stage."""
    t = time.monotonic()
    try:
        yield
    finally:
        now = time.monotonic()
        st = _STATE.get()
        if st is not None:
            st["last"] = now
        log_stage(name, now - t)


def mark(name: str) -> None:
    """Log the time since the previous stage or mark as stage ``name``."""
    st = _STATE.get()
    if st is None:
        return
    now = time.monotonic()
    dur, st["last"] = now - st["last"], now
    log_stage(name, dur)


def first_token() -> None:
    """Log the first token of this turn's answer (once)."""
    st = _STATE.get()
    if st is None or st["first_token"]:
        return
    st["first_token"] = True
    log_stage("first_token", 0.0)
