"""Signature-derived param binding for construction_calc / run_calculation.

Live Phase-2 re-score of calculators 1–42 failed with tool_error:
``run_calculation`` kept only exact signature names, so harness/model
kwargs (canonical, uppercase, or volume/volume_m3 synonyms) never reached
the function. Same class as PR #636 (calculate_evm PMI aliases).

SHARED WITH AGENT C: bind lives in run_calculation; the tool path flattens
top-level kwargs the same way the container path already does.
"""
from __future__ import annotations

import json

import pytest

from app.lib.construction_formulas import (
    bind_calculation_params,
    describe_calculation_params,
    run_calculation,
)
from tests.test_construction_calc_tool import _agent, _call, _run


def _ok(name, params):
    env = run_calculation(name, params)
    assert env.get("status") == "success", (name, params, env)
    return env["result"]


def _err(name, params):
    env = run_calculation(name, params)
    assert env.get("status") == "error", (name, params, env)
    return env


# ── live tool_error calcs: canonical + alias / flatten ─────────────────────


def test_backfill_volume_canonical_and_aliases():
    canonical = _ok("backfill_volume", {
        "excavation_bank_m3": 600, "structure_volume_m3": 200,
    })
    assert canonical["void_volume_m3"] == pytest.approx(400.0)
    assert canonical["loose_backfill_needed_m3"] == pytest.approx(480.0)

    aliased = _ok("backfill_volume", {
        "EXCAVATION_BANK_M3": 600, "structure_volume": 200,
    })
    assert aliased["void_volume_m3"] == pytest.approx(400.0)

    nested = _ok("backfill_volume", {
        "params": {"excavation_bank": 600, "structure": 200},
    })
    assert nested["void_volume_m3"] == pytest.approx(400.0)


def test_bolt_shear_capacity_canonical_and_aliases():
    # AISC: 0.75 * 372 * 490 * 1 / 1000 = 136.71 kN
    canonical = _ok("bolt_shear_capacity", {
        "bolt_area_mm2": 490, "shear_strength_mpa": 372,
        "n_shear_planes": 1, "code": "aci",
    })
    assert canonical["capacity_kn"] == pytest.approx(136.71, abs=0.02)

    aliased = _ok("bolt_shear_capacity", {
        "bolt_area": 490, "SHEAR_STRENGTH_MPA": 372,
    })
    assert aliased["capacity_kn"] == pytest.approx(136.71, abs=0.02)


def test_calculate_interim_payment_canonical_and_aliases():
    canonical = _ok("calculate_interim_payment", {
        "gross_valuation": 900_000, "retention_percent": 10,
    })
    assert canonical["net_payment"] == pytest.approx(810_000.0)

    aliased = _ok("calculate_interim_payment", {
        "GROSS_VALUATION": 900_000, "retention": 10,
    })
    assert aliased["net_payment"] == pytest.approx(810_000.0)


def test_calculate_payment_canonical_and_aliases():
    canonical = _ok("calculate_payment", {
        "claimed_amount": 1000, "certified_amount": 800,
    })
    assert canonical["net_payment_due"] == pytest.approx(760.0)

    aliased = _ok("calculate_payment", {
        "claimed": 1000, "CERTIFIED_AMOUNT": 800,
    })
    assert aliased["net_payment_due"] == pytest.approx(760.0)


def test_carbon_footprint_concrete_canonical_and_aliases():
    canonical = _ok("carbon_footprint_concrete", {
        "volume_m3": 100, "grade": "c30",
    })
    assert canonical["total_kgco2e"] == pytest.approx(32000.0)

    aliased = _ok("carbon_footprint_concrete", {"volume": 100, "GRADE": "c30"})
    assert aliased["total_kgco2e"] == pytest.approx(32000.0)


def test_compaction_control_canonical_and_aliases():
    canonical = _ok("compaction_control", {
        "field_dry_density": 1.90, "max_dry_density": 2.00,
    })
    assert canonical["compaction_percent"] == pytest.approx(95.0)

    aliased = _ok("compaction_control", {
        "FIELD_DRY_DENSITY": 1.90, "mdd": 2.00,
    })
    assert aliased["compaction_percent"] == pytest.approx(95.0)


def test_composite_column_design_canonical_and_aliases():
    canonical = _ok("composite_column_design", {
        "axial_load_kn": 2000, "column_diameter_mm": 400,
    })
    assert "recommended" in canonical

    aliased = _ok("composite_column_design", {
        "axial_load": 2000, "diameter": 400,
    })
    assert aliased["recommended"] == canonical["recommended"]


# ── tool path: top-level flatten (SHARED WITH AGENT C / #636) ───────────────


def test_construction_calc_tool_top_level_flatten_backfill():
    agent = _agent(["construction"])
    tc = {"id": "c1", "function": {
        "name": "construction_calc",
        "arguments": json.dumps({
            "calculation": "backfill_volume",
            "excavation_bank_m3": 600,
            "structure_volume_m3": 200,
        }),
    }}
    env = _run(agent._run_tool_call(tc))
    assert env["ok"] is True, env
    assert env["result"]["status"] == "success", env
    assert env["result"]["result"]["void_volume_m3"] == pytest.approx(400.0)


