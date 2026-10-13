"""CORE calculator selection. The calculator whose display name the ask
writes is the one that runs, including a two-word name and a plain-words
ask."""
from __future__ import annotations

import asyncio

import pytest

from tests.lane5_core_support import (
    Param, call, construction_agent, drive, install_formula, registered,
    remove_formula, tool_envelope,
)


@pytest.fixture(autouse=True)
def _driver_stream(monkeypatch):
    from tests.lane5_core_support import install_driver_stream

    install_driver_stream(monkeypatch)


def _area(span_m: float, breadth_m: float) -> dict:
    return {"value": span_m * breadth_m, "rows": ["north apron 4", "south apron 9"]}


_INPUTS = {
    "span_m": Param("m", lo=0, hi=1e6, label="span"),
    "breadth_m": Param("m", lo=0, hi=1e6, label="breadth"),
}


def _predispatch(user: str):
    from app.agents.runtime import _predispatch_formula_calc

    agent = construction_agent()
    messages = [{"role": "user", "content": user}]

    async def go():
        return await _predispatch_formula_calc(agent, messages, None, user)

    return asyncio.run(go())


def test_a_two_word_display_name_is_selected(monkeypatch):
    name = "lane5_pick_two"
    user = "calculate the qxnet kestrel span 4 m breadth 3 m"
    with registered(name, _area, "Qxnet kestrel", _INPUTS, {"value": "m2"}):
        _events, seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("construction_calc", {"text": user}, "c1")]},
            {"content": "noted"},
        ])
    env = tool_envelope(seen)
    assert env.get("status") == "success", env
    assert env.get("calculation") == name


def test_the_display_name_the_ask_writes_beats_a_shorter_look_alike(monkeypatch):
    short = "lane5_qxnet_kestrel_total"
    named = "lane5_pick_named"
    user = "calculate the lane5 qxnet kestrel total net span 4 m breadth 3 m"

    def short_fn(span_m: float, breadth_m: float) -> dict:
        return {"value": span_m + breadth_m}

    try:
        install_formula(short, short_fn, "Qxnet kestrel total", _INPUTS, {"value": "m"})
        install_formula(named, _area, "Qxnet kestrel total net", _INPUTS, {"value": "m2"})
        _events, seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("construction_calc", {"text": user}, "c1")]},
            {"content": "noted"},
        ])
    finally:
        remove_formula(short)
        remove_formula(named)
    env = tool_envelope(seen)
    assert env.get("status") == "success", env
    assert env.get("calculation") == named


@pytest.mark.xfail(strict=True, reason="CORE: 18 plain progress ask answered without the calculator; result lines dropped")
def test_a_plain_ask_runs_the_calculator_and_keeps_the_result_lines():
    name = "lane5_pick_plain"
    user = "qxnet kestrel for a span of 4 m and a breadth of 3 m"
    with registered(name, _area, "Qxnet kestrel", _INPUTS, {"value": "m2"}):
        out = _predispatch(user)
    assert isinstance(out, dict) and out.get("ok"), out
    rendered = str(out.get("result") or "")
    assert "north apron 4" in rendered
    assert "south apron 9" in rendered
    assert not rendered.lstrip().startswith("{")


@pytest.mark.xfail(strict=True, reason="CORE: 26 calculator not selected for a plain-words ask")
def test_a_plain_words_ask_selects_the_named_calculator():
    name = "lane5_pick_words"
    user = "qxnet kestrel span 4 m breadth 3 m"
    with registered(name, _area, "Qxnet kestrel", _INPUTS, {"value": "m2"}):
        out = _predispatch(user)
    assert isinstance(out, dict) and out.get("ok"), out
    inner = out.get("result") or {}
    assert inner.get("status") == "success"
    assert inner.get("calculation") == name


@pytest.mark.xfail(strict=True, reason="CORE: 41 plain-words ask answered from general knowledge, no calculator")
def test_a_plain_words_ask_from_document_sections_still_runs_the_calculator():
    name = "lane5_pick_sections"
    user = (
        "qxnet kestrel from the project's own document sections, "
        "span 4 m and breadth 3 m"
    )
    with registered(name, _area, "Qxnet kestrel", _INPUTS, {"value": "m2"}):
        out = _predispatch(user)
    assert isinstance(out, dict) and out.get("ok"), out
    inner = out.get("result") or {}
    assert inner.get("calculation") == name
    assert inner.get("status") == "success"


@pytest.mark.xfail(strict=True, reason="CORE: 26 a plain-words unit conversion still runs the named calculator")
def test_a_plain_words_unit_conversion_runs_the_named_calculator():
    name = "lane5_pick_convert"

    def fn(floor_m2: float) -> dict:
        return {"value": floor_m2}

    user = "qxnet kestrel floor area 130 sq ft"
    with registered(name, fn, "Qxnet kestrel", {
        "floor_m2": Param("m2", lo=0, hi=1e9, label="floor area"),
    }, {"value": "m2"}):
        out = _predispatch(user)
    assert isinstance(out, dict) and out.get("ok"), out
    inner = out.get("result") or {}
    assert inner.get("calculation") == name
    value = (inner.get("result") or {}).get("value")
    assert value == pytest.approx(12.077, rel=1e-3)
