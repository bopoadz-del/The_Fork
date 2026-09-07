"""Live UI pack E4: raft 30×20×1.5 m including documented waste → 945 m3.

Live on 567147a (Master Corpus / theshovel.ai), construction-pm:

    "Concrete volume for a raft 30x20x1.5 m including your documented
     waste factor."

    Actual FAIL: net 900; waste factor missing.
    Expect: 945 (net 900 × documented 5%).

THE CAUSE. Leftover L6 aliased unnamed L×W×D to excavation_volume, which
returns bank volume only. E4 is a concrete/raft ask; the calc path must
pin concrete_volume and apply the project's documented waste (5%).

Kill switch: APPLY_DOCUMENTED_WASTE=0 restores the FAIL (900).
These tests pin the machinery (no live LLM, no corpus).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.runtime import (
    _graft_composed_e4_waste_volume,
    _looks_like_self_contained_calculation,
    _looks_like_search_preamble,
    _postprocess_answer,
    _should_force_synthesis,
)
from app.containers.construction import ConstructionContainer
from app.lib import construction_formulas as _cf
from app.lib.construction_formulas_quantities import (
    DOCUMENTED_CONCRETE_WASTE_FACTOR,
    answer_states_waste_volume,
    compose_documented_waste_volume,
    documented_waste_enabled,
    format_documented_waste_volume_line,
    looks_like_concrete_volume_ask,
    parse_lwt_metres,
    resolve_concrete_volume_calc,
)

E4_ASK_ASCII = (
    "Concrete volume for a raft 30x20x1.5 m including your documented "
    "waste factor."
)
E4_ASK_UNICODE = (
    "Concrete volume for a raft 30×20×1.5 m including your documented "
    "waste factor."
)
L6_ASK = (
    "Stay on smart-orchestrator. Bank volume of a rectangular trench "
    "14.5 m long by 3.2 m wide by 1.75 m deep."
)

FIXTURES = Path(__file__).parent / "fixtures" / "ui_phys"
CATALOG = json.loads((FIXTURES / "questions.json").read_text(encoding="utf-8"))


def _volume(payload: dict):
    inner = payload.get("result") or payload
    if isinstance(inner, dict) and isinstance(inner.get("result"), dict):
        inner = inner["result"]
    if not isinstance(inner, dict):
        return None
    return inner.get("volume_m3", inner.get("volume_with_waste_m3"))


def _net(payload: dict):
    inner = payload.get("result") or payload
    if isinstance(inner, dict) and isinstance(inner.get("result"), dict):
        inner = inner["result"]
    if not isinstance(inner, dict):
        return None
    return inner.get("net_volume_m3")


# ── the instrument ──────────────────────────────────────────────────────────


def test_e4_fixture_ask_is_the_instrument_wording():
    """The battery question is frozen. Do not tidy it."""
    assert CATALOG["cases"]["E4"]["ask"] == E4_ASK_UNICODE


def test_e4_still_routes_to_the_calculator():
    assert _looks_like_self_contained_calculation(E4_ASK_ASCII)
    assert _looks_like_self_contained_calculation(E4_ASK_UNICODE)


# ── detectors ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ask", [E4_ASK_ASCII, E4_ASK_UNICODE])
def test_e4_is_a_concrete_volume_ask(ask):
    assert looks_like_concrete_volume_ask(ask)


def test_leftover_l6_is_not_stolen_as_concrete():
    """Mutation killed: matching any 'volume' ask, which would send L6
    to concrete_volume and apply a 5% waste that earthwork does not use."""
    assert not looks_like_concrete_volume_ask(L6_ASK)
    assert not looks_like_concrete_volume_ask(
        "Bank volume of a rectangular trench 14.5 x 3.2 x 1.75"
    )


@pytest.mark.parametrize(
    "text,want",
    [
        ("30x20x1.5 m", (30.0, 20.0, 1.5)),
        ("30×20×1.5 m", (30.0, 20.0, 1.5)),
        ("30 x 20 x 1.5 m", (30.0, 20.0, 1.5)),
        ("30*20*1.5", (30.0, 20.0, 1.5)),
        (E4_ASK_ASCII, (30.0, 20.0, 1.5)),
        (E4_ASK_UNICODE, (30.0, 20.0, 1.5)),
        ("no dimensions here", None),
    ],
)
def test_lwt_chain_parses_e4_dims(text, want):
    assert parse_lwt_metres(text) == want


# ── the calculator ──────────────────────────────────────────────────────────


def test_named_concrete_volume_applies_documented_waste():
    r = _cf.run_calculation(
        "concrete_volume",
        {"length_m": 30, "width_m": 20, "thickness_m": 1.5},
    )
    assert r.get("status") == "success", r
    assert r["calculation"] == "concrete_volume"
    assert _net(r) == pytest.approx(900.0)
    assert _volume(r) == pytest.approx(945.0)
    assert r["result"]["waste_factor"] == pytest.approx(
        DOCUMENTED_CONCRETE_WASTE_FACTOR
    )


@pytest.mark.asyncio
async def test_e4_text_only_construction_calc_returns_945():
    """The live hat often passes the user ask as ``text`` and no calculator
    name. Before this fix leftover L6 either errored or returned bank 900."""
    r = await ConstructionContainer().construction_calc(
        {"text": E4_ASK_ASCII},
        {"action": "construction_calc", "text": E4_ASK_ASCII},
    )
    assert r.get("status") == "success", r
    assert r.get("calculation") == "concrete_volume"
    assert _net(r) == pytest.approx(900.0)
    assert _volume(r) == pytest.approx(945.0)


@pytest.mark.asyncio
async def test_e4_unicode_times_sign_returns_945():
    r = await ConstructionContainer().route(
        "construction_calc",
        {},
        {"action": "construction_calc", "text": E4_ASK_UNICODE},
    )
    assert r.get("status") == "success", r
    assert _volume(r) == pytest.approx(945.0)


@pytest.mark.asyncio
async def test_e4_formula_string_is_not_excavation():
    """Leftover L6 treats a bare 3-number formula as excavation_volume.
    The same shape plus a concrete/raft ask must not."""
    r = await ConstructionContainer().route(
        "construction_calc",
        {},
        {
            "action": "construction_calc",
            "formula": "30 * 20 * 1.5",
            "text": E4_ASK_ASCII,
        },
    )
    assert r.get("status") == "success", r
    assert r.get("calculation") == "concrete_volume"
    assert _volume(r) == pytest.approx(945.0)
    inner = r.get("result") or {}
    assert "bank_volume_m3" not in inner


def test_run_calculation_redirects_excavation_name_when_ask_is_e4():
    """The model names leftover L6's calculator and still passes the ask."""
    r = _cf.run_calculation(
        "excavation_volume",
        {
            "length_m": 30,
            "width_m": 20,
            "depth_m": 1.5,
            "text": E4_ASK_ASCII,
        },
    )
    assert r.get("status") == "success", r
    assert r.get("calculation") == "concrete_volume"
    assert _volume(r) == pytest.approx(945.0)


