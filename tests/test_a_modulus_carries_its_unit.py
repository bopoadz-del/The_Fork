"""A bare number in kg/cm2 gets converted by guesswork.

Live SET4.1 T12, 7/10. The answer stated the ACI formula and its substitution
correctly -- "Ec = 4700 x sqrt(35) = 4700 x 5.9161" -- and then printed
**28,062 MPa**. That arithmetic gives 27,806. 28,062 is the tool's output,
280,624, divided by 10; the tool returns
``15000 x sqrt(f'c)`` in **kg/cm2** as a bare float, and 280,624 kg/cm2 is
27,520 MPa (x0.0980665), not 28,062.

So three different numbers were in play for one quantity, and nothing in the
payload said which unit it was in. The value now carries its unit and its own
MPa conversion, and the ACI 318-19 SI form is available on request -- that is
what an ACI question asks for.
"""
import math

import pytest

from app.lib import construction_formulas as cf

CALC = "modulus_of_elasticity_concrete"


def _result(params):
    out = cf.run_calculation(CALC, params)
    assert out["status"] == "success", out
    return out["result"]


# ── the default form is unchanged, but now says what it is ─────────────────

def test_the_default_value_is_unchanged():
    # Existing callers and the audit suite depend on this number.
    assert _result({"fck_n_mm2": 40})["value"] == pytest.approx(300_000.0, abs=0.5)
    assert _result({"fck_n_mm2": 25})["value"] == pytest.approx(
        round(15_000 * math.sqrt(250)), abs=0.5)


def test_the_default_value_names_its_unit():
    r = _result({"fck_n_mm2": 35})
    assert r["unit"] == "kg/cm2"
    assert r["value"] == pytest.approx(280_624.0, abs=0.5)


def test_the_mpa_conversion_is_done_here_not_guessed():
    r = _result({"fck_n_mm2": 35})
    # 280,624 kg/cm2 x 0.0980665 = 27,520 MPa. Dividing by 10 gives 28,062,
    # which is the number that reached the operator.
    assert r["value_mpa"] == pytest.approx(27_520.0, abs=1.0)
    assert r["value_mpa"] != pytest.approx(28_062.0, abs=1.0)


# ── the ACI form, which is what an ACI question asks for ───────────────────

@pytest.mark.parametrize("code", ["aci", "ACI", "aci318", "ACI-318", "aci_318_19"])
def test_the_aci_form_is_4700_root_fc_in_mpa(code):
    r = _result({"fck_n_mm2": 35, "code": code})
    assert r["value"] == pytest.approx(round(4700 * math.sqrt(35)), abs=0.5)
    assert r["value"] == pytest.approx(27_806.0, abs=1.0)
    assert r["unit"] == "MPa"


def test_the_aci_form_names_the_clause():
    assert "19.2.2.1b" in _result({"fck_n_mm2": 35, "code": "aci"})["standard"]


def test_an_unknown_code_keeps_the_default_form():
    # Not _norm_code: that helper maps every unknown string to ACI, which
    # would silently change this function's default.
    r = _result({"fck_n_mm2": 35, "code": "something else"})
    assert r["unit"] == "kg/cm2"
    assert r["value"] == pytest.approx(280_624.0, abs=0.5)


def test_the_two_forms_are_different_numbers():
    metric = _result({"fck_n_mm2": 35})
    aci = _result({"fck_n_mm2": 35, "code": "aci"})
    assert metric["value_mpa"] != pytest.approx(aci["value"], abs=1.0), (
        "if these were the same number the unit confusion would not matter")


def test_zero_strength_is_zero_in_both_forms():
    assert _result({"fck_n_mm2": 0})["value"] == pytest.approx(0.0, abs=0.5)
    assert _result({"fck_n_mm2": 0, "code": "aci"})["value"] == pytest.approx(0.0, abs=0.5)