def test_construction_calc_tool_alias_volume_for_carbon():
    r = _call(_agent(["construction"]), "carbon_footprint_concrete",
              {"volume": 50, "grade": "c30"})
    assert r["ok"] is True, r
    assert r["result"]["result"]["total_kgco2e"] == pytest.approx(16000.0)


# ── structured missing-bind error ──────────────────────────────────────────


def test_failed_bind_names_expected_params_and_units():
    env = _err("backfill_volume", {"wrong_arg": 1})
    err = env["error"]
    assert "excavation_bank_m3" in err
    assert "structure_volume_m3" in err
    assert "(m3)" in err
    assert "missing required" in err
    names = [row["name"] for row in env["expected_params"]]
    assert "excavation_bank_m3" in names
    assert "structure_volume_m3" in names
    units = {row["name"]: row.get("unit") for row in env["expected_params"]}
    assert units["excavation_bank_m3"] == "m3"
    assert "excavation_bank_m3" in env["missing"]
    assert "structure_volume_m3" in env["missing"]
    assert "signature" in env
    # Never a bare TypeError string alone.
    assert not err.startswith("missing a required argument")
    assert "TypeError" not in err or "Expected:" in err


def test_failed_bind_bolt_shear_lists_bolt_area_mm2():
    env = _err("bolt_shear_capacity", {})
    assert "bolt_area_mm2" in env["error"]
    assert "(mm2)" in env["error"]
    assert "bolt_area_mm2" in env["missing"]


def test_failed_bind_interim_payment_lists_gross_valuation():
    env = _err("calculate_interim_payment", {"retention_percent": 10})
    assert "gross_valuation" in env["error"]
    assert "gross_valuation" in env["missing"]


def test_bind_helpers_are_not_registered_calculators():
    from app.lib.construction_formulas import CALCULATORS
    assert "bind_calculation_params" not in CALCULATORS
    assert "describe_calculation_params" not in CALCULATORS


def test_bind_is_case_insensitive_on_canonical_names():
    from app.lib.construction_formulas import CALCULATORS
    fn = CALCULATORS["compaction_control"]
    bound = bind_calculation_params(fn, {
        "Field_Dry_Density": 1.9, "MAX_DRY_DENSITY": 2.0,
    })
    assert bound == {"field_dry_density": 1.9, "max_dry_density": 2.0}


# ── side investigation: beam_shear_simple / delay_damages_daily ─────────────
#
# Live Phase-2 scored these wrong_number. Both functions default every
# argument to 0, so a failed bind does NOT TypeError — it "succeeds" with
# 0. That is the same missing-kwargs class as tool_error, not bad maths.
# Gated oracles (docs/formula-verification-table.md, test_formula_rc_beams,
# test_formula_qc_commercial_safety) already match the formulas:
#   beam_shear_simple(w=20, L=6) → V = wL/2 = 60 kN
#   delay_damages_daily(0.1%, 1_754_504_456.25) → 1_754_504.46 / day
# Harness expected values that assume bound kwargs are correct; the
# formula is not changed.


def test_beam_shear_simple_oracle_and_aliases():
    direct = _ok("beam_shear_simple", {"udl_w_kn_m": 20, "span_m": 6})
    assert direct["max_shear_kn"] == pytest.approx(60.0)
    aliased = _ok("beam_shear_simple", {"w": 20, "span": 6})
    assert aliased["max_shear_kn"] == pytest.approx(60.0)
    # Unbound kwargs used to silently return 0 (live wrong_number).
    zero = _ok("beam_shear_simple", {})
    assert zero["max_shear_kn"] == pytest.approx(0.0)


def test_delay_damages_daily_oracle_and_aliases():
    direct = _ok("delay_damages_daily", {
        "rate_percent": 0.1, "contract_amount": 1_754_504_456.25,
    })
    assert direct["daily_amount"] == pytest.approx(1_754_504.46, abs=0.005)
    aliased = _ok("delay_damages_daily", {
        "rate": 0.1, "aca": 1_754_504_456.25,
    })
    assert aliased["daily_amount"] == pytest.approx(1_754_504.46, abs=0.005)


def test_describe_params_includes_units_from_name_suffix():
    from app.lib.construction_formulas import CALCULATORS
    rows = describe_calculation_params(CALCULATORS["carbon_footprint_concrete"])
    by_name = {r["name"]: r for r in rows}
    assert by_name["volume_m3"]["unit"] == "m3"
    assert by_name["volume_m3"]["required"] is True


def test_calculate_evm_uppercase_pmi_names_bind_without_special_case():
    """#636 coverage, via the general binder (no calculate_evm-only table)."""
    env = _ok("calculate_evm", {
        "BCWS": 500_000, "BCWP": 400_000, "ACWP": 450_000, "BAC": 1_000_000,
    })
    assert env["CPI"] == 0.889
    assert env["SPI"] == 0.8
