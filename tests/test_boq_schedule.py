"""A schedule built from a BOQ must contain THAT BOQ's work.

Measured 2026-08-17 on the live service: /export/schedule-from-document was
handed a BOQ of structural steel truss, composite cladding, chilled-water
piping and epoxy flooring, and returned 46 activities of which ZERO mentioned
any of it — a per-project-type template, with the response headers reporting
'procurement items: 0, milestones: 0'. The BOQ contributed nothing.

These fences pin the replacement: quantities drive durations, the BOQ's own
scope becomes the activity list, sequencing is construction order, and a
missing productivity rate is refused by name rather than defaulted (a schedule
resting on an invented output rate looks exactly like a real one until it
slips).
"""
from __future__ import annotations

import json
import math

import pytest

from app.lib.boq_schedule import (
    CONSTRUCTION_SEQUENCE,
    MissingProductivity,
    activities_from_boq,
    duration_days,
    group_by_category,
    schedule_basis,
)

# A real-shaped BOQ: exactly what boq_processor emits.
BOQ = [
    {"item_key": "structural_steel_roof_truss", "description": "Structural steel roof truss fabrication",
     "quantity": 47, "unit": "ton", "unit_cost": 4850.0, "total_cost": 227950.0},
    {"item_key": "aluminium_composite_cladding", "description": "Aluminium composite cladding to facade",
     "quantity": 1310, "unit": "m2", "unit_cost": 395.0, "total_cost": 517450.0},
    {"item_key": "chilled_water_piping", "description": "Chilled water piping DN200 insulated",
     "quantity": 640, "unit": "m", "unit_cost": 720.0, "total_cost": 460800.0},
    {"item_key": "bulk_excavation", "description": "Bulk excavation to reduced level",
     "quantity": 2500, "unit": "m3", "unit_cost": 18.5, "total_cost": 46250.0},
]

# The shared classifier is pricing-tuned and puts "…piping DN200 insulated"
# in Waterproofing/Insulation (earliest match wins, on "insulated"). A planner
# corrects that with an override; the rates below cover the corrected result.
# Keyed by DESCRIPTION, not item_key: boq_processor derives item_key from
# the description itself ("chilled_water_piping_dn200_insulated"), so a
# hand-written key silently fails to match the extracted line.
OVERRIDES = {"Chilled water piping DN200 insulated": "Mechanical/HVAC (MEP)"}

RATES = {
    "Structural Steel": 4,         # ton / crew-day
    "Windows/Doors/Facade": 60,    # m2 / crew-day
    "Mechanical/HVAC (MEP)": 40,   # m / crew-day
    "Earthworks/Excavation": 400,  # m3 / crew-day
}


def _by_name(acts):
    return {a["name"]: a for a in acts}


# ── durations are derived, not invented ────────────────────────────────────

def test_duration_is_quantity_over_output():
    assert duration_days(1310, 60) == math.ceil(1310 / 60) == 22
    assert duration_days(2500, 400) == 7
    assert duration_days(47, 4) == 12


def test_crews_divide_the_duration():
    assert duration_days(1310, 60, crews=2) == math.ceil(1310 / 120) == 11


def test_a_measurable_item_never_takes_zero_days():
    assert duration_days(0.5, 400) == 1


def test_zero_or_negative_output_is_an_error_not_a_division():
    with pytest.raises(ValueError):
        duration_days(100, 0)


# ── the BOQ's own scope becomes the activity list ──────────────────────────

def test_activities_carry_the_boq_scope_not_a_template():
    acts = activities_from_boq(BOQ, productivity=RATES, category_overrides=OVERRIDES)
    names = " | ".join(a["name"].lower() for a in acts)
    for term in ("steel roof truss", "cladding", "chilled water piping", "excavation"):
        assert term in names, f"{term!r} missing from {names}"
    # and none of the canned template's activities appear
    for template_item in ("topographic survey", "building permit"):
        assert template_item not in names


def test_each_activity_traces_back_to_its_boq_line():
    acts = _by_name(activities_from_boq(BOQ, productivity=RATES, category_overrides=OVERRIDES))
    cladding = acts["Aluminium composite cladding to facade"]
    assert cladding["duration_days"] == 22
    assert cladding["boq"]["quantity"] == 1310
    assert cladding["boq"]["unit"] == "m2"
    assert cladding["boq"]["rate_used_per_crew_day"] == 60
    assert cladding["boq"]["total_cost"] == 517450.0


