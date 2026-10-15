"""A project's document names and project code never reach the model.

Order 2130Z / LANE4 #977 FAIL: the Sources fix alone left the name in the
passage text the model reads -- the project code inside drawing references
and the stored file names. One rule (app.core.document_labels) at every tool
the model reads a project document through. Invented project and code.
"""

from app.agents.base import tools
from app.core import document_labels

DOCS = [
    {"id": "abc123def", "original_name": "QZX-ST-0042 Harbour Tower General Arrangement.pdf", "doc_type": "drawing"},
    {"id": "f00d42aa1", "original_name": "projects/QZX Harbour Tower Specification.docx", "doc_type": "specification"},
    {"id": "77aa00bb", "original_name": "DWG-0001 Key plan.pdf", "doc_type": "drawing"},
]


def test_the_project_code_and_names_become_neutral_in_passage_text():
    guard = document_labels.NameGuard(DOCS)
    out = guard.text("See QZX-ST-0043 and QZX Harbour Tower Specification.docx; cure 7 days per DWG-0099.")
    assert "QZX" not in out and "Harbour Tower" not in out
    assert "[project]-ST-0043" in out and "Specification f00d42" in out
    assert "DWG-0099" in out  # a generic document code is not a project code
    assert "7 days" in out
    assert guard.text("per DWG-0001 Key plan") == "per Drawing 77aa00"


def test_a_word_containing_the_code_is_left_alone():
    guard = document_labels.NameGuard(DOCS)
    assert guard.text("QZXA is not the code; neither is aQZX.") == "QZXA is not the code; neither is aQZX."


def test_a_label_resolves_back_to_its_document():
    guard = document_labels.NameGuard(DOCS)
    assert guard.resolve("Drawing abc123") == "abc123def"
    assert guard.resolve("specification f00d42") == "f00d42aa1"


def test_search_hits_reach_the_model_without_the_name(monkeypatch):
    hits = [{"document_id": "abc123def", "filename": DOCS[0]["original_name"], "doc_type": "drawing", "score": 0.9,
             "title": "QZX Harbour Tower GA", "snippet": "Refer QZX-ST-0042 for the slab edge.", "origin": "own"},
            {"filename": "design_values_metric.md", "snippet": "Library text.", "origin": "general_knowledge"}]
    monkeypatch.setattr(document_labels, "for_project", lambda pid: document_labels.NameGuard(DOCS))
    out = tools._labelled_hits(hits, "p1")
    assert out[0]["filename"] == "Drawing abc123" and "title" not in out[0]
    assert out[0]["snippet"] == "Refer [project]-ST-0042 for the slab edge."
    assert out[1] == hits[1]  # library files keep their names
    assert "QZX" not in str(out[0]) and "Harbour" not in str(out[0])
