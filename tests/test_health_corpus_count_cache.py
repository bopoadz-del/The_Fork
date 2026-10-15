"""Corpus chunk count on /health is cached. No database."""
from __future__ import annotations

import threading
import time

import app.core.health_probes as health_probes
from app.core.health_probes import probe_corpus_chunks, reset_corpus_chunk_cache


def _ok(chunks: int, source: str = "estimate") -> dict:
    return {
        "chunks": chunks,
        "source": source,
        "table": "chunks_v2",
        "latency_ms": 4,
        "error": None,
    }


def setup_function():
    reset_corpus_chunk_cache()


def teardown_function():
    thread = health_probes._corpus_refresh_thread
    if thread is not None and thread.is_alive():
        thread.join(2)
    reset_corpus_chunk_cache()


def test_two_calls_inside_ttl_refresh_once(monkeypatch):
    calls = []

    def refresher(timeout_s: float = 1.5):
        calls.append(timeout_s)
        return _ok(11)

    monkeypatch.setattr(health_probes, "_refresh_corpus_count", refresher)
    first = probe_corpus_chunks()
    second = probe_corpus_chunks()
    assert calls == [1.5]
    assert first["source"] == "estimate"
    assert first["chunks"] == 11
    assert second["source"] == "cached"
    assert second["chunks"] == 11
    assert second["age_s"] >= 0
    assert second.get("stale") is not True


def test_stale_call_returns_immediately_and_refreshes_once(monkeypatch):
    calls = []
    started = threading.Event()
    release = threading.Event()

    def refresher(timeout_s: float = 1.5):
        calls.append(timeout_s)
        if len(calls) == 1:
            return _ok(4, "estimate")
        started.set()
        release.wait(2)
        return _ok(8, "exact")

    monkeypatch.setattr(health_probes, "_refresh_corpus_count", refresher)
    primed = probe_corpus_chunks()
    assert primed["chunks"] == 4
    with health_probes._corpus_cache_lock:
        health_probes._corpus_cache_at = time.monotonic() - (health_probes._corpus_ttl_s() + 5)

    began = time.monotonic()
    stale = probe_corpus_chunks()
    elapsed = time.monotonic() - began
    assert elapsed < 0.3
    assert stale["stale"] is True
    assert stale["chunks"] == 4
    assert stale["source"] == "estimate"
    assert stale["age_s"] >= health_probes._corpus_ttl_s()
    assert started.wait(2)

    again = probe_corpus_chunks()
    assert again["stale"] is True
    assert again["chunks"] == 4
    release.set()
    thread = health_probes._corpus_refresh_thread
    assert thread is not None
    thread.join(2)
    assert calls == [1.5, 1.5]


def test_refresher_error_is_payload_and_does_not_raise(monkeypatch):
    def refresher(timeout_s: float = 1.5):
        raise RuntimeError("db down")

    monkeypatch.setattr(health_probes, "_refresh_corpus_count", refresher)
    payload = probe_corpus_chunks()
    assert payload["chunks"] is None
    assert payload["error"]
    assert "RuntimeError" in payload["error"]
    assert "db down" in payload["error"]
