"""After an idle spell the first request does not pay a new database connection.

Order 0230Z item 6 (1940Z/2115Z: /health DB 616-640 ms after idle, 6-67 ms
warm). Every pooled connection older than pool_recycle (300 s) is replaced at
checkout -- a new connect + TLS, ~0.6 s -- so after five quiet minutes the
first request and /health paid it. Generic fix, configuration plus one timer:
the pool hands out its most recently used connection first (LIFO), and a
keep-warm thread runs SELECT 1 every DB_KEEP_WARM_S (default 240 s, inside
the recycle window; 0 turns it off), so that connection is always fresh.
LANE5 confirms live: first hit under 100 ms after an idle hour.
"""
import ast
import pathlib
import threading
import time

import pytest

from app.core import db, health_probes


def test_the_pool_reuses_its_newest_connection_and_recycle_is_configurable(monkeypatch):
    kwargs = db._engine_kwargs("postgresql://u@h/d")
    assert kwargs["pool_use_lifo"] is True
    assert kwargs["pool_recycle"] == 300 and kwargs["pool_pre_ping"] is True
    monkeypatch.setenv("DB_POOL_RECYCLE_S", "600")
    assert db._engine_kwargs("postgresql://u@h/d")["pool_recycle"] == 600
    monkeypatch.setenv("DB_POOL_RECYCLE_S", "1")
    assert db._engine_kwargs("postgresql://u@h/d")["pool_recycle"] == 30


@pytest.fixture
def probes(monkeypatch):
    calls: list = []

    def fake_probe(timeout_s: float = 2.0):
        calls.append(threading.current_thread().name)
        return {"ok": len(calls) % 2 == 0, "latency_ms": 1, "error": None if len(calls) % 2 == 0 else "down"}

    monkeypatch.setattr(health_probes, "probe_database", fake_probe)
    monkeypatch.setattr(health_probes, "_keep_warm_thread", None)
    yield calls
    health_probes.stop_db_keep_warm()


def test_the_keep_warm_runs_select_1_on_its_own_thread_and_survives_failures(probes):
    thread = health_probes.start_db_keep_warm(0.05)
    assert thread is not None and thread.daemon
    deadline = time.monotonic() + 3
    while len(probes) < 4 and time.monotonic() < deadline:
        time.sleep(0.02)
    health_probes.stop_db_keep_warm()
    thread.join(2)
    assert len(probes) >= 4, "the timer stopped after a failed round-trip"
    assert set(probes) == {"db-keep-warm"}


def test_one_keep_warm_thread_per_process(probes):
    first = health_probes.start_db_keep_warm(10)
    assert health_probes.start_db_keep_warm(10) is first


def test_the_interval_comes_from_the_environment(monkeypatch, probes):
    monkeypatch.delenv("DB_KEEP_WARM_S", raising=False)
    assert health_probes._keep_warm_interval_s() == 240.0
    monkeypatch.setenv("DB_KEEP_WARM_S", "0")
    assert health_probes.start_db_keep_warm() is None
    for bad in ("x", "-5", "inf"):
        monkeypatch.setenv("DB_KEEP_WARM_S", bad)
        assert health_probes._keep_warm_interval_s() == 240.0


def test_the_app_starts_the_keep_warm_at_startup():
    source = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    lifespan = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    calls = {getattr(c.func, "attr", "") for c in ast.walk(lifespan) if isinstance(c, ast.Call)}
    assert "start_db_keep_warm" in calls
