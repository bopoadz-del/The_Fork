"""A project's identity never leaves in an answer, a citation or Sources.

Order 2210Z (the #1 product defect): LANE3 saw a project name in 7 answers,
LANE4 the project abbreviation inside drawing references. The identity set is
derived from the project's own record and documents, stored with the project,
and every member becomes the neutral project id at the exit. Invented
project, client, site, codes and contract number throughout.
"""
from __future__ import annotations

import json

from app.agents import answer_exit
from app.core import project_identity

RECORD = {
    "id": "a1b2c3d4e5",
    "name": "Qelvar Harbour Tower Development Phase 2",
    "client": "Morrowind Estates Holding",
    "location": "Port Avela",
    "profile": {"contract_no": "QHT-C-2291", "discipline": "structural", "contract_form": "FIDIC Red Book"},
}
DOCS = ["QHT-ST-0042 General Arrangement.pdf", "QHT-AR-0101 Floor plans.pdf", "DWG-0007 Key plan.pdf"]


def _red():
    return project_identity.Redactor(RECORD["id"], project_identity.derive(RECORD, DOCS, general={"DWG"}))


def test_the_set_comes_from_the_record_and_the_documents():
    members = set(project_identity.derive(RECORD, DOCS, general={"DWG"}))
    assert {"Qelvar Harbour Tower Development Phase 2", "Qelvar Harbour Tower", "Morrowind Estates Holding",
            "Port Avela", "QHT-C-2291", "QHT", "MEH"} <= members
    # A discipline, a contract form and a generic document code are not identity.
    assert not members & {"structural", "FIDIC Red Book", "DWG"}


def test_an_answer_citing_a_drawing_by_its_real_reference_comes_out_neutral():
    red = _red()
    out = red.text("Per drawing QHT-ST-0043 (Qelvar Harbour Tower), the slab at Port Avela is 250 mm; "
                   "contract QHT-C-2291 with morrowind estates holding governs.")
    for leaked in ("QHT", "Qelvar", "Harbour Tower", "Port Avela", "Morrowind", "morrowind"):
        assert leaked not in out
    assert "PRJ-a1b2c3-ST-0043" in out and "250 mm" in out


def test_a_word_containing_a_member_is_left_alone():
    red = _red()
    assert red.text("QHTX and aQHT stay; Port Avelanche stays.") == "QHTX and aQHT stay; Port Avelanche stays."


def test_the_exit_redacts_answers_and_sources_for_the_turns_project():
    token = project_identity.set_current(_red())
    try:
        text = answer_exit.check_text("The QHT-ST-0042 drawing for Qelvar Harbour Tower shows the core walls.")
        rows = answer_exit.check_sources([{"doc_name": "Qelvar Harbour Tower spec", "page_or_section": "p. 3"}])
    finally:
        project_identity.reset(token)
    assert "QHT" not in text and "Qelvar" not in text and "PRJ-a1b2c3" in text
    assert "Qelvar" not in json.dumps(rows)


def test_without_a_project_nothing_changes():
    assert answer_exit.check_text("Qelvar Harbour Tower QHT-ST-0042.") == "Qelvar Harbour Tower QHT-ST-0042."


def test_the_set_is_stored_with_the_project_at_ingest():
    from app.core import projects

    project = projects.create_project(RECORD["name"], client=RECORD["client"], location=RECORD["location"])
    projects.add_document(project["id"], original_name=DOCS[0])
    stored = project_identity._stored(project["id"])
    assert "QHT" in stored["derived"] and "Qelvar Harbour Tower" in stored["derived"]
    assert stored["added"] == []


def test_a_code_the_platform_uses_is_never_identity(monkeypatch):
    """No list of codes in code: a currency in the platform's currency table,
    or a whole word of its library corpus, is general vocabulary."""
    from app.core import document_labels

    monkeypatch.setattr(document_labels, "_in_library", lambda code: code == "HVAC")
    members = set(project_identity.derive({"id": "zz9", "name": "Qelvar Yard"},
                                          ["AED-rates 2026.pdf", "HVAC-0003 Riser.pdf", "QYD-ST-0001.pdf"]))
    assert "QYD" in members and "AED" not in members and "HVAC" not in members
    red = project_identity.Redactor("zz9", members)
    assert red.text("Rate 308.83 AED/m3 per QYD-ST-0001; HVAC riser.") == "Rate 308.83 AED/m3 per PRJ-zz9-ST-0001; HVAC riser."


def test_search_passages_reach_the_model_without_the_projects_identity(monkeypatch):
    """LANE4 #977: chunk text is run through the identity set too, not only
    the file-name labels -- here a set from the project record alone."""
    from app.agents.base import tools
    from app.core import document_labels

    red = project_identity.Redactor(RECORD["id"], project_identity.derive(RECORD, [], general=set()))
    monkeypatch.setattr(project_identity, "redactor_for", lambda pid: red)
    monkeypatch.setattr(document_labels, "for_project", lambda pid: document_labels.NameGuard([], general=set()))
    hits = [{"document_id": "abc123def", "filename": "notes.pdf", "score": 0.8, "origin": "own",
             "snippet": "Morrowind Estates Holding requires the slab at Port Avela by May."},
            {"filename": "design_values_metric.md", "origin": "general_knowledge",
             "snippet": "As on the Qelvar Harbour Tower job, use C40."}]
    out = tools._labelled_hits(hits, RECORD["id"])
    text = str(out)
    assert "Morrowind" not in text and "Port Avela" not in text and "Qelvar" not in text
    assert out[0]["snippet"] == "PRJ-a1b2c3 requires the slab at PRJ-a1b2c3 by May."
    assert out[1]["filename"] == "design_values_metric.md"


def test_the_driver_exit_neutralises_a_real_drawing_reference():
    """Order 2210Z's test, through the driver's own exit (loop.exit_check)."""
    from app.agents.driver import loop

    token = project_identity.set_current(_red())
    try:
        out, _trail, _report = loop.exit_check(
            "Drawing QHT-ST-0043 shows the Qelvar Harbour Tower core wall reinforcement.",
            [{"role": "user", "content": "What does drawing QHT-ST-0043 show for the core wall?"}], shared=True)
    finally:
        project_identity.reset(token)
    assert "QHT" not in out and "Qelvar" not in out
    assert "PRJ-a1b2c3-ST-0043" in out


def test_a_failed_refresh_still_applies_the_stored_set(monkeypatch):
    """Not fail-open: when the set cannot be re-derived, the stored one applies."""
    def boom(pid):
        raise RuntimeError("database away")

    monkeypatch.setattr(project_identity, "refresh", boom)
    monkeypatch.setattr(project_identity, "_stored", lambda pid: {"derived": ["Qelvar Harbour Tower"], "added": []})
    red = project_identity.redactor_for("a1b2c3d4e5")
    assert red is not None and red.text("Qelvar Harbour Tower") == "PRJ-a1b2c3"
