"""A figure the user writes with a unit is never dropped for a default: it
goes to the one open input that measures it, or the calculator asks which
input it is. Synthetic calculator with optional inputs."""
from __future__ import annotations

import pytest

from tests.lane5_core_support import Param, registered


def _strip(early_mpa: float = 2.0, design_mpa: float = 5.0, hours_h: float = 12.0) -> dict:
    return {"value": early_mpa + design_mpa + hours_h}


_INPUTS = {
    "early_mpa": Param("N/mm2", lo=0, hi=150, label="early strength", required=False),
    "design_mpa": Param("N/mm2", lo=0, hi=150, label="design strength", required=False),
    "hours_h": Param("h", lo=0, hi=1000, label="striking age", required=False),
}


def _run(text):
    from app.lib.construction_formulas import run_calculation

    with registered("lane5_place_or_ask", _strip, "Qx strip check", _INPUTS, {"value": "h"}):
        return run_calculation("lane5_place_or_ask", {"text": text})


def test_a_figure_that_fits_one_open_input_goes_there():
    env = _run("qx strip check, early strength 7 MPa, design strength 4 MPa, at 8 h")
    assert env.get("status") == "success", env
    assert env["result"]["value"] == pytest.approx(7 + 4 + 8)


def test_a_figure_that_fits_several_open_inputs_is_asked_not_defaulted():
    env = _run("qx strip check at 7 MPa")
    assert env.get("status") == "error" and env.get("needs_input") is True, env
    question = env["question"]
    assert "7 MPa" in question and "early strength" in question and "design strength" in question
