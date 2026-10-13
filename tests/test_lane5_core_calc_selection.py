"""CORE calculator selection. A driver turn dispatches the calculator whose
display name the ask writes, including a two-word name and a plain-words ask."""
from __future__ import annotations

import pytest

from tests.lane5_core_support import (
    Param, answer_text, call, drive, install_formula, registered,
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


def _dispatched(events):
    """What the driver itself dispatched, from its own events."""
    calls = [event.get("tool") for event in events if event.get("type") == "tool_call"]
    end = events[-1] if events else {}
    return calls, list(end.get("tools") or [])


@pytest.mark.xfail(strict=True, reason="CORE: 11 two-word display name not selected from those words")
def test_a_two_word_display_name_is_selected(monkeypatch):
    name = "lane5_pick_two"
    user = "calculate the qxnet kestrel span 4 m breadth 3 m"
    with registered(name, _area, "Qxnet kestrel", _INPUTS, {"value": "m2"}):
        events, seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("construction_calc", {"text": user}, "c1")]},
            {"content": "noted"},
        ])
    calls, tools = _dispatched(events)
    assert calls == ["construction_calc"], events
    assert tools == ["construction_calc"], events
    env = tool_envelope(seen)
    assert env.get("status") == "success", env
    assert env.get("calculation") == name


@pytest.mark.xfail(strict=True, reason="CORE: 22 look-alike calculator chosen over the one the ask names")
def test_the_display_name_the_ask_writes_beats_a_shorter_look_alike(monkeypatch):
    short = "lane5_qxnet_kestrel_total"
    named = "lane5_pick_named"
    user = "calculate the lane5 qxnet kestrel total net span 4 m breadth 3 m"

    def short_fn(span_m: float, breadth_m: float) -> dict:
        return {"value": span_m + breadth_m}

    try:
        install_formula(short, short_fn, "Qxnet kestrel total", _INPUTS, {"value": "m"})
        install_formula(named, _area, "Qxnet kestrel total net", _INPUTS, {"value": "m2"})
        events, seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("construction_calc", {"text": user}, "c1")]},
            {"content": "noted"},
        ])
    finally:
        remove_formula(short)
        remove_formula(named)
    calls, tools = _dispatched(events)
    assert calls == ["construction_calc"], events
    assert tools == ["construction_calc"], events
    env = tool_envelope(seen)
    assert env.get("status") == "success", env
    assert env.get("calculation") == named


def _driver_selects(monkeypatch, user, name, fn, inputs, outputs):
    """One driver turn. The model does not name a calculator; the driver
    dispatches construction_calc and the selection is whatever that run resolved."""
    with registered(name, fn, "Qxnet kestrel", inputs, outputs):
        events, seen = drive(monkeypatch, user, [
            {"content": "", "tool_calls": [call("construction_calc", {"text": user}, "c1")]},
            {"content": "noted"},
        ])
    calls, tools = _dispatched(events)
    assert calls == ["construction_calc"], events
    assert tools == ["construction_calc"], events
    return events, tool_envelope(seen)


@pytest.mark.xfail(strict=True, reason="CORE: 18 plain progress ask answered without the calculator; result lines dropped")
def test_a_plain_ask_runs_the_calculator_and_keeps_the_result_lines(monkeypatch):
    name = "lane5_pick_plain"
    user = "qxnet kestrel for a span of 4 m and a breadth of 3 m"
    events, env = _driver_selects(monkeypatch, user, name, _area, _INPUTS, {"value": "m2"})
    assert env.get("status") == "success", env
    assert env.get("calculation") == name
    text = answer_text(events)
    assert "north apron 4" in text
    assert "south apron 9" in text
    assert not text.lstrip().startswith("{")


@pytest.mark.xfail(strict=True, reason="CORE: 26 calculator not selected for a plain-words ask")
def test_a_plain_words_ask_selects_the_named_calculator(monkeypatch):
    name = "lane5_pick_words"
    user = "qxnet kestrel span 4 m breadth 3 m"
    _events, env = _driver_selects(monkeypatch, user, name, _area, _INPUTS, {"value": "m2"})
    assert env.get("status") == "success", env
    assert env.get("calculation") == name


@pytest.mark.xfail(strict=True, reason="CORE: 41 plain-words ask answered from general knowledge, no calculator")
def test_a_plain_words_ask_from_document_sections_still_runs_the_calculator(monkeypatch):
    name = "lane5_pick_sections"
    user = (
        "qxnet kestrel from the project's own document sections, "
        "span 4 m and breadth 3 m"
    )
    _events, env = _driver_selects(monkeypatch, user, name, _area, _INPUTS, {"value": "m2"})
    assert env.get("status") == "success", env
    assert env.get("calculation") == name


@pytest.mark.xfail(strict=True, reason="CORE: 26 a plain-words unit conversion still runs the named calculator")
def test_a_plain_words_unit_conversion_runs_the_named_calculator(monkeypatch):
    name = "lane5_pick_convert"

    def fn(floor_m2: float) -> dict:
        return {"value": floor_m2}

    user = "qxnet kestrel floor area 130 sq ft"
    _events, env = _driver_selects(monkeypatch, user, name, fn, {
        "floor_m2": Param("m2", lo=0, hi=1e9, label="floor area"),
    }, {"value": "m2"})
    assert env.get("status") == "success", env
    assert env.get("calculation") == name
    value = (env.get("result") or {}).get("value")
    assert value == pytest.approx(12.077, rel=1e-3)
