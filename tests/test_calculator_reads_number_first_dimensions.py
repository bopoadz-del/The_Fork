"""The calculator reads dimensions written the way a site engineer writes
them -- "12 m long, 8 m wide and 200 mm thick" -- converts each to the
unit its parameter declares, infers the shape from the dimensions given,
and asks rather than returning 0 when none was given."""
from __future__ import annotations

import math

import pytest

from app.lib import construction_formulas as cf


def _bind(name, text):
    return cf.extract_calculation_params_from_text(cf.CALCULATORS[name], text)


@pytest.mark.parametrize("text", [
    "How much concrete do I need for a ground slab 12 m long, 8 m wide and 200 mm thick?",
    "slab length 12 m, width 8 m, thickness 200 mm",
    "a slab 1200 cm long, 8 m wide, 0.2 m thick",
])
def test_slab_dimensions_bind_in_metres(text):
    p = _bind("concrete_volume", text)
    assert p == pytest.approx({"length_m": 12.0, "width_m": 8.0, "thickness_m": 0.2})
    out = cf.run_calculation("concrete_volume", p)["result"]
    assert out["net_volume_m3"] == pytest.approx(19.2)


def test_a_column_given_by_diameter_and_height_is_a_cylinder():
    p = _bind("concrete_volume", "a circular column 0.6 m in diameter and 3.5 m high")
    assert p == pytest.approx({"diameter_m": 0.6, "height_m": 3.5})
    out = cf.run_calculation("concrete_volume", p)["result"]
    assert out["net_volume_m3"] == pytest.approx(math.pi * 0.3 ** 2 * 3.5, rel=1e-3)


def test_a_millimetre_parameter_takes_metres_converted():
    assert cf._to_param_unit(0.2, "m", "thickness_mm") == pytest.approx(200.0)
    assert cf._to_param_unit(200, "mm", "thickness_m") == pytest.approx(0.2)
    assert cf._to_param_unit(5, None, "thickness_m") == 5


def test_a_volume_with_no_dimensions_asks_instead_of_returning_zero():
    out = cf.run_calculation("concrete_volume", {})
    assert out["status"] == "error" and "missing required" in out["error"]
