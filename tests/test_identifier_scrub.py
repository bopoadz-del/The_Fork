"""Confidentiality scrub — project/client names must not leak into answers.

The rules under test are SYNTHETIC and injected through RAG_SCRUB_RULES. The
real denylist is the client's own names, which is why it is env-fed and why
this file must never contain it: scripts/scan_secrets.py fails closed on it.
"""
from __future__ import annotations

import importlib

import pytest

SYNTHETIC_RULES = (
    r"\bAG2\s+Infra\s+Pack\s*1\b => the project\n"
    r"\bInfra\s+Pack\s*1\b => the project\n"
    r"\bAcmegate(?:\s+Gate)?\b => the project\n"
    r"\bAG\s?II\b => the project\n"
    r"\bAG2\b => the project\n"
    r"\bZED\b => the contractor\n"
)


@pytest.fixture
def scrub(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "true")
    monkeypatch.setenv("RAG_SCRUB_RULES", SYNTHETIC_RULES)
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    mod = importlib.reload(importlib.import_module("app.core.identifier_scrub"))
    return mod.scrub_identifiers


def test_project_names_replaced(scrub):
    assert scrub("The AG2 Infra Pack 1 sewer network") == "The the project sewer network"
    assert "Acmegate" not in scrub("Works at Acmegate are ongoing")
    assert "AG2" not in scrub("the AG2 scope")
    assert "AGII" not in scrub("reference XYZ-AGII-5.4")


def test_multiword_wins_over_substring(scrub):
    # Must not leave a dangling "Infra Pack 1" after replacing the project code.
    out = scrub("AG2 Infra Pack 1")
    assert out == "the project"
    assert "Infra Pack" not in out


def test_client_and_third_party(scrub):
    assert scrub("the client is the developer") == "the client is the developer"
    assert "ZED" not in scrub("templates from ZED")


def test_extra_terms_env(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "true")
    monkeypatch.setenv("RAG_SCRUB_RULES", SYNTHETIC_RULES)
    monkeypatch.setenv("RAG_SCRUB_EXTRA_TERMS", "Acme, Northgate")
    mod = importlib.reload(importlib.import_module("app.core.identifier_scrub"))
    assert "Acme" not in mod.scrub_identifiers("Acme won the tender")
    assert "Northgate" not in mod.scrub_identifiers("the Northgate plot")


def test_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "0")
    monkeypatch.setenv("RAG_SCRUB_RULES", SYNTHETIC_RULES)
    mod = importlib.reload(importlib.import_module("app.core.identifier_scrub"))
    s = "AG2 Infra Pack 1 for the client"
    assert mod.scrub_identifiers(s) == s


def test_empty_and_none_safe(scrub):
    assert scrub("") == ""
    assert scrub("no identifiers here") == "no identifiers here"


# ── Sources-panel filename scrub (phase-4 make-it-work audit A) ──────────
#
# The answer TEXT was scrubbed in _finalize while the SSE end-event sources
# panel shipped original filenames verbatim; underscore-bound identifiers
# ("AGII_MS-001.pdf") additionally defeat the prose rules' \b boundaries.

def test_filename_scrub_catches_underscore_bound_identifiers(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "1")
    monkeypatch.setenv("RAG_SCRUB_RULES", SYNTHETIC_RULES)
    from app.core.identifier_scrub import scrub_identifiers, scrub_identifiers_filename
    leaked = "AGII_MS-001_Earthworks.pdf"
    # the prose scrub alone does NOT catch it (that was the leak)
    assert "AGII" in scrub_identifiers(leaked)
    assert "AGII" not in scrub_identifiers_filename(leaked)
    assert "the project" in scrub_identifiers_filename(leaked)


def test_sources_panel_scrubs_non_own_layers(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "1")
    monkeypatch.setenv("RAG_SCRUB_RULES", SYNTHETIC_RULES)
    from app.agents import runtime
    audit = {"project_id": "p1", "chunks": [
        {"doc_id": "d1", "chunk_index": 0, "chunk_id": "c1", "score": 0.9,
         "layer": "master_corpus"},
        {"doc_id": "d2", "chunk_index": 1, "chunk_id": "c2", "score": 0.8,
         "layer": "own"},
    ]}
    names = {"d1": "Acmegate_Gate_Spec.pdf", "d2": "Acmegate_Gate_Spec.pdf"}

    class _P:
        @staticmethod
        def get_document(doc_id):
            return {"original_name": names[doc_id]}
    import app.core.projects as projects_mod
    monkeypatch.setattr(projects_mod, "get_document", _P.get_document, raising=False)
    out = runtime._build_sources_from_audit(audit, "the answer text")
    by_layer = {s["layer"]: s for s in out}
    assert "Acmegate" not in by_layer["master_corpus"]["doc_name"]
    # the user's OWN document keeps its real name
    assert "Acmegate" in by_layer["own"]["doc_name"]


def test_enabled_with_no_rules_logs_loudly_and_passes_text_through(monkeypatch, caplog):
    """Cannot refuse to serve, must never be silent."""
    import logging
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "1")
    monkeypatch.delenv("RAG_SCRUB_RULES", raising=False)
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    mod = importlib.reload(importlib.import_module("app.core.identifier_scrub"))
    with caplog.at_level(logging.ERROR, logger="app.core.identifier_scrub"):
        assert mod.scrub_identifiers("AG2 site") == "AG2 site"
    assert mod.rules_loaded() == 0
    assert any("RAG_SCRUB_RULES is empty" in r.message for r in caplog.records)


def test_mutation_probe_a_hardcoded_default_rule_would_be_a_leak(monkeypatch):
    """MUTATION PROBE: the module must carry no client pattern of its own.
    Any rule present with the environment EMPTY is a rule that lives in git."""
    monkeypatch.delenv("RAG_SCRUB_RULES", raising=False)
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    mod = importlib.reload(importlib.import_module("app.core.identifier_scrub"))
    assert mod._rules_from_env() == []
