"""Sources and the answer name a project document by type and short id.

Order 2130Z (LANE4 NAME FLAG, 408d440 curing-time run 2): a project
document's stored file name -- which can carry a client's or project's real
name -- was rendered in the answer text and in Sources. One rule for every
project document: its type and a short id, never its file name or path.
Library files keep their titles. Invented documents.
"""
from app.agents.driver import loop, sources


def _trail(name="Client_Tower_Spec_rev3.pdf"):
    hit = {"document_id": "abc123def", "filename": name, "doc_type": "specification", "score": 0.9,
           "snippet": "Concrete shall cure for 7 days.", "origin": "own"}
    return [{"role": "user", "content": "How long does the concrete cure?"},
            {"role": "tool", "name": "search_project_documents",
             "content": __import__("json").dumps({"result": {"results": [hit]}}), "tool_call_id": "t1"}]


def test_the_sources_panel_shows_type_and_short_id():
    rows = sources.panel(_trail(), [{"doc_id": "abc123def"}], answer="Cure for 7 days.")
    assert rows and rows[0]["doc_name"] == "Specification abc123"


def test_a_file_name_in_the_answer_text_becomes_the_label():
    out, _trail_out, _report = loop.exit_check("Per Client_Tower_Spec_rev3.pdf, cure for 7 days.", _trail(), shared=True)
    assert "Client_Tower" not in out and "Specification abc123" in out
