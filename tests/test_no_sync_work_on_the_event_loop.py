"""The production LOOP_STALL sources run off the event loop.

Production logged 220 LOOP_STALL events in a day (268 ms to 21.7 s), each
with a [loop] stack naming the blocking call. Each such call is replaced here
by a probe that records the thread it ran on; driving the async entry point
must never run it on the event loop's own thread. On main these fail: the
calls ran inline on the loop.
"""
from __future__ import annotations

import asyncio
import threading

import pytest


class _Probe:
    def __init__(self, result):
        self.result = result
        self.threads: list[int] = []

    def __call__(self, *args, **kwargs):
        self.threads.append(threading.get_ident())
        return self.result


def _drive(coro_factory):
    """Run the coroutine on a fresh loop; return the loop thread's ident."""
    loop_thread: list[int] = []

    async def main():
        loop_thread.append(threading.get_ident())
        return await coro_factory()

    out = asyncio.run(main())
    return loop_thread[0], out


def test_the_calculator_schema_is_a_fresh_copy_for_each_caller():
    from app.agents.driver import tools as driver_tools

    first = driver_tools.calculator_schema(None)
    first.clear()
    again = driver_tools.calculator_schema(None)
    assert again, "a caller's change reached the cached schema"


def test_the_calculator_schema_follows_a_newly_registered_formula(monkeypatch):
    from app.agents.driver import tools as driver_tools
    from app.lib import formula_registry

    before = str(driver_tools.calculator_schema(None))
    assert "zz_probe_formula" not in before

    @formula_registry.formula(owner="base", display_name="Probe formula", description="A probe.",
                              inputs={"x": formula_registry.Param("nr", 0, 10, label="probe input")},
                              outputs={"y": "nr"}, name="zz_probe_formula")
    def zz_probe_formula(x: float) -> dict:
        return {"y": x}

    try:
        assert "zz_probe_formula" in str(driver_tools.calculator_schema(None))
    finally:
        formula_registry._REGISTRY.pop("zz_probe_formula", None)
    assert "zz_probe_formula" not in str(driver_tools.calculator_schema(None))


@pytest.fixture(autouse=True)
def _fresh_schema_cache():
    from app.agents.driver import tools as driver_tools

    cache = getattr(driver_tools, "_SCHEMA_CACHE", {})
    cache.clear()
    yield
    cache.clear()
