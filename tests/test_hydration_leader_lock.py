"""Tests for the cross-process leader lock around the hydration scheduler.

The race the lock fixes: once UVICORN_WORKERS goes above 1 (PILOT.md targets
2 after the RAM upgrade), every worker spawns its own
``hydration_scheduler._loop`` background task at the same UTC hour. Without
coordination they all run ``_run_one_pass`` concurrently, producing torn JSON
writes to ``/tmp/cerebrum_learning_engine.json``, duplicate ``hydration_runs``
rows, and racy ``set_fact`` / ``set_agent_fact`` writes.

These tests verify the three scenarios that matter:

1. Lock free: the inner work runs and the lock is released.
2. Lock held by another worker: ``_run_one_pass`` returns immediately and
   does NOT touch the inner work. Critically, the non-owner must also NOT
   call ``delete`` on the owner's key.
3. ``REDIS_URL`` unset (dev mode): the pass runs unconditionally, no redis
   call is made — preserves single-worker dev behaviour.

We stub at ``redis.from_url`` rather than monkeypatching
``_acquire_leader_lock`` directly. Patching the helper would only exercise
the caller's ``if``; stubbing the redis layer runs the real
``set(key, value, nx=True, ex=3600)`` codepath, which is what
"a second concurrent worker would NOT enter" actually requires.
"""

from __future__ import annotations

import pytest


# ── Fake redis client ──────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal SET/DELETE backend that honours ``nx=True`` semantics.

    Backed by a shared store dict so two ``_FakeRedis`` instances built from
    the same store simulate two workers talking to the same Redis. That is
    what makes "worker B sees worker A's lock" a real test, not a tautology.
    """

    def __init__(self, store: dict):
        self._store = store
        self.delete_calls: list[str] = []
        self.set_calls: list[tuple[str, str, bool, int]] = []

    def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex or 0))
        if nx and key in self._store:
            return None  # redis-py returns None when NX fails; falsy
        self._store[key] = value
        return True

    def delete(self, key):
        self.delete_calls.append(key)
        return 1 if self._store.pop(key, None) is not None else 0


@pytest.fixture
def reset_hydration_module():
    """Drop the module's cached redis client between tests."""
    from app.core import hydration_scheduler
    hydration_scheduler.reset_for_tests()
    yield
    hydration_scheduler.reset_for_tests()


@pytest.fixture
def fake_redis_store():
    """Shared key/value backing store. Each fresh fixture yields a clean dict
    so test isolation matches a fresh Redis instance."""
    return {}


def _install_fake_redis(monkeypatch, store: dict) -> dict:
    """Make the scheduler's lazy ``import redis`` resolve to a stub module
    whose ``from_url`` hands out ``_FakeRedis`` clients sharing ``store``.

    We inject into ``sys.modules`` (not ``monkeypatch.setattr`` on a real
    redis module) because the dev venv may not have redis-py installed —
    redis is declared in requirements.txt but only needed in production
    where REDIS_URL is set. Returns a registry of every client built so
    tests can introspect set/delete calls per worker.
    """
    import sys
    import types

    built: dict[str, _FakeRedis] = {}

    def _factory(url, decode_responses=False):
        client = _FakeRedis(store)
        built[f"client-{len(built)}"] = client
        return client

    stub = types.ModuleType("redis")
    stub.from_url = _factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", stub)
    return built


def _install_redis_trap(monkeypatch) -> dict:
    """Inject a redis stub whose ``from_url`` raises if invoked — used to
    prove the dev-mode codepath never touches redis."""
    import sys
    import types

    calls = {"count": 0}

    def _trap(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("redis.from_url must not be invoked when REDIS_URL is unset")

    stub = types.ModuleType("redis")
    stub.from_url = _trap  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", stub)
    return calls


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_free_runs_inner_work(monkeypatch, fake_redis_store, reset_hydration_module):
    """Happy path: REDIS_URL set, no other worker holds the key. The inner
    work runs and the lock is released in the finally."""
    from app.core import hydration_scheduler

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    clients = _install_fake_redis(monkeypatch, fake_redis_store)

    ran = {"called": False}

    async def fake_do_pass():
        ran["called"] = True

    monkeypatch.setattr(hydration_scheduler, "_do_hydration_pass", fake_do_pass)

    await hydration_scheduler._run_one_pass()

    assert ran["called"] is True, "lock-free worker must execute inner work"
    # Lock acquired then released → store is empty again
    assert fake_redis_store == {}, "leader must release the lock in finally"
    # Exactly one client was built and it both SET and DELETE'd
    assert len(clients) == 1
    only = next(iter(clients.values()))
    assert any(call[2] is True for call in only.set_calls), "must call SET with nx=True"
    assert len(only.delete_calls) == 1, "owner must release the lock once"


@pytest.mark.asyncio
async def test_lock_held_skips_inner_work(monkeypatch, fake_redis_store, reset_hydration_module):
    """The critical concurrency invariant: worker B must NOT run the inner
    work while worker A holds the lock, and worker B must NOT delete A's key
    on its way out."""
    from datetime import datetime, timezone
    from app.core import hydration_scheduler

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    clients = _install_fake_redis(monkeypatch, fake_redis_store)

    # Pre-seed: pretend worker A acquired the lease for today already.
    today_iso = datetime.now(timezone.utc).date().isoformat()
    fake_redis_store[hydration_scheduler._leader_key(today_iso)] = "worker-a:host-a"

    ran = {"called": False}

    async def fake_do_pass():
        ran["called"] = True

    monkeypatch.setattr(hydration_scheduler, "_do_hydration_pass", fake_do_pass)

    await hydration_scheduler._run_one_pass()

    assert ran["called"] is False, (
        "Second concurrent worker must NOT enter _do_hydration_pass while the "
        "leader lock is held — this is the race the lock exists to prevent."
    )
    # Worker B's client must not have deleted worker A's key.
    assert len(clients) == 1
    only = next(iter(clients.values()))
    assert only.delete_calls == [], "non-owner worker must not release the leader's lock"
    # Worker A's pre-seeded lease is still there.
    assert fake_redis_store[hydration_scheduler._leader_key(today_iso)] == "worker-a:host-a"


@pytest.mark.asyncio
async def test_no_redis_url_runs_unconditionally(monkeypatch, reset_hydration_module):
    """Dev mode: REDIS_URL unset → no coordination, the pass always runs.

    Also asserts that ``redis.from_url`` is never even called — we should not
    require redis-py to do dev work."""
    from app.core import hydration_scheduler

    monkeypatch.delenv("REDIS_URL", raising=False)

    redis_factory_calls = _install_redis_trap(monkeypatch)

    ran = {"called": False}

    async def fake_do_pass():
        ran["called"] = True

    monkeypatch.setattr(hydration_scheduler, "_do_hydration_pass", fake_do_pass)

    await hydration_scheduler._run_one_pass()

    assert ran["called"] is True, "dev mode (no REDIS_URL) must run the pass unconditionally"
    assert redis_factory_calls["count"] == 0
