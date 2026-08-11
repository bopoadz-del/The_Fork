"""Startup must open the port before loading the heavy models.

2026-08-11: the service could not boot on a 512Mi instance. The lifespan
warm-loaded the bge-small embedder inline, BEFORE FastAPI opened the port, and
with no persistent HF cache that warm-up also downloads the model. Startup
outran the platform's port-scan window ("No open ports detected"), the platform
restarted the container while the first boot was still loading, and two
processes each holding torch + a transformer exceeded the instance memory. The
kill looked like a memory leak; the cause was a slow startup.

These tests pin the fix: warm-up is a background task by default, so a slow or
failing model load can never keep the app from becoming reachable.
"""
from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


def test_a_hanging_model_load_does_not_prevent_serving(monkeypatch):
    """The defect shape: if warm-up blocks, the app never becomes reachable.

    The assertion that discriminates old from new is the ELAPSED TIME to finish
    startup. Inline warm-up cannot complete startup until the (here: 30s) model
    load returns, so this fails on the pre-fix code rather than merely running
    slower. Background warm-up finishes startup in well under a second.
    """
    import time

    started = threading.Event()
    release = threading.Event()
    # Comfortably longer than this app's real startup (~45s locally: block init,
    # DB init, knowledge seed, agent load). Inline warm-up would add the full
    # hang on top and blow the budget; background warm-up does not extend
    # startup at all, so the two are cleanly separated rather than a race.
    HANG_SECONDS = 120

    def _hang():
        started.set()
        # Simulates a slow first-time model download on a host with no cache.
        release.wait(timeout=HANG_SECONDS)

    monkeypatch.setattr(main_module, "_warm_embedder", _hang)
    monkeypatch.setattr(main_module, "_warm_safety_detector", lambda: None)

    try:
        t0 = time.monotonic()
        with TestClient(app) as client:      # runs the full lifespan
            startup_seconds = time.monotonic() - t0
            assert client.get("/livez").status_code == 200
            assert started.wait(timeout=15), "warm-up never started"
            # Serving WHILE the model load is still stuck.
            assert client.get("/livez").status_code == 200
            assert startup_seconds < HANG_SECONDS, (
                f"startup took {startup_seconds:.1f}s, which is longer than the "
                f"{HANG_SECONDS}s model load it should NOT have waited for. "
                "Warm-up must not block the port from opening."
            )
    finally:
        release.set()


def test_a_failing_model_load_does_not_break_startup(monkeypatch):
    def _boom():
        raise RuntimeError("model repo unreachable")

    monkeypatch.setattr(main_module, "_warm_embedder", _boom)
    monkeypatch.setattr(main_module, "_warm_safety_detector", _boom)

    with TestClient(app) as client:
        assert client.get("/livez").status_code == 200


def test_one_warm_failure_does_not_skip_the_other(monkeypatch):
    """Isolation: the detector failing must not stop the embedder loading."""
    calls = []

    def _boom():
        calls.append("detector")
        raise RuntimeError("no weights")

    monkeypatch.setattr(main_module, "_warm_safety_detector", _boom)
    monkeypatch.setattr(main_module, "_warm_embedder", lambda: calls.append("embedder"))

    asyncio.run(main_module._warm_models())
    assert calls == ["detector", "embedder"], calls


def test_blocking_mode_is_still_available(monkeypatch):
    """WARM_MODELS_BLOCKING=true restores inline warm-up for on-prem, where
    readiness is expected to mean model-loaded."""
    order = []
    monkeypatch.setenv("WARM_MODELS_BLOCKING", "true")
    monkeypatch.setattr(main_module, "_warm_safety_detector", lambda: order.append("detector"))
    monkeypatch.setattr(main_module, "_warm_embedder", lambda: order.append("embedder"))

    with TestClient(app) as client:
        # Both loads completed during startup, before the first request.
        assert order == ["detector", "embedder"], order
        assert client.get("/livez").status_code == 200
