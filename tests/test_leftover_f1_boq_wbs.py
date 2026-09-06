"""Leftover F1 beyond #521: WBS from BOQ demolition / site-clearance rows.

Live leftover on Master Corpus (f785682 / #521):

    "Answer only from the client project documents. Generate a high-level
     WBS for the demolition and site clearance scope in this project's BOQ."

    Actual FAIL: "Template scaffold, project_type inferred: building —
    not derived from this project's BOQ…" then Site Preparation /
    Substructure / Superstructure. No citations. Hierarchy from #521
    printed, but the tree was still the building template.

These tests pin the election and the glass (no live LLM). A2/A3/A5/A9/
C1/E1 stay off this path. Kill-switch ``BOQ_SCOPE_WBS=0`` restores the
template scaffold.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.runtime import _format_wbs_result
from app.containers.construction import ConstructionContainer
from app.core.predefined_reasoning import (
    deliver_schedule,
    format_wbs_outline,
    message_wants_boq_scope_wbs,
    message_wants_wbs_outline,
    run_workflow,
)
from app.lib.boq_schedule import (
    activities_from_boq_scope_outline,
    filter_demolition_site_clearance_items,
    parse_boq_measured_rows,
    resolve_boq_wbs_project_id,
    wbs_tree_from_boq_items,
)
from app.schemas.project_session import ProjectSession

CATALOG = json.loads(
    (Path(__file__).parent / "fixtures" / "ui_phys" / "questions.json")
    .read_text(encoding="utf-8")
)
LIVE_PREFIX = "Answer only from the client project documents. "
F1_ASK = LIVE_PREFIX + CATALOG["cases"]["F1"]["ask"]
A2_ASK = CATALOG["cases"]["A2"]["ask"]
A3_ASK = CATALOG["cases"]["A3"]["ask"]
A5_ASK = CATALOG["cases"]["A5"]["ask"]
A6_ASK = CATALOG["cases"]["A6"]["ask"]
A9_ASK = CATALOG["cases"]["A9"]["ask"]
C1_ASK = CATALOG["cases"]["C1"]["ask"]
E1_ASK = CATALOG["cases"]["E1"]["ask"]
H1_EXPORT = "export this answer as a Word document"
SCHEDULE_ONLY = "produce the schedule"

S2_BOQ = (Path(__file__).parent / "fixtures" / "ui_phys" / "S2_demolition_boq.md").read_text(
    encoding="utf-8"
)

_BOQ_ITEMS = [
    {"item_key": "D110", "description": "General site clearance", "source": "S2_demolition_boq.md"},
    {"item_key": "D290.1", "description": "Removal of trees in existing sidewalks"},
    {"item_key": "D549.2", "description": "Removal of existing chain link fence"},
    {"item_key": "D599.5", "description": "Breaking out existing carriageway including road markings"},
]


def test_f1_elects_boq_scope_wbs_and_plain_schedule_does_not():
    assert message_wants_wbs_outline(F1_ASK)
    assert message_wants_boq_scope_wbs(F1_ASK)
    assert message_wants_boq_scope_wbs(CATALOG["cases"]["F1"]["ask"])
    assert not message_wants_boq_scope_wbs(SCHEDULE_ONLY)
    assert not message_wants_boq_scope_wbs("Generate a WBS for a 10-storey tower")
    assert not message_wants_boq_scope_wbs(
        "Generate a high-level WBS for the demolition and site clearance scope"
    )


def test_a2_a3_a5_a6_a9_c1_e1_h1_do_not_elect_boq_scope_wbs():
    """Contract Data / spec-precedence / delay-damages / export stay on RAG."""
    from app.core.rag.retriever import (
        query_asks_for_accepted_contract_amount,
        query_asks_for_delay_damages_rate,
        query_asks_for_spec_precedence_list,
        query_asks_for_time_for_completion,
        query_needs_a_monetary_base,
    )

    for ask in (
        A2_ASK, A3_ASK, A5_ASK, A6_ASK, A9_ASK, C1_ASK, E1_ASK,
        LIVE_PREFIX + A6_ASK, LIVE_PREFIX + E1_ASK, H1_EXPORT,
    ):
        assert not message_wants_boq_scope_wbs(ask), ask
        assert not message_wants_wbs_outline(ask), ask

    assert query_asks_for_accepted_contract_amount(A2_ASK)
    assert query_asks_for_time_for_completion(A3_ASK)
    assert query_asks_for_delay_damages_rate(A5_ASK)
    assert query_asks_for_spec_precedence_list(C1_ASK)
    assert query_needs_a_monetary_base(LIVE_PREFIX + E1_ASK) or query_needs_a_monetary_base(
        "Calculate the delay damages per calendar day in SAR for the whole of the Works."
    )


def test_parse_s2_fixture_rows_does_not_invent():
    rows = parse_boq_measured_rows(S2_BOQ)
    codes = {r.get("item_key") for r in rows}
    assert "D110" in codes
    assert "D290.1" in codes
    assert "D549.2" in codes
    assert "D599.5" in codes
    descs = " ".join(r.get("description") or "" for r in rows).lower()
    assert "site clearance" in descs
    assert "trees" in descs
    assert "superstructure" not in descs
    assert "hall a" not in descs
    assert not any("part summary" in (r.get("description") or "").lower() for r in rows)


def test_filter_drops_non_demolition_rows():
    mixed = _BOQ_ITEMS + [
        {"item_key": "C20", "description": "Concrete to columns"},
        {"description": "Hall A slab"},
    ]
    scoped = filter_demolition_site_clearance_items(mixed)
    codes = {r.get("item_key") for r in scoped}
    assert codes == {"D110", "D290.1", "D549.2", "D599.5"}
    tree = wbs_tree_from_boq_items(mixed)
    blob = " ".join(tree.values()).lower()
    assert "d110" in blob and "site clearance" in blob
    assert "superstructure" not in blob
    assert "hall a" not in blob
    assert "concrete to columns" not in blob


def test_tree_and_activities_are_only_retrieved_rows():
    tree = wbs_tree_from_boq_items(_BOQ_ITEMS)
    assert tree["1"] == "Demolition and Site Clearance"
    assert "D110" in tree["1.1"]
    acts = activities_from_boq_scope_outline(_BOQ_ITEMS)
    assert [a["boq"]["item_key"] for a in acts] == ["D110", "D290.1", "D549.2", "D599.5"]
    assert wbs_tree_from_boq_items([]) == {}
    assert activities_from_boq_scope_outline([]) == []


@pytest.mark.asyncio
async def test_generate_wbs_f1_ask_uses_boq_items_not_building_template():
    r = await ConstructionContainer().generate_wbs(
        {"brief": F1_ASK, "boq_items": _BOQ_ITEMS},
        {"target_count": 60},
    )
    assert r["status"] == "success"
    assert r["scaffold"]["derived_from_boq"] is True
    assert r["scaffold"]["source"] == "boq"
    assert "template scaffold" not in r["scaffold"]["declaration"].lower()
    blob = " ".join(r["wbs_tree"].values()).lower()
    assert "site clearance" in blob
    assert "d110" in blob
    assert "trees" in blob
    assert "site preparation" not in blob
    assert "superstructure" not in blob
    names = " ".join(a["name"] for a in r["activities"]).lower()
    assert "d110" in names and "hall a" not in names
    out = _format_wbs_result(r)
    assert "Template scaffold" not in out
    assert "Template activities:" not in out
    assert "BOQ-derived items:" in out
    assert "D110" in out


@pytest.mark.asyncio
async def test_generate_wbs_office_brief_still_ignores_silent_boq():
    """F1b boundary for a generic brief: a leftover boq key is not an election."""
    r = await ConstructionContainer().generate_wbs(
        {
            "brief": "office building, 12 storeys",
            "boq": [{"item_key": "D110", "description": "General site clearance"}],
            "boq_items": [{"item_key": "D110", "description": "General site clearance"}],
        },
        {"target_count": 40},
    )
    assert r["scaffold"]["derived_from_boq"] is False
    assert "template scaffold" in r["scaffold"]["declaration"].lower()
    assert "D110" not in r["wbs_tree"].values()


@pytest.mark.asyncio
async def test_generate_wbs_f1_without_rows_keeps_honest_scaffold():
    r = await ConstructionContainer().generate_wbs(
        {"brief": F1_ASK},
        {"target_count": 40, "project_type": "building"},
    )
    assert r["scaffold"]["derived_from_boq"] is False
    assert "template scaffold" in r["scaffold"]["declaration"].lower()
    blob = " ".join(r["wbs_tree"].values()).lower()
    assert "d110" not in blob


def test_deliver_f1_boq_tree_has_no_template_scaffold():
    s = ProjectSession.new("f1-boq")
    tree = wbs_tree_from_boq_items(_BOQ_ITEMS)
    s.data["schedule_summary"] = {
        "activities": 4,
        "duration_days": 4,
        "critical_count": 4,
    }
    s.data["wbs"] = {
        "wbs_tree": tree,
        "scaffold": {
            "derived_from_boq": True,
            "declaration": (
                "High-level WBS derived from this project's BOQ "
                "demolition and site clearance items."
            ),
        },
    }
    text = deliver_schedule(s, True, F1_ASK)
    assert "Template scaffold" not in text
    assert "D110" in text and "site clearance" in text.lower()
    assert "High-level WBS" in text
    assert "Site Preparation" not in text
    assert format_wbs_outline(tree)


def test_deliver_schedule_only_still_skips_tree():
    s = ProjectSession.new("sched")
    s.data["schedule_summary"] = {
        "activities": 204,
        "duration_days": 688,
        "critical_count": 44,
    }
    s.data["wbs"] = {
        "wbs_tree": wbs_tree_from_boq_items(_BOQ_ITEMS),
        "scaffold": {"derived_from_boq": True, "declaration": "from BOQ"},
    }
    text = deliver_schedule(s, True, SCHEDULE_ONLY)
    assert "Schedule built: 204 activities" in text
    assert "High-level WBS" not in text
    assert "D110" not in text


def test_retrieve_boq_scope_items_parses_named_bill(monkeypatch):
    from app.core.rag.vector_store import Chunk
    from app.lib import boq_schedule as bs

    chunk = Chunk(
        chunk_id="c1",
        project_id="p1",
        doc_id="boq1",
        chunk_index=0,
        text=S2_BOQ,
    )
    chunk.source_name = (
        "DD-2023-118_the client project II Infrastructure Package 1_"
        "Demolition and Site Clearance BOQ.pdf"
    )

    monkeypatch.setattr(
        "app.core.rag.retriever.retrieve_with_filter",
        lambda *a, **k: ([chunk], 0),
    )
    monkeypatch.setattr(
        "app.core.rag.retriever._doc_name_for_id",
        lambda _d: chunk.source_name,
    )
    items = bs.retrieve_boq_scope_items(F1_ASK, "p1")
    codes = {i.get("item_key") for i in items}
    assert "D110" in codes and "D290.1" in codes
    assert all(i.get("source") for i in items)


@pytest.mark.asyncio
async def test_generate_wbs_retrieves_when_project_has_boq(monkeypatch):
    from app.lib import boq_schedule as bs

    monkeypatch.setattr(bs, "retrieve_boq_scope_items", lambda *_a, **_k: list(_BOQ_ITEMS))
    r = await ConstructionContainer().generate_wbs(
        {"brief": F1_ASK},
        {"project_id": "master_corpus", "target_count": 40},
    )
    assert r["scaffold"]["derived_from_boq"] is True
    assert "D110" in " ".join(r["wbs_tree"].values())


def test_kill_switch_restores_template_election(monkeypatch):
    monkeypatch.setenv("BOQ_SCOPE_WBS", "0")
    assert not message_wants_boq_scope_wbs(F1_ASK)
    assert message_wants_wbs_outline(F1_ASK)


@pytest.mark.asyncio
async def test_f1_run_workflow_with_boq_items_is_not_building_template():
    ctx = {
        "message": F1_ASK,
        "project_name": "Master Corpus",
        "project_id": "f1-boq-wf",
        "params": {
            "target_count": 40,
            "start_date": "2026-01-05",
            "boq_items": _BOQ_ITEMS,
        },
    }
    out = await run_workflow("generate_wbs", ctx, ProjectSession.new("f1-boq-wf"))
    assert out["handled"] and out["status"] == "success", out
    answer = out["answer"]
    assert "Template scaffold" not in answer
    assert "D110" in answer
    assert "site clearance" in answer.lower()
    assert "Site Preparation" not in answer
    assert "Superstructure" not in answer
    assert "Hall A" not in answer
    assert "High-level WBS" in answer
    assert "Schedule built:" in answer


def test_master_corpus_alias_resolves_to_source():
    from app.core.projects import MASTER_CORPUS_SOURCE_PROJECT_ID, _master_corpus_source
    assert resolve_boq_wbs_project_id("master_corpus") == (
        _master_corpus_source("master_corpus") or "master_corpus"
    )
    assert resolve_boq_wbs_project_id("master_corpus") == MASTER_CORPUS_SOURCE_PROJECT_ID
    assert resolve_boq_wbs_project_id("drive_archive") == "drive_archive"
    assert resolve_boq_wbs_project_id("") == ""


def test_retrieve_remaps_master_corpus_before_filter(monkeypatch):
    from app.lib import boq_schedule as bs

    seen: list[str] = []

    def fake_retrieve(query, project_id, k=8):
        seen.append(project_id)
        return ([], 0)

    monkeypatch.setattr("app.core.rag.retriever.retrieve_with_filter", fake_retrieve)
    monkeypatch.setattr(bs, "_lexical_rescue_demolition_boq_chunks", lambda *_a, **_k: [])
    assert bs.retrieve_boq_scope_items(F1_ASK, "master_corpus") == []
    assert seen
    assert all(pid == resolve_boq_wbs_project_id("master_corpus") for pid in seen)
    assert "master_corpus" not in seen


def test_retrieve_rescues_when_wbs_query_returns_contract_prose(monkeypatch):
    from app.core.rag.vector_store import Chunk
    from app.lib import boq_schedule as bs

    prose = Chunk(
        chunk_id="c-prose",
        project_id="p1",
        doc_id="contract",
        chunk_index=0,
        text=(
            "The Contractor shall execute the demolition and site clearance "
            "scope described in the BOQ."
        ),
    )
    prose.source_name = "Vol 1 - Conditions of Contract.pdf"
    bill = Chunk(
        chunk_id="c-boq",
        project_id="p1",
        doc_id="boq1",
        chunk_index=0,
        text=S2_BOQ,
    )
    bill.source_name = (
        "DD-2023-118_the client project II Infrastructure Package 1_"
        "Demolition and Site Clearance BOQ.pdf"
    )
    monkeypatch.setattr(
        "app.core.rag.retriever.retrieve_with_filter",
        lambda *a, **k: ([prose], 0),
    )
    monkeypatch.setattr(
        bs, "_lexical_rescue_demolition_boq_chunks", lambda *_a, **_k: [bill],
    )
    items = bs.retrieve_boq_scope_items(F1_ASK, "p1")
    codes = {i.get("item_key") for i in items}
    assert "D110" in codes and "D549.2" in codes
    assert not any("superstructure" in (i.get("description") or "").lower() for i in items)


@pytest.mark.asyncio
async def test_f1_run_workflow_on_master_corpus_is_not_building_template(monkeypatch):
    """Live predefined path: project_id=master_corpus, no injected boq_items."""
    from app.lib import boq_schedule as bs

    monkeypatch.setattr(bs, "retrieve_boq_scope_items", lambda *_a, **_k: list(_BOQ_ITEMS))
    ctx = {
        "message": F1_ASK,
        "project_name": "Master Corpus",
        "project_id": "master_corpus",
        "params": {"target_count": 40, "start_date": "2026-01-05"},
    }
    out = await run_workflow("generate_wbs", ctx, ProjectSession.new("f1-mc"))
    assert out["handled"] and out["status"] == "success", out
    answer = out["answer"]
    assert "Template scaffold" not in answer
    assert "Site Preparation" not in answer
    assert "Superstructure" not in answer
    assert "D110" in answer
    assert "site clearance" in answer.lower()


@pytest.mark.asyncio
async def test_f1_predispatch_passes_project_id_and_renders_boq(monkeypatch):
    """Agent path: live chat predispatched generate_wbs without project_id."""
    from app.agents.runtime import _predispatch_remaining_deliverables

    seen: dict = {}

    class _Container:
        async def generate_wbs(self, data, params):
            seen["params"] = params
            seen["data"] = data
            return {
                "status": "success",
                "wbs_tree": wbs_tree_from_boq_items(_BOQ_ITEMS),
                "scaffold": {
                    "derived_from_boq": True,
                    "declaration": (
                        "High-level WBS derived from this project's BOQ "
                        "demolition and site clearance items."
                    ),
                },
                "actual_count": 4,
                "project_type": "demolition_site_clearance",
                "summary": {"activity_count": 4},
                "activities": [],
            }

    monkeypatch.setattr(
        "app.dependencies.get_block_instance",
        lambda _name: _Container(),
    )

    class _A:
        allowed_blocks = ["construction"]
        name = "construction-pm"

    msgs = [{"role": "user", "content": F1_ASK}]
    out = await _predispatch_remaining_deliverables(_A(), msgs, "master_corpus")
    assert out is not None
    assert out["name"] == "generate_wbs"
    assert seen["params"].get("project_id") == "master_corpus"
    assert seen["data"].get("project_id") == "master_corpus"
    glass = _format_wbs_result(out["result"])
    assert "Template scaffold" not in glass
    assert "D110" in glass
    assert "Site Preparation" not in glass