def test_unnamed_earthwork_formula_stays_excavation():
    """Fence: a 3-number formula with no concrete/raft words is still L6."""
    r = _cf.run_calculation(
        None,
        {"formula": "14.5 * 3.2 * 1.75", "text": L6_ASK},
    )
    # resolve must not pin concrete_volume; leftover L6 inference lives
    # on the container. Direct run_calculation with name=None stays unknown
    # unless the ask itself is concrete.
    assert r.get("status") == "error", r
    assert "Unknown calculation" in (r.get("error") or "")


def test_resolve_is_a_no_op_for_leftover_l6():
    calc, params = resolve_concrete_volume_calc(
        None, {"length_m": 14.5, "width_m": 3.2, "depth_m": 1.75}, L6_ASK,
    )
    assert calc is None
    assert params.get("waste_factor") in (None, 0, 0.0) or "waste_factor" not in params


# ── kill switch / mutation ──────────────────────────────────────────────────


def test_kill_switch_restores_the_fail_900(monkeypatch):
    """APPLY_DOCUMENTED_WASTE=0 is the operator's escape hatch and the
    mutation that proves the waste factor is load-bearing."""
    monkeypatch.setenv("APPLY_DOCUMENTED_WASTE", "0")
    assert not documented_waste_enabled()
    r = _cf.run_calculation(
        "concrete_volume",
        {"length_m": 30, "width_m": 20, "thickness_m": 1.5, "text": E4_ASK_ASCII},
    )
    assert r.get("status") == "success", r
    assert _net(r) == pytest.approx(900.0)
    assert _volume(r) == pytest.approx(900.0)
    assert r["result"]["waste_factor"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_kill_switch_on_the_container_path_returns_900(monkeypatch):
    monkeypatch.setenv("APPLY_DOCUMENTED_WASTE", "0")
    r = await ConstructionContainer().construction_calc(
        {"text": E4_ASK_UNICODE},
        {"action": "construction_calc"},
    )
    assert r.get("status") == "success", r
    assert _volume(r) == pytest.approx(900.0)
    assert _volume(r) != pytest.approx(945.0)


def test_mutation_resolve_no_op_leaves_excavation_900():
    """If resolve is a no-op, the leftover-L6 name stays excavation and
    the FAIL (bank 900, waste missing) returns. Proves the redirect is
    what applies the waste factor."""
    r = _cf.CALCULATORS["excavation_volume"](
        length_m=30, width_m=20, depth_m=1.5,
    )
    assert r["bank_volume_m3"] == pytest.approx(900.0)
    assert "volume_with_waste_m3" not in r
    assert "waste_factor" not in r


@pytest.mark.asyncio
async def test_leftover_l6_trench_still_returns_81_2():
    r = await ConstructionContainer().route(
        "construction_calc",
        {},
        {
            "action": "construction_calc",
            "name": "excavation_volume",
            "length_m": 14.5,
            "width_m": 3.2,
            "depth_m": 1.75,
        },
    )
    assert r.get("status") == "success", r
    inner = r.get("result") or {}
    assert inner.get("bank_volume_m3") == pytest.approx(81.2)
    assert inner.get("volume_with_waste_m3") is None


@pytest.mark.asyncio
async def test_orchestrator_routes_e4_to_construction_calc():
    from app.blocks.smart_orchestrator import SmartOrchestratorBlock

    r = await SmartOrchestratorBlock().process({"user_message": E4_ASK_ASCII})
    assert r["status"] == "success"
    assert "construction_calc" in (r.get("action_queue") or []), r


# ── leftover E4: "Let me validate…" is not 945 ────────────────────────────


LIVE_VALIDATE = "Let me validate…"
L4_BEAM = (
    "Stay on validation. Validate this office floor beam forty metres long "
    "on a fifty millimetre steel I-section carrying eight hundred kilonewtons "
    "per metre. Report Physical and Tier."
)
E1_ASK = "Calculate the delay damages per calendar day in SAR for the whole of the Works."


def test_compose_from_the_e4_ask_is_945():
    composed = compose_documented_waste_volume(E4_ASK_ASCII)
    assert composed is not None
    assert composed["volume_m3"] == pytest.approx(945.0)
    assert composed["net_volume_m3"] == pytest.approx(900.0)
    assert composed["waste_factor"] == pytest.approx(DOCUMENTED_CONCRETE_WASTE_FACTOR)
    line = format_documented_waste_volume_line(composed)
    assert "945" in line
    assert "waste" in line.lower()


def test_compose_is_none_for_leftover_l6_and_e1():
    assert compose_documented_waste_volume(L6_ASK) is None
    assert compose_documented_waste_volume(E1_ASK) is None
    assert compose_documented_waste_volume(L4_BEAM) is None


def test_let_me_validate_is_a_dangling_promise():
    """Live leftover E4 on 907f6cd. #454 caught search, not validate."""
    assert _looks_like_search_preamble(LIVE_VALIDATE)
    assert _looks_like_search_preamble("Let me validate the waste factor.")
    assert _looks_like_search_preamble("I'll validate this volume against the pipeline.")


def test_graft_replaces_validate_promise_with_945():
    msgs = [{"role": "user", "content": E4_ASK_UNICODE}]
    out = _graft_composed_e4_waste_volume(LIVE_VALIDATE, msgs)
    assert "945" in out
    assert "Let me validate" not in out
    assert "waste" in out.lower()


def test_graft_replaces_net_900_without_waste():
    msgs = [{"role": "user", "content": E4_ASK_ASCII}]
    out = _graft_composed_e4_waste_volume("The raft is 900 m³.", msgs)
    assert "945" in out
    assert not answer_states_waste_volume("The raft is 900 m³.", 945.0)


def test_graft_keeps_an_answer_that_already_states_945():
    msgs = [{"role": "user", "content": E4_ASK_ASCII}]
    already = "Concrete volume including documented waste is 945 m³ (119 trucks)."
    assert _graft_composed_e4_waste_volume(already, msgs) == already


def test_graft_is_a_no_op_for_leftover_l6():
    msgs = [{"role": "user", "content": L6_ASK}]
    assert _graft_composed_e4_waste_volume(LIVE_VALIDATE, msgs) == LIVE_VALIDATE


def test_validation_pipeline_does_not_force_synth_on_e4():
    rec = {"name": "validation_pipeline", "ok": True, "result": {"overall": "pass"}}
    assert _should_force_synthesis(rec, E4_ASK_ASCII) is False
    assert _should_force_synthesis(rec, L4_BEAM) is True
    assert _should_force_synthesis(rec) is True


def test_kill_switch_compose_is_900_not_a_hang(monkeypatch):
    monkeypatch.setenv("APPLY_DOCUMENTED_WASTE", "0")
    composed = compose_documented_waste_volume(E4_ASK_ASCII)
    assert composed is not None
    assert composed["volume_m3"] == pytest.approx(900.0)
    assert composed["waste_factor"] == pytest.approx(0.0)
    line = format_documented_waste_volume_line(composed)
    assert "900" in line
    assert "945" not in line


def test_postprocess_grafts_945_over_validate_promise():
    msgs = [{"role": "user", "content": E4_ASK_ASCII}]
    out = _postprocess_answer(LIVE_VALIDATE, None, msgs)
    assert "945" in out
    assert "Let me validate" not in out