def test_activity_shape_matches_generate_wbs():
    """The CPM engine, cost bridge and Excel writer consume this unchanged."""
    for a in activities_from_boq(BOQ, productivity=RATES, category_overrides=OVERRIDES):
        assert {"id", "code", "name", "duration_days", "predecessors",
                "resources", "wbs_phase"} <= set(a)
        assert isinstance(a["duration_days"], int) and a["duration_days"] >= 1
        assert isinstance(a["predecessors"], list)
        assert a["resources"] and all(isinstance(r, str) for r in a["resources"])


# ── sequencing is construction order ───────────────────────────────────────

def test_packages_run_in_construction_order():
    acts = activities_from_boq(BOQ, productivity=RATES, category_overrides=OVERRIDES)
    phases = [a["wbs_phase"] for a in acts]
    order = {p: i for i, p in enumerate(dict.fromkeys(phases))}
    # earthworks precedes steel precedes facade precedes MEP
    assert order["earthworks_excavation"] < order["structural_steel"]
    assert order["structural_steel"] < order["windows_doors_facade"]
    assert order["windows_doors_facade"] < order["mechanical_hvac_mep"]


def test_first_activity_has_no_predecessor_and_the_rest_chain():
    acts = activities_from_boq(BOQ, productivity=RATES, category_overrides=OVERRIDES)
    assert acts[0]["predecessors"] == []
    ids = {a["id"] for a in acts}
    for a in acts[1:]:
        assert a["predecessors"], f"{a['name']} floats with no predecessor"
        for p in a["predecessors"]:
            assert p in ids, f"dangling predecessor {p}"


def test_sequence_covers_every_category_the_classifier_can_emit():
    from app.lib.boq_pricing import CATS
    known = {c[0] for c in CATS} | {"Other/Uncategorized"}
    assert known <= set(CONSTRUCTION_SEQUENCE), known - set(CONSTRUCTION_SEQUENCE)


def test_an_unrecognised_line_is_still_scheduled_and_reported():
    """Unknown work must never vanish from the programme — and the planner
    must be told the taxonomy did not understand it."""
    from app.lib.boq_schedule import uncategorized
    items = BOQ + [{"item_key": "novel", "description": "Zzz unknown novel work",
                    "quantity": 10, "unit": "no"}]
    assert "Zzz unknown novel work" in uncategorized(items, OVERRIDES)
    rates = dict(RATES); rates["Other/Uncategorized"] = 5
    acts = activities_from_boq(items, productivity=rates, category_overrides=OVERRIDES)
    assert any("novel work" in a["name"].lower() for a in acts)
    # and it sorts last, after the recognised packages
    assert acts[-1]["wbs_phase"] == "other_uncategorized"


def test_an_override_moves_a_line_to_the_right_package():
    """Without the override the chilled-water line schedules as waterproofing."""
    default = group_by_category(BOQ)
    assert any("Chilled water" in (i["description"]) for i in
               default["Waterproofing/Insulation"])
    corrected = group_by_category(BOQ, OVERRIDES)
    assert any("Chilled water" in (i["description"]) for i in
               corrected["Mechanical/HVAC (MEP)"])
    assert "Waterproofing/Insulation" not in corrected


# ── missing productivity is refused, never defaulted ───────────────────────

def test_missing_productivity_refuses_and_names_the_gap():
    with pytest.raises(MissingProductivity) as exc:
        activities_from_boq(BOQ, productivity={"Structural Steel": 4},
                            category_overrides=OVERRIDES)
    msg = str(exc.value)
    assert "Windows/Doors/Facade" in msg and "m2" in msg
    assert "Mechanical/HVAC (MEP)" in msg
    # the machine-readable form drives the operator question
    assert exc.value.missing["Earthworks/Excavation"] == "m3"


def test_no_category_carries_a_built_in_rate():
    """Process, not facts: nothing in the module may supply an output rate."""
    import inspect

    from app.lib import boq_schedule
    src = inspect.getsource(boq_schedule)
    body = src.split("CONSTRUCTION_SEQUENCE")[0]
    assert "per_crew_day = " not in body
    with pytest.raises(MissingProductivity):
        activities_from_boq(BOQ, productivity={}, category_overrides=OVERRIDES)


