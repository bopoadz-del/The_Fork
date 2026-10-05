"""Need plan: every figure and fact has a declared source, enforced in code.

One synthetic topic -- a waterproofing membrane's lap width -- lives in three
places: the project specification (150 mm), a reference handbook in general
knowledge (100 mm), and sometimes the user's own question. Depending on what
the question needs, code fetches it from a different home. The plans below
stand in for the planner's output; everything after the plan is code.
Synthetic documents and figures only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.agents import need_plan as np_
from app.agents.citation_provenance import figure_provenance


@dataclass
class _Chunk:
    text: str
    doc_id: str
    source_name: str
    page: int
    chunk_id: str = "c"
    score: float = 0.9


PROJECT = [_Chunk("Clause 5.3 Membrane laps: side and end laps shall be 150 mm minimum.",
                  "d_spec", "Roofing Specification Rev B.pdf", 41)]
GENERAL = [_Chunk("Table 2 Typical membrane lap width is 100 mm for torch-applied sheets.",
                  "d_hand", "Waterproofing Handbook.pdf", 17)]


async def _extract(what, chunks):
    """Stand-in for the extraction call: report the first number+unit stated."""
    import re
    for i, c in enumerate(chunks):
        m = re.search(r"(\d+) mm", c.text)
        if m:
            return {"excerpt": i, "value": m.group(1), "unit": "mm", "quote": m.group(0)}
    return {}


def _resolve(needs, question, project=PROJECT, general=GENERAL):
    return asyncio.run(np_.resolve(
        needs, question,
        fetch_project=lambda w: list(project),
        fetch_general=lambda w: list(general),
        extract=_extract,
    ))


def _need(**kw):
    return np_.Need(**kw)


# ── one topic, four routes ────────────────────────────────────────────────

def test_project_route_reads_the_project_document_with_its_page():
    ctx = _resolve([_need(id="n1", kind="project_fact", what="membrane lap width")],
                   "What lap width does our roofing specification require?")
    (fact,) = ctx.facts
    assert (fact.source, fact.value, fact.doc_id, fact.page) == (np_.SOURCE_PROJECT, "150", "d_spec", 41)


def test_code_route_reads_general_knowledge_only():
    ctx = _resolve([_need(id="n1", kind="code_rule", what="typical membrane lap width")],
                   "What lap width does the handbook give for torch-applied sheets?")
    (fact,) = ctx.facts
    assert (fact.source, fact.value, fact.doc_id, fact.page) == (np_.SOURCE_GENERAL, "100", "d_hand", 17)


def test_calculator_route_states_the_calculator_result():
    needs = [
        _need(id="l", kind="user_value", what="pit length", value="3.4 m"),
        _need(id="w", kind="user_value", what="pit width", value="2.6 m"),
        _need(id="d", kind="user_value", what="pit depth", value="1.15 m"),
        _need(id="v", kind="computed", what="pit volume", calculation="excavation_volume",
              inputs={"length_m": "l", "width_m": "w", "depth_m": "d"}),
    ]
    ctx = _resolve(needs, "How much do I dig for a pit 3.4 m by 2.6 m and 1.15 m deep?")
    (calc,) = ctx.calculator_facts()
    assert calc.formula == "excavation_volume"
    assert calc.inputs == {"length_m": 3.4, "width_m": 2.6, "depth_m": 1.15}
    assert abs(float(calc.value) - 3.4 * 2.6 * 1.15) < 0.01


def test_missing_input_route_asks_and_states_no_figure():
    needs = [
        _need(id="a", kind="user_value", what="membrane area", value="420 m2"),
        _need(id="r", kind="missing_input", what="laying rate per day"),
        _need(id="t", kind="computed", what="laying duration", calculation="excavation_volume",
              inputs={"length_m": "a", "width_m": "r", "depth_m": "a"}),
    ]
    ctx = _resolve(needs, "How long to lay 420 m2 of membrane?", project=[], general=[])
    assert [n.what for n in ctx.missing][0] == "laying rate per day"
    assert not ctx.calculator_facts()
    assert "Ask the user" in np_.facts_block(ctx)


# ── precedence ───────────────────────────────────────────────────────────

def test_user_value_beats_project_value():
    ctx = _resolve([_need(id="n1", kind="user_value", what="membrane lap width", value="120 mm")],
                   "Using a 120 mm lap, how many rolls do I need?")
    (fact,) = ctx.facts
    assert (fact.source, fact.value) == (np_.SOURCE_USER, "120 mm")


def test_a_user_value_not_in_the_question_is_not_a_user_value():
    ctx = _resolve([_need(id="n1", kind="user_value", what="membrane lap width", value="120 mm")],
                   "How many rolls do I need for the roof?")
    (fact,) = ctx.facts
    assert fact.source == np_.SOURCE_PROJECT and fact.value == "150"


def test_project_beats_general_and_states_the_difference():
    ctx = _resolve([_need(id="n1", kind="project_fact", what="membrane lap width")],
                   "What lap width applies on this roof?")
    (fact,) = ctx.facts
    assert fact.source == np_.SOURCE_PROJECT and fact.value == "150"
    assert "100" in (fact.note or "") and "project value is used" in fact.note


def test_general_value_is_used_and_marked_when_the_project_is_silent():
    ctx = _resolve([_need(id="n1", kind="project_fact", what="membrane lap width")],
                   "What lap width applies on this roof?", project=[])
    (fact,) = ctx.facts
    assert fact.source == np_.SOURCE_GENERAL and fact.value == "100"
    assert "general assumption" in fact.note


def test_an_unverified_quote_is_not_evidence():
    async def lying_extract(what, chunks):
        return {"excerpt": 0, "value": "175", "unit": "mm", "quote": "laps shall be 175 mm"}

    ctx = asyncio.run(np_.resolve(
        [_need(id="n1", kind="project_fact", what="membrane lap width")],
        "What lap width applies?", fetch_project=lambda w: PROJECT,
        fetch_general=lambda w: [], extract=lying_extract,
    ))
    assert not ctx.facts and ctx.missing


# ── calculator authority and figure provenance ───────────────────────────

def _user(text):
    return [{"role": "user", "content": text}]


def test_a_computed_number_with_no_calculator_behind_it_is_not_stated():
    ctx = np_.NeedContext(needs=[], facts=[], missing=[])
    answer = "You will dig 10.17 m3. Allow for working space."
    out, prov = figure_provenance(answer, None, _user("How much do I dig for the pit?"), ctx)
    assert "10.17" not in out
    assert "working space" in out
    assert prov == []


def test_the_calculator_result_is_credited_with_formula_and_inputs():
    needs = [
        _need(id="l", kind="user_value", what="pit length", value="3.4 m"),
        _need(id="w", kind="user_value", what="pit width", value="2.6 m"),
        _need(id="d", kind="user_value", what="pit depth", value="1.15 m"),
        _need(id="v", kind="computed", what="pit volume", calculation="excavation_volume",
              inputs={"length_m": "l", "width_m": "w", "depth_m": "d"}),
    ]
    q = "How much do I dig for a pit 3.4 m by 2.6 m and 1.15 m deep?"
    ctx = _resolve(needs, q)
    volume = round(3.4 * 2.6 * 1.15, 2)
    out, prov = figure_provenance(f"The pit volume is {volume} m3 (3.4 m x 2.6 m x 1.15 m).",
                                  None, _user(q), ctx)
    assert f"{volume} m3" in out
    calc = [e for e in prov if e["source"] == np_.SOURCE_CALCULATOR]
    assert calc and calc[0]["formula"] == "excavation_volume"
    assert {e["source"] for e in prov if e.get("figure", "").startswith("3.4")} == {np_.SOURCE_USER}


def test_a_figure_from_a_document_is_credited_to_document_and_page():
    ctx = _resolve([_need(id="n1", kind="project_fact", what="membrane lap width")],
                   "What lap width applies on this roof?")
    out, prov = figure_provenance("Laps must be at least 150 mm (handbook typical: 100 mm).",
                                  None, _user("What lap width applies on this roof?"), ctx)
    by_fig = {e["figure"]: e for e in prov if "figure" in e}
    assert by_fig["150 mm"]["source"] == np_.SOURCE_PROJECT and by_fig["150 mm"]["page"] == 41


# ── wiring: post-processing records it, the message store keeps it ───────

def test_postprocess_records_provenance_and_enforces_only_with_a_plan():
    from app.agents.runtime import _record_figure_provenance

    audit: dict = {}
    np_.CURRENT.set(None)
    answer = "Dig 10.17 m3."
    assert _record_figure_provenance(answer, None, _user("How much?"), audit) == answer
    assert "provenance" in audit  # recorded even without a plan

    np_.CURRENT.set(np_.NeedContext())
    out = _record_figure_provenance(answer, None, _user("How much?"), audit)
    assert "10.17" not in out
    np_.CURRENT.set(None)


def test_the_assistant_message_stores_its_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.core import agent_memory

    agent_memory.init_db()
    conv = agent_memory.get_or_create_conversation("conv-prov-test", "heavy-reasoning")
    entries = [{"figure": "150 mm", "source": np_.SOURCE_PROJECT, "doc_id": "d_spec", "page": 41}]
    np_.LAST_PROVENANCE.set(entries)
    msg = agent_memory.append_message(conv["id"], "assistant", "Laps are 150 mm.")
    np_.LAST_PROVENANCE.set(None)
    assert msg["provenance"] == entries
    assert agent_memory.append_message(conv["id"], "user", "thanks")["provenance"] is None
