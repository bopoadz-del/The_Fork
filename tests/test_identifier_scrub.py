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