def test_basis_states_what_every_duration_rests_on():
    basis = " ".join(schedule_basis(RATES, crews={"Structural Steel": 2}))
    assert "quantity /" in basis
    assert "Structural Steel: 4 per crew-day x 2 crews" in basis
    assert "not assumed" in basis


# ── the endpoint: BOQ in, real programme out ───────────────────────────────

@pytest.mark.asyncio
async def test_endpoint_builds_a_workbook_from_the_boq(tmp_path, monkeypatch):
    """End-to-end through the route: BOQ line items -> CPM -> workbook."""
    import httpx
    from openpyxl import Workbook, load_workbook

    from app import main as app_main
    from app.core import projects as projects_store
    from app.routers import exports as exports_mod
    from app.dependencies import require_user

    src = tmp_path / "priced_boq.xlsx"
    wb = Workbook(); ws = wb.active
    ws.append(["Item", "Description", "Unit", "Qty", "Rate", "Amount"])
    for i, r in enumerate(BOQ):
        ws.append([f"{i+1}.0", r["description"], r["unit"], r["quantity"],
                   r["unit_cost"], r["total_cost"]])
    wb.save(src)

    monkeypatch.setattr(projects_store, "get_document",
                        lambda doc_id: {"id": doc_id, "file_path": str(src)},
                        raising=False)
    monkeypatch.setattr(exports_mod, "_check_owner",
                        lambda pid, uid: {"name": "Fence Project"}, raising=False)
    app_main.app.dependency_overrides[require_user] = lambda: {"user_id": "u1"}
    try:
        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            res = await client.post(
                "/v1/projects/p1/export/schedule-from-boq",
                json={"document_id": "d1", "productivity": RATES,
                      "category_overrides": OVERRIDES, "start_date": "2026-09-01"})
    finally:
        app_main.app.dependency_overrides.pop(require_user, None)

    assert res.status_code == 200, res.text[:300]
    assert int(res.headers["X-Activities"]) == len(BOQ)
    assert int(res.headers["X-Duration-Days"]) > 0

    out = tmp_path / "out.xlsx"
    out.write_bytes(res.content)
    book = load_workbook(out)
    assert "L2 Schedule" in book.sheetnames
    names = " ".join(str(r[2]).lower() for r in
                     book["L2 Schedule"].iter_rows(min_row=4, values_only=True) if r and r[2])
    for term in ("truss", "cladding", "chilled water", "excavation"):
        assert term in names, f"{term!r} missing from the generated schedule"


@pytest.mark.asyncio
async def test_endpoint_refuses_and_names_the_missing_rates(tmp_path, monkeypatch):
    """The operator must be asked for the exact rates, not given a guess."""
    import httpx
    from openpyxl import Workbook

    from app import main as app_main
    from app.core import projects as projects_store
    from app.routers import exports as exports_mod
    from app.dependencies import require_user

    src = tmp_path / "b.xlsx"
    wb = Workbook(); ws = wb.active
    ws.append(["Item", "Description", "Unit", "Qty", "Rate", "Amount"])
    ws.append(["1.0", "Bulk excavation to reduced level", "m3", 2500, 18.5, 46250])
    wb.save(src)

    monkeypatch.setattr(projects_store, "get_document",
                        lambda doc_id: {"id": doc_id, "file_path": str(src)},
                        raising=False)
    monkeypatch.setattr(exports_mod, "_check_owner",
                        lambda pid, uid: {"name": "P"}, raising=False)
    app_main.app.dependency_overrides[require_user] = lambda: {"user_id": "u1"}
    try:
        transport = httpx.ASGITransport(app=app_main.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            res = await client.post("/v1/projects/p1/export/schedule-from-boq",
                                    json={"document_id": "d1", "productivity": {}})
    finally:
        app_main.app.dependency_overrides.pop(require_user, None)

    assert res.status_code == 422
    body = res.json()
    detail = body.get("detail", body)
    assert "Earthworks/Excavation" in json.dumps(detail)
    assert "m3" in json.dumps(detail)
