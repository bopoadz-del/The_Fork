"""A knowledge-base hit is cited by its title and its layer.

The stored file name stays available so a later read can fetch it. What a
person sees is the document's title, or the file name cleaned to words when
the document states no title, and a layer label that says knowledge base or
project document. The same rule for every hit; nothing here names one file.
"""
from __future__ import annotations

import asyncio

from app.agents.base.knowledge import handle_search_general_knowledge
from app.agents.core.tool_registry import ToolCall


def _call(hits):
    async def fake_search(project_id, query, top_k):
        return list(hits)

    return fake_search


def test_a_knowledge_hit_is_labelled_by_its_title_and_layer(monkeypatch):
    from app.core import doc_index
    from app.core.rag import retriever

    filename = "productivity_norms_planning_defaults.md"
    title = "Trade Productivity Norms — Published Planning Ranges"
    monkeypatch.setattr(retriever, "_general_knowledge_project_ids", lambda: ["gk"])
    monkeypatch.setattr(doc_index, "search_project_documents", _call([{
        "document_id": "doc-1",
        "filename": filename,
        "origin": "own",
        "score": 0.8,
        "snippet": f"# {title}\n\nA gang places formwork.",
    }]))
    call = ToolCall(agent=None, name="search_general_knowledge",
                    args={"query": "formwork productivity"}, project_id="the-job")
    out = asyncio.run(handle_search_general_knowledge(call))
    hit = out["result"]["results"][0]
    assert hit["filename"] == filename
    assert hit["source_label"] == title
    assert hit["layer_label"] == "Knowledge base"
    assert ".md" not in hit["source_label"]
    assert hit["source_label"] != filename


def test_a_hit_without_a_title_uses_the_cleaned_file_name(monkeypatch):
    from app.core import doc_index
    from app.core.rag import retriever

    monkeypatch.setattr(retriever, "_general_knowledge_project_ids", lambda: ["gk"])
    monkeypatch.setattr(doc_index, "search_project_documents", _call([{
        "document_id": "doc-2",
        "filename": "ab12cd34_site_diary.md",
        "origin": "own",
        "score": 0.5,
        "snippet": "Concrete was poured on the north side.",
    }]))
    call = ToolCall(agent=None, name="search_general_knowledge",
                    args={"query": "pour"}, project_id="the-job")
    out = asyncio.run(handle_search_general_knowledge(call))
    hit = out["result"]["results"][0]
    assert hit["source_label"] == "site diary"
    assert hit["layer_label"] == "Knowledge base"
    assert hit["filename"] == "ab12cd34_site_diary.md"


def test_title_metadata_wins_over_the_file_name(monkeypatch):
    from app.core import doc_index
    from app.core.rag import retriever

    monkeypatch.setattr(retriever, "_general_knowledge_project_ids", lambda: ["gk"])
    monkeypatch.setattr(doc_index, "search_project_documents", _call([{
        "document_id": "doc-3",
        "filename": "notes.md",
        "origin": "own",
        "score": 0.6,
        "metadata": {"title": "Site instructions"},
        "snippet": "See the attached sketch.",
    }]))
    call = ToolCall(agent=None, name="search_general_knowledge",
                    args={"query": "instructions"}, project_id="the-job")
    out = asyncio.run(handle_search_general_knowledge(call))
    hit = out["result"]["results"][0]
    assert hit["source_label"] == "Site instructions"
    assert hit["layer_label"] == "Knowledge base"


def test_a_project_document_hit_is_labelled_as_a_project_document():
    from app.agents.base.knowledge import label_search_hit

    hit = label_search_hit({
        "filename": "section_04_concrete.pdf",
        "origin": "own",
        "snippet": "The slab is 200 mm thick.",
    }, layer="own")
    assert hit["source_label"] == "section 04 concrete"
    assert hit["layer_label"] == "Project document"
    assert hit["filename"] == "section_04_concrete.pdf"
