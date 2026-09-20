"""Bind engineer-language numbers from ask text into construction_calc.

Phase-2 re-probe of #43–84 scored missing-required after #639 because
named_calculator / predispatch passes ``text`` only. The model (or the
probe) writes As1500 / span 8m / W=10000 kN — those numbers never became
kwargs. Owner D7: extract only what the ask/tool args contain; never
invent a missing figure.

SHARED WITH AGENT C: extract + bind live in run_calculation (formula
envelope). The tool path is the same flatten #636/#639 already use.
"""
from __future__ import annotations

import json

import pytest

from app.lib.construction_formulas import (
    CALCULATORS,
    bind_calculation_params,
    run_calculation,
)
from tests.test_construction_calc_tool import _agent, _run


def _ok(name, params):
    env = run_calculation(name, params)
    assert env.get("status") == "success", (name, params, env)
    return env["result"]


def _err(name, params):
    env = run_calculation(name, params)
    assert env.get("status") == "error", (name, params, env)
    return env


def _tool(calculation, extra):
    agent = _agent(["construction"])
    tc = {
        "id": "c1",
        "function": {
            "name": "construction_calc",
            "arguments": json.dumps({"calculation": calculation, **extra}),
        },
    }
    return _run(agent._run_tool_call(tc))


# ── rc_beam_moment_capacity (ACI oracle 288.50 kN·m) ───────────────────────

_RC_ASK = (
    "rc_beam_moment_capacity design ACI As1500, fy420, b300, d550, fc30"
)
_RC_KEY = "moment_capacity_kn_m"
_RC_VAL = 288.50
_RC_ABS = 0.3


def test_rc_beam_engineer_ask_text_binds_and_computes():
    """Predispatch shape: calculation + text, no numeric kwargs."""
    inner = _ok("rc_beam_moment_capacity", {"text": _RC_ASK})
    assert inner[_RC_KEY] == pytest.approx(_RC_VAL, abs=_RC_ABS)


def test_rc_beam_engineer_abbreviation_kwargs_bind():
    """Model may put As/fy/b/d/fc next to calculation (not inside params)."""
    inner = _ok("rc_beam_moment_capacity", {
        "As": 1500, "fy": 420, "b": 300, "d": 550, "fc": 30,
    })
    assert inner[_RC_KEY] == pytest.approx(_RC_VAL, abs=_RC_ABS)


@pytest.mark.parametrize("shape", ("top_level_text", "params", "input"))
def test_rc_beam_engineer_ask_three_shapes(shape):
    if shape == "top_level_text":
        extra = {"text": _RC_ASK}
    elif shape == "params":
        extra = {"params": {"text": _RC_ASK}}
    else:
        extra = {"input": {"text": _RC_ASK}}
    env = _tool("rc_beam_moment_capacity", extra)
    assert env.get("ok") is True, env
    assert env["result"]["result"][_RC_KEY] == pytest.approx(_RC_VAL, abs=_RC_ABS)


def test_rc_beam_partial_ask_does_not_invent_missing_dims():
    """D7: As+fy only — refuse; do not invent b=300 / d=550 / fc=30."""
    env = _err("rc_beam_moment_capacity", {
        "text": "rc_beam_moment_capacity As1500 fy420",
    })
    err = env["error"]
    assert "missing required" in err
    for name in ("width_mm", "eff_depth_mm", "fc_mpa"):
        assert name in env["missing"], env["missing"]
        assert name in err
    assert env.get("result") is None or _RC_KEY not in (env.get("result") or {})


# ── steel_tension_capacity (AISC oracle 860.63 kN) ─────────────────────────

_STEEL_ASK = (
    "steel_tension_capacity AISC Ag=3000, Ae=2550 mm2, Fy=345, Fu=450"
)
_STEEL_KEY = "capacity_kn"
_STEEL_VAL = 860.63


def test_steel_tension_engineer_ask_text_binds_and_computes():
    inner = _ok("steel_tension_capacity", {"text": _STEEL_ASK})
    assert inner[_STEEL_KEY] == pytest.approx(_STEEL_VAL, abs=0.05)


def test_steel_tension_engineer_abbreviation_kwargs_bind():
    inner = _ok("steel_tension_capacity", {
        "Ag": 3000, "Ae": 2550, "Fy": 345, "Fu": 450,
    })
    assert inner[_STEEL_KEY] == pytest.approx(_STEEL_VAL, abs=0.05)


