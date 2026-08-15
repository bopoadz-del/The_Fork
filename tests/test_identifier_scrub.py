"""Confidentiality scrub — project/client names must not leak into answers."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def scrub(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "true")
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    mod = importlib.import_module("app.core.identifier_scrub")
    return mod.scrub_identifiers


def test_project_names_replaced(scrub):
    assert scrub("The DG2 Infra Pack 1 sewer network") == "The the project sewer network"
    assert "Diriyah" not in scrub("Works at Diriyah are ongoing")
    assert "DG2" not in scrub("the DG2 scope")
    assert "DGII" not in scrub("reference LAAR-DGII-5.4")


def test_multiword_wins_over_substring(scrub):
    # Must not leave a dangling "Infra Pack 1" after replacing DG2.
    out = scrub("DG2 Infra Pack 1")
    assert out == "the project"
    assert "Infra Pack" not in out


def test_client_and_third_party(scrub):
    assert scrub("the client is the developer") == "the client is the developer"
    assert "DPR" not in scrub("templates from DPR")


def test_extra_terms_env(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "true")
    monkeypatch.setenv("RAG_SCRUB_EXTRA_TERMS", "Acme, Northgate")
    mod = importlib.import_module("app.core.identifier_scrub")
    assert "Acme" not in mod.scrub_identifiers("Acme won the tender")
    assert "Northgate" not in mod.scrub_identifiers("the Northgate plot")


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "0")
    mod = importlib.import_module("app.core.identifier_scrub")
    s = "DG2 Infra Pack 1 for the client"
    assert mod.scrub_identifiers(s) == s


def test_empty_and_none_safe(scrub):
    assert scrub("") == ""
    assert scrub("no identifiers here") == "no identifiers here"


# ── Sources-panel filename scrub (phase-4 make-it-work audit A) ──────────
#
# The answer TEXT was scrubbed in _finalize while the SSE end-event sources
# panel shipped original filenames verbatim; underscore-bound identifiers
# ("DGII_MS-001.pdf") additionally defeat the prose rules' \b boundaries.

def test_filename_scrub_catches_underscore_bound_identifiers(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "1")
    from app.core.identifier_scrub import scrub_identifiers, scrub_identifiers_filename
    leaked = "DGII_MS-001_Earthworks.pdf"
    # the prose scrub alone does NOT catch it (that was the leak)
    assert "DGII" in scrub_identifiers(leaked)
    assert "DGII" not in scrub_identifiers_filename(leaked)
    assert "the project" in scrub_identifiers_filename(leaked)


def test_sources_panel_scrubs_non_own_layers(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "1")
    from app.agents import runtime
    audit = {"project_id": "p1", "chunks": [
        {"doc_id": "d1", "chunk_index": 0, "chunk_id": "c1", "score": 0.9,
         "layer": "master_corpus"},
        {"doc_id": "d2", "chunk_index": 1, "chunk_id": "c2", "score": 0.8,
         "layer": "own"},
    ]}
    names = {"d1": "Diriyah_Gate_Spec.pdf", "d2": "Diriyah_Gate_Spec.pdf"}

    class _P:
        @staticmethod
        def get_document(doc_id):
            return {"original_name": names[doc_id]}
    import app.core.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_document", _P.get_document, raising=False)
    out = runtime._build_sources_from_audit(audit, "the answer text")
    by_layer = {s["layer"]: s for s in out}
    assert "Diriyah" not in by_layer["master_corpus"]["doc_name"]
    # the user's OWN document keeps its real name
    assert "Diriyah" in by_layer["own"]["doc_name"]
