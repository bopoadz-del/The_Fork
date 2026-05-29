"""Concurrency regression for LearningEngineBlock.shared_instance().

Before the singleton + per-instance state lock landed (PRs #19-#23 retro),
each smart_orchestrator dispatch and each /v1/feedback/route POST instantiated
a fresh LearningEngineBlock — full JSON _load_state on construction, full
JSON _save_state on every _record_pattern. Concurrent writes raced on the
file write: two threads would both serialise + write _state and the later
arrival silently clobbered the earlier one's bucket changes.

These tests prove (a) the lock prevents row loss under concurrent writes,
and (b) the path-keyed cache rebinds when LEARNING_ENGINE_STORAGE changes.

Establishes the first asyncio.gather + asyncio.to_thread pattern in tests/
since _record_pattern is sync — to_thread gives us real OS-thread
concurrency, not just event-loop coroutines that never actually run in
parallel on a CPython single-threaded interpreter.
"""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Mirror tests/test_auto_retrain.py:18-34's pattern verbatim — fresh
    DATA_DIR + storage path per test, plus the singleton cache reset so
    nothing leaks between tests."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LEARNING_ENGINE_STORAGE", str(tmp_path / "le_state.json"))
    from app.blocks.learning_engine import LearningEngineBlock
    LearningEngineBlock.reset_shared_instance_cache()
    yield tmp_path
    LearningEngineBlock.reset_shared_instance_cache()


@pytest.mark.asyncio
async def test_concurrent_record_pattern_loses_no_rows(isolated_data_dir):
    """N=25 parallel _record_pattern writes — every write must survive.

    Modern hardware + GIL makes the unlocked version pass most of the time
    even when it's racy, so this test also asserts BOTH the in-memory
    bucket and the on-disk JSON agree. A race in _save_state would let
    one thread serialise a smaller snapshot AFTER another committed a
    larger one — the in-memory count would be N but disk would be < N.
    Combined with the lock-invariant test below this catches scope drift
    even when the row-count assertion happens to pass.
    """
    from app.blocks.learning_engine import LearningEngineBlock, _storage_path

    le = LearningEngineBlock.shared_instance()
    le._state.setdefault("patterns", {}).pop("proj_test", None)

    N = 25

    def _write_one(i: int) -> None:
        le._record_pattern(
            {
                "project_id": "proj_test",
                "category": "routing_decisions",
                "observation": json.dumps({"idx": i, "action": "chat"}),
                "source": "concurrency_test",
            },
            {},
        )

    await asyncio.gather(*[asyncio.to_thread(_write_one, i) for i in range(N)])

    bucket = le._state["patterns"]["proj_test"]["routing_decisions"]
    assert len(bucket) == N, (
        f"in-memory: expected {N}, got {len(bucket)} — lock missing on append?"
    )
    # Disk must match memory — proves _save_state was inside the lock.
    # Without the lock, two _save_state calls could interleave at the JSON
    # write and the loser would serialise its smaller snapshot last.
    with open(_storage_path(), "r") as f:
        saved = json.load(f)
    saved_bucket = saved["patterns"]["proj_test"]["routing_decisions"]
    assert len(saved_bucket) == N, (
        f"on-disk: expected {N}, got {len(saved_bucket)} — "
        f"_save_state not inside the lock?"
    )


@pytest.mark.asyncio
async def test_record_pattern_serializes_via_state_lock(isolated_data_dir):
    """The actual proof the lock matters: monkeypatch _save_state to sleep,
    then run N parallel writes. With the lock held across the bucket-mutate
    + _save_state, only ONE writer is inside the critical region at a time
    (max-concurrent == 1). Without the lock, multiple writers overlap and
    max-concurrent > 1. This is a semantic assertion — the wall-clock or
    row-count tests pass even on a buggy implementation if hardware is fast
    enough; this one cannot pass without the lock.
    """
    import threading
    import time as _time
    from app.blocks.learning_engine import LearningEngineBlock

    le = LearningEngineBlock.shared_instance()
    le._state.setdefault("patterns", {}).pop("proj_test", None)

    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    original_save = le._save_state

    def slow_save():
        nonlocal active, max_active
        with counter_lock:
            active += 1
            if active > max_active:
                max_active = active
        _time.sleep(0.01)  # widen the critical region to expose races
        with counter_lock:
            active -= 1
        original_save()

    le._save_state = slow_save

    N = 20

    def _write(i: int) -> None:
        le._record_pattern(
            {"project_id": "proj_test", "category": "rd",
             "observation": json.dumps({"i": i})},
            {},
        )

    await asyncio.gather(*[asyncio.to_thread(_write, i) for i in range(N)])

    assert max_active == 1, (
        f"observed {max_active} concurrent writers inside _record_pattern's "
        f"critical region — the per-instance state lock is missing or its "
        f"scope doesn't cover _save_state. Lock must wrap bucket.append AND "
        f"_save_state together to be coherent."
    )
    # And all rows still landed
    assert len(le._state["patterns"]["proj_test"]["rd"]) == N


def test_shared_instance_rebinds_on_storage_path_change(tmp_path, monkeypatch):
    """The path-keyed cache must hand out a fresh instance when the resolved
    LEARNING_ENGINE_STORAGE changes. Without this guarantee, tests that use
    monkeypatch.setenv between calls would silently share state."""
    from app.blocks.learning_engine import LearningEngineBlock

    LearningEngineBlock.reset_shared_instance_cache()

    monkeypatch.setenv("LEARNING_ENGINE_STORAGE", str(tmp_path / "a.json"))
    a = LearningEngineBlock.shared_instance()

    monkeypatch.setenv("LEARNING_ENGINE_STORAGE", str(tmp_path / "b.json"))
    b = LearningEngineBlock.shared_instance()

    assert a is not b, (
        "shared_instance() must rebind when LEARNING_ENGINE_STORAGE changes; "
        "returning the same instance would corrupt test isolation"
    )

    # And going back to the first path returns the same first instance —
    # the cache is keyed, not a generation counter.
    monkeypatch.setenv("LEARNING_ENGINE_STORAGE", str(tmp_path / "a.json"))
    a_again = LearningEngineBlock.shared_instance()
    assert a is a_again, "same path must yield same cached instance"


def test_shared_instance_same_path_returns_same_instance(isolated_data_dir):
    """Two callers on the same storage path get the same instance —
    that's the whole point of the cache (avoid a per-request _load_state)."""
    from app.blocks.learning_engine import LearningEngineBlock

    a = LearningEngineBlock.shared_instance()
    b = LearningEngineBlock.shared_instance()
    assert a is b


def test_reset_shared_instance_cache_works(isolated_data_dir):
    """reset_shared_instance_cache() drops cached instances so tests that
    need a fresh _load_state can force one."""
    from app.blocks.learning_engine import LearningEngineBlock

    a = LearningEngineBlock.shared_instance()
    LearningEngineBlock.reset_shared_instance_cache()
    b = LearningEngineBlock.shared_instance()
    assert a is not b, "reset must drop the cached instance"
