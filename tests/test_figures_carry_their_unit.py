"""A figure the model passes to the calculator carries the unit the user
stated (lane 2 on f0405fc: "5 floors, each 21,528 sq ft" ran with the floor
area as square metres). A bare number is read in the input's own unit, so a
derived figure -- a total the user never wrote -- was never converted. The
calculator converts a figure written with its unit; the tool description
tells the model to write it so."""
from __future__ import annotations

from app.agents.driver import tools
from app.lib import construction_formulas as cf


def test_the_calculator_description_asks_for_figures_with_their_unit():
    text = tools.calculator_schema(None)["function"]["description"]
    assert "Give every figure with the unit the user stated, written with it" in text
    assert "A figure you derive" in text and "carries the unit of the figures it came from" in text
    assert "A bare number is read in the input's own unit" in text


def test_a_derived_figure_written_with_its_unit_is_converted():
    out = cf.run_calculation("plumbing_flow_programme", {
        "text": "Plumbing programme for 5 floors, each 21,528 sq ft.",
        "floor_area_m2": "107,640 sq ft", "num_floors": 5})
    assert out["status"] == "success"
    note = out["conversions"][0]
    assert note["from_unit"] in ("sq ft", "ft2") and round(note["value_out"]) == 10000