def test_steel_tension_partial_ask_does_not_invent_gross_area():
    env = _err("steel_tension_capacity", {
        "text": "steel_tension_capacity Fy=345 Fu=450",
    })
    assert "gross_area_mm2" in env["missing"]
    assert "missing required" in env["error"]


# ── post_tensioning_force (8 m / 0.25 m → 1000 kN; LL unused) ─────────────

_PT_ASK = (
    "post_tensioning_force for span 8m, slab 0.25m, live load 5 kN/m2"
)
_PT_PARTIAL = "post_tensioning_force for span 8m and slab 0.25m"
_PT_KEY = "tendon_force_kn"


def test_post_tensioning_engineer_ask_text_binds_and_computes():
    inner = _ok("post_tensioning_force", {"text": _PT_ASK})
    assert inner[_PT_KEY] == pytest.approx(1000.0, abs=0.1)


def test_post_tensioning_span_and_slab_without_live_load_is_honest():
    """D7: span+slab in the ask still do not invent live_load_kn_m2=5."""
    env = _err("post_tensioning_force", {"text": _PT_PARTIAL})
    assert "live_load_kn_m2" in env["missing"]
    assert "span_m" not in env["missing"]
    assert "slab_thickness_m" not in env["missing"]
    assert "missing required" in env["error"]
    assert "live_load_kn_m2 (kN/m2)" in env["error"]


def test_post_tensioning_construction_calc_text_only():
    env = _tool("post_tensioning_force", {"text": _PT_ASK})
    assert env.get("ok") is True, env
    assert env["result"]["result"][_PT_KEY] == pytest.approx(1000.0, abs=0.1)


# ── seismic_base_shear (ASCE 1250 kN) + unit on success ────────────────────

_SEISMIC_ASK = (
    "seismic_base_shear ASCE W=10000 kN, SDS=1.0, R=8, Ie=1"
)
_SEISMIC_KEY = "base_shear_kn"


def test_seismic_engineer_ask_text_binds_and_stamps_unit():
    env = run_calculation("seismic_base_shear", {"text": _SEISMIC_ASK})
    assert env.get("status") == "success", env
    inner = env["result"]
    assert inner[_SEISMIC_KEY] == pytest.approx(1250.0, abs=0.05)
    assert inner.get("unit") == "kN"
    assert "kn" in str(inner.get("note") or "").lower()


def test_seismic_kwargs_success_stamps_unit_like_628():
    """#628 unitless/SAR pattern: a computed figure must carry a unit."""
    env = run_calculation("seismic_base_shear", {
        "seismic_weight_kn": 10000, "code": "aci",
        "sds": 1.0, "r": 8.0, "ie": 1.0,
    })
    assert env.get("status") == "success", env
    inner = env["result"]
    assert inner[_SEISMIC_KEY] == pytest.approx(1250.0, abs=0.05)
    assert inner.get("unit") == "kN"
    assert inner.get("value") == pytest.approx(1250.0, abs=0.05)


def test_seismic_construction_calc_text_only_has_unit():
    env = _tool("seismic_base_shear", {"text": _SEISMIC_ASK})
    assert env.get("ok") is True, env
    inner = env["result"]["result"]
    assert inner[_SEISMIC_KEY] == pytest.approx(1250.0, abs=0.05)
    assert inner.get("unit") == "kN"


# ── more #43–84 engineer asks (same extract path, no invented defaults) ────

def test_live_load_reduction_engineer_ask_binds():
    inner = _ok("live_load_reduction", {
        "text": "live_load_reduction L0=4.79 kN/m2, KLL=4, AT=40 m2",
    })
    assert inner["reduced_live_load_kn_m2"] == pytest.approx(2.928, abs=0.01)


def test_modulus_of_rupture_engineer_ask_binds():
    inner = _ok("modulus_of_rupture", {
        "text": "modulus of rupture for fck 40 N/mm2",
    })
    assert inner["modulus_of_rupture_n_mm2"] == pytest.approx(4.8, abs=0.005)


def test_rebar_by_area_engineer_ask_binds():
    inner = _ok("rebar_by_area", {
        "text": "rebar_by_area 20 m2, spacing 200 mm, d12",
    })
    assert inner["total_mass_kg"] == pytest.approx(88.8, abs=0.2)


def test_empty_text_still_names_required_does_not_invent():
    env = _err("rc_beam_moment_capacity", {"text": "rc_beam_moment_capacity"})
    assert "missing required" in env["error"]
    assert "steel_area_mm2" in env["missing"]
    bound = bind_calculation_params(
        CALCULATORS["rc_beam_moment_capacity"],
        {"text": "rc_beam_moment_capacity"},
    )
    assert bound == {}
