"""Calculator runs and background jobs never queue ahead of a turn's reads.

Order 0520Z: after #980 moved the calculator run and hydration onto the one
turn pool (sized to the database pool), long workflow asks slowed end to end
while the driver turn itself did not -- work waited for a worker. They now
run in their own pools, and any call that waits for a worker is logged.
"""
import asyncio
import logging
import threading
import time

from app.core import offload


def test_the_calculator_pool_is_not_the_turn_pool():
    async def run():
        turn = await offload.off_loop(lambda: threading.current_thread().name)
        calc = await offload.off_loop_in("calc", lambda: threading.current_thread().name)
        back = await offload.off_loop_in("background", lambda: threading.current_thread().name)
        return turn, calc, back

    turn, calc, back = asyncio.run(run())
    assert turn.startswith("turn-io") and calc.startswith("calc-work") and back.startswith("background-work")


def test_a_long_wait_for_a_worker_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(offload, "_QUEUE_WARN_S", 0.05)
    monkeypatch.setattr(offload, "_last_queue_log", 0.0)
    monkeypatch.setitem(offload._NAMED_SIZES, "background", ("BACKGROUND_WORKERS_TEST_UNSET", 1))
    offload._NAMED_POOLS.pop("background", None)

    async def run():
        slow = asyncio.ensure_future(offload.off_loop_in("background", time.sleep, 0.2))
        await asyncio.sleep(0.01)
        await offload.off_loop_in("background", lambda: None)
        await slow

    with caplog.at_level(logging.WARNING, logger="app.core.offload"):
        asyncio.run(run())
    offload._NAMED_POOLS.pop("background", None)
    assert any("OFFLOAD_QUEUE pool=background" in r.getMessage() for r in caplog.records)


def test_context_variables_still_round_trip_in_a_named_pool():
    import contextvars

    var = contextvars.ContextVar("probe", default="")

    async def run():
        var.set("caller")
        seen = await offload.off_loop_in("calc", var.get)
        await offload.off_loop_in("calc", var.set, "set in thread")
        return seen, var.get()

    assert asyncio.run(run()) == ("caller", "set in thread")
