"""CORE step budget: several independent calculator calls, then the answer,
must finish."""
from __future__ import annotations

import pytest

from tests.lane5_core_support import Param, answer_text, call, drive, registered


@pytest.fixture(autouse=True)
def _driver_stream(monkeypatch):
    from tests.lane5_core_support import install_driver_stream

    install_driver_stream(monkeypatch)
    monkeypatch.delenv("DRIVER_MAX_STEPS", raising=False)


@pytest.mark.xfail(strict=True, reason="CORE: 2 driver step limit on multi-input calculator turns -> could not finish")
def test_six_calculator_calls_then_the_answer_finish(monkeypatch):
    name = "lane5_step_total"

    def fn(span_m: float) -> dict:
        return {"value": span_m}

    user = "calculate the qxnet step total span 4 m"
    replies = [
        {"content": "", "tool_calls": [call("construction_calc", {
            "calculation": name, "span_m": 4,
        }, f"c{i}")]}
        for i in range(6)
    ]
    replies.append({"content": "The stored total is 4 m."})
    with registered(name, fn, "Qxnet step total", {
        "span_m": Param("m", lo=0, hi=1e6, label="span"),
    }, {"value": "m"}):
        events, _seen = drive(monkeypatch, user, replies)
    text = answer_text(events)
    assert "could not finish" not in text
    assert "The stored total is 4 m." in text
