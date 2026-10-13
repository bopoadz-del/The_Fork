"""Modulus of rupture follows the named design code.

The calculator does not pick a code. ACI 318 and Eurocode 2 each have a
published clause; any other string is refused in plain words.
"""
from __future__ import annotations

import math

import pytest

from app.lib.construction_formulas import run_calculation
from app.lib.formula_registry import get, parameters


CALC = "modulus_of_rupture"


def _run(params):
    return run_calculation(CALC, params)


def _ok(params):
    env = _run(params)
    assert env["status"] == "success", env
    return env["result"]


def _plain(env):
    return f"{env.get('question') or ''} {env.get('error') or ''}"


def test_aci_fck_30_is_3_40_mpa_and_names_the_clause():
    r = _ok({"fck_n_mm2": 30, "code": "ACI 318"})
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(3.40, abs=0.01)
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(0.62 * math.sqrt(30), abs=0.01)
    cited = str(r.get("standard") or r.get("clause") or "")
    assert "ACI 318" in cited
    assert "19.2.3.1" in cited


def test_ec2_fck_30_is_fctm_2_90_mpa_and_names_the_clause():
    r = _ok({"fck_n_mm2": 30, "code": "Eurocode 2"})
    fctm = 0.30 * (30.0 ** (2.0 / 3.0))
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(2.90, abs=0.01)
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(fctm, abs=0.01)
    cited = str(r.get("standard") or r.get("clause") or "")
    assert "1992" in cited or "Eurocode 2" in cited
    assert "3.1" in cited


def test_unknown_code_is_rejected_in_plain_words():
    env = _run({"fck_n_mm2": 30, "code": "not-a-published-code"})
    assert env["status"] == "error", env
    text = _plain(env)
    assert text.strip()
    assert "[" not in text
    assert "not-a-published-code" in text
    low = text.lower()
    assert "design code" in low or "code" in low


def test_missing_code_is_required_and_asked_in_plain_words():
    spec = get(CALC)
    assert spec is not None
    code_param = parameters(spec)["code"]
    assert code_param.required is True
    assert (code_param.label or "").strip()
    assert "_" not in code_param.label

    env = _run({"fck_n_mm2": 30})
    assert env["status"] == "error", env
    assert "code" in (env.get("missing") or [])
    text = _plain(env)
    assert text.strip()
    assert "design code" in text.lower()


@pytest.mark.parametrize("code", ["aci", "ACI", "ACI 318-19", "aci318"])
def test_aci_spellings_use_the_same_clause(code):
    r = _ok({"fck_n_mm2": 30, "code": code})
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(3.40, abs=0.01)
    assert "19.2.3.1" in str(r.get("standard") or r.get("clause") or "")


@pytest.mark.parametrize("code", ["EC2", "ec2", "EN 1992-1-1", "eurocode 2"])
def test_ec2_spellings_use_the_same_clause(code):
    r = _ok({"fck_n_mm2": 30, "code": code})
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(2.90, abs=0.01)
    cited = str(r.get("standard") or r.get("clause") or "")
    assert "1992" in cited or "Eurocode 2" in cited


def test_ec2_uses_flexural_tensile_when_depth_is_given():
    fctm = 0.30 * (30.0 ** (2.0 / 3.0))
    h_mm = 200.0
    flexural = max((1.6 - h_mm / 1000.0) * fctm, fctm)
    r = _ok({"fck_n_mm2": 30, "code": "EN 1992-1-1", "h_mm": h_mm})
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(flexural, abs=0.01)
    cited = str(r.get("standard") or r.get("clause") or "")
    assert "3.1" in cited


def test_ec2_high_strength_uses_the_log_form():
    fck = 60.0
    fctm = 2.12 * math.log(1.0 + (fck + 8.0) / 10.0)
    r = _ok({"fck_n_mm2": fck, "code": "Eurocode 2"})
    assert r["modulus_of_rupture_n_mm2"] == pytest.approx(fctm, abs=0.01)


def test_aci_lambda_defaults_to_one_and_scales_when_given():
    plain = _ok({"fck_n_mm2": 30, "code": "ACI 318"})
    light = _ok({"fck_n_mm2": 30, "code": "ACI 318", "lambda_factor": 0.75})
    assert plain["modulus_of_rupture_n_mm2"] == pytest.approx(
        0.62 * 1.0 * math.sqrt(30), abs=0.01,
    )
    assert light["modulus_of_rupture_n_mm2"] == pytest.approx(
        0.62 * 0.75 * math.sqrt(30), abs=0.01,
    )
