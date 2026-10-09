"""Confidentiality scrub — project/client names must not leak into answers.

The rules are STRUCTURAL: identities are derived at run time from the projects
store (the master-corpus source and general-knowledge projects), with rule
parameters in app/core/scrub_rules.json. Every name below is SYNTHETIC and is
created in the test database; no real name may appear in this file
(scripts/scan_secrets.py fails closed on them).
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import uuid

import pytest

from app.core import identifier_scrub
from app.core import projects as projects_mod

SOURCE_NAME = "Zorvath Lagoon Terminal"
SOURCE_CLIENT = "Quillbrook Development Company"
SOURCE_LOCATION = "Vexmoor Heights"
GK_NAME = "Brindlewick Annex Library"
GK_CLIENT = "Ostlemere Holdings"
OTHER_NAME = "Kettlewyn Pumping Works"


def _seed(source_files=(), other_files=(), with_gk=True):
    """Create synthetic source / GK / ordinary projects under fresh ids."""
    from app.core.users import ensure_user_exists

    ensure_user_exists("system", role="admin")
    tag = uuid.uuid4().hex[:8]
    src = projects_mod.create_project(
        SOURCE_NAME, client=SOURCE_CLIENT, location=SOURCE_LOCATION,
        user_id="system", project_id=f"src_{tag}",
    )
    gk = projects_mod.create_project(
        GK_NAME, client=GK_CLIENT, user_id="system", project_id=f"gk_{tag}",
    ) if with_gk else None
    other = projects_mod.create_project(
        OTHER_NAME, client="Sprocketvale Ltd", user_id="system",
        project_id=f"oth_{tag}",
    )
    for name in source_files:
        projects_mod.add_document(project_id=src["id"], original_name=name)
    for name in other_files:
        projects_mod.add_document(project_id=other["id"], original_name=name)
    return src, gk, other


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "true")
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    monkeypatch.delenv("RAG_SCRUB_RULES", raising=False)

    def make(**kw):
        src, gk, other = _seed(**kw)
        monkeypatch.setattr(projects_mod, "MASTER_CORPUS_SOURCE_PROJECT_ID", src["id"])
        monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", gk["id"] if gk else "")
        identifier_scrub._reset_cache()
        return src, gk, other

    yield make
    identifier_scrub._reset_cache()


def test_source_project_identity_and_variants_scrubbed(seeded):
    seeded()
    s = identifier_scrub.scrub_identifiers
    assert s(f"The {SOURCE_NAME} sewer network") == "The the project sewer network"
    assert "Zorvath" not in s("works at Zorvath are ongoing")          # variant
    assert "ZLT" not in s("ref ZLT-RFI-12")                              # acronym
    assert "zlt" in s("a zlt lower-case word")                           # acronym is case-sensitive
    out = s(f"{SOURCE_CLIENT} approved the Quillbrook Development submittal (QDC)")
    assert "Quillbrook" not in out and "QDC" not in out
    assert "the client" in out
    assert "Vexmoor" not in s(f"site at {SOURCE_LOCATION}")


def test_general_knowledge_project_identity_scrubbed(seeded):
    seeded()
    out = identifier_scrub.scrub_identifiers(f"per {GK_NAME} for {GK_CLIENT}")
    assert "Brindlewick" not in out and "Ostlemere" not in out


def test_non_scrubbed_project_keeps_its_own_name(seeded):
    seeded()
    text = f"{OTHER_NAME} and Sprocketvale Ltd"
    assert identifier_scrub.scrub_identifiers(text) == text


def test_multiword_wins_over_substring(seeded):
    seeded()
    out = identifier_scrub.scrub_identifiers(SOURCE_NAME)
    assert out == "the project"


def _code() -> str:
    """A fresh synthetic document code: the suite database is shared, so a code
    reused by an earlier test's project would (correctly) not be unique."""
    return "Z" + "".join(chr(65 + b % 26) for b in uuid.uuid4().bytes[:4])


def test_recurring_filename_code_unique_to_source_is_scrubbed(seeded):
    own, shared = _code(), _code()
    seeded(
        source_files=[f"{own}_MS-001.pdf", f"{own}_MS-002.pdf", f"{own}-DWG-003.pdf",
                      f"{shared}_A.pdf", f"{shared}_B.pdf", f"{shared}-C.pdf",
                      "MS-004.pdf", "MS-005.pdf", "MS-006.pdf"],
        other_files=[f"memo {shared} 2.pdf"],
    )
    s = identifier_scrub.scrub_identifiers
    assert own not in s(f"see {own}-MS-001 rev B")
    assert shared in s(f"see {shared}-A")      # shared with another project
    assert "MS-004" in s("see MS-004")         # generic document-type code


def test_filename_code_below_min_occurrences_is_not_scrubbed(seeded):
    code = _code()
    seeded(source_files=[f"{code}_1.pdf", f"{code}_2.pdf"])
    assert code in identifier_scrub.scrub_identifiers(f"{code}-1")


def test_filename_scrub_catches_underscore_bound_identifiers(seeded, monkeypatch):
    code = _code()
    seeded(source_files=[f"{code}_MS-001.pdf", f"{code}_MS-002.pdf", f"{code}_MS-003.pdf"])
    leaked = f"{code}_MS-001_Zorvath_Earthworks.pdf"
    out = identifier_scrub.scrub_identifiers_filename(leaked)
    assert code not in out and "Zorvath" not in out
    assert "the project" in out
    # A word-boundary rule (an operator's extra term) never fires inside an
    # underscore-bound name in prose; the filename scrub normalises first.
    monkeypatch.setenv("RAG_SCRUB_EXTRA_TERMS", "Plumtree")
    assert "Plumtree" in identifier_scrub.scrub_identifiers("Plumtree_Spec.pdf")
    assert "Plumtree" not in identifier_scrub.scrub_identifiers_filename("Plumtree_Spec.pdf")


def test_sources_panel_scrubs_non_own_layers(seeded, monkeypatch):
    seeded()
    from app.agents import runtime
    audit = {"project_id": "p1", "chunks": [
        {"doc_id": "d1", "chunk_index": 0, "chunk_id": "c1", "score": 0.9,
         "layer": "master_corpus"},
        {"doc_id": "d2", "chunk_index": 1, "chunk_id": "c2", "score": 0.8,
         "layer": "own"},
    ]}
    names = {"d1": "Zorvath_Lagoon_Spec.pdf", "d2": "Zorvath_Lagoon_Spec.pdf"}
    monkeypatch.setattr(projects_mod, "get_document",
                        lambda doc_id: {"original_name": names[doc_id]}, raising=False)
    out = runtime._build_sources_from_audit(audit, "the answer text")
    by_layer = {s["layer"]: s for s in out}
    assert "Zorvath" not in by_layer["master_corpus"]["doc_name"]
    # the user's OWN document keeps its real name
    assert "Zorvath" in by_layer["own"]["doc_name"]


def test_extra_terms_env(seeded, monkeypatch):
    seeded()
    monkeypatch.setenv("RAG_SCRUB_EXTRA_TERMS", "Plumtree, Northgrove")
    assert "Plumtree" not in identifier_scrub.scrub_identifiers("Plumtree won the tender")
    assert "Northgrove" not in identifier_scrub.scrub_identifiers("the Northgrove plot")


def test_disabled_is_noop(seeded, monkeypatch):
    seeded()
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "0")
    s = f"{SOURCE_NAME} for the client"
    assert identifier_scrub.scrub_identifiers(s) == s
    assert identifier_scrub.scrub_identifiers_filename("Zorvath_x.pdf") == "Zorvath_x.pdf"


def test_empty_and_none_safe(seeded):
    seeded()
    assert identifier_scrub.scrub_identifiers("") == ""
    assert identifier_scrub.scrub_identifiers("no identifiers here") == "no identifiers here"


def test_retired_secret_env_var_is_inert(seeded, monkeypatch):
    """The old secret denylist must have NO effect: the rules are structural."""
    seeded()
    monkeypatch.setenv("RAG_SCRUB_RULES", r"\bGribblethorpe\b => the project")
    identifier_scrub._reset_cache()
    assert "Gribblethorpe" in identifier_scrub.scrub_identifiers("Gribblethorpe site")
    assert "RAG_SCRUB_RULES" not in inspect.getsource(identifier_scrub)


def test_module_carries_no_rule_of_its_own(monkeypatch, caplog):
    """MUTATION PROBE: with no scrubbed project in the store and no extra term,
    the scrub derives nothing -- a rule present here would live in git."""
    monkeypatch.setenv("RAG_SCRUB_IDENTIFIERS", "1")
    monkeypatch.delenv("RAG_SCRUB_EXTRA_TERMS", raising=False)
    monkeypatch.setattr(projects_mod, "MASTER_CORPUS_SOURCE_PROJECT_ID",
                        f"absent_{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("RAG_GENERAL_KNOWLEDGE_PROJECTS", "")
    mod = importlib.reload(identifier_scrub)
    try:
        with caplog.at_level(logging.ERROR, logger="app.core.identifier_scrub"):
            assert mod.scrub_identifiers("Zorvath site") == "Zorvath site"
        assert mod.rules_loaded() == 0
        assert any("no rules were derived" in r.message for r in caplog.records)
    finally:
        mod._reset_cache()


def test_unreadable_store_falls_back_and_logs_once(seeded, monkeypatch, caplog):
    seeded()
    monkeypatch.setenv("RAG_SCRUB_EXTRA_TERMS", "Plumtree")

    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(projects_mod, "SessionLocal", boom)
    monkeypatch.setattr(identifier_scrub, "_warned_db", False)
    identifier_scrub._reset_cache()
    with caplog.at_level(logging.ERROR, logger="app.core.identifier_scrub"):
        out = identifier_scrub.scrub_identifiers("Plumtree at Zorvath")
        identifier_scrub._reset_cache()
        identifier_scrub.scrub_identifiers("again")
    assert out == "the project at Zorvath"   # data-file-only rules still apply
    assert sum("projects store unreadable" in r.message for r in caplog.records) == 1


def test_rules_file_holds_parameters_only():
    """The data file must name no project, client, place or company."""
    path = identifier_scrub._RULES_FILE
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert set(spec) == {
        "_doc", "cache_ttl_seconds", "scrubbed_projects", "identity_fields",
        "identity_expansion", "document_number_code", "title_block_labels",
        "extra_terms_replacement",
    }
    assert set(spec["identity_fields"]) <= {"name", "client", "location"}
    for field in spec["identity_fields"].values():
        assert field["replacement"].startswith("the ")
    assert all(v is True or v is False for v in spec["scrubbed_projects"].values())
    for code in spec["document_number_code"]["generic_codes"]:
        assert re.fullmatch(r"[A-Z]{2,6}", code), code
    # No proper noun anywhere outside the prose note and the label regexes.
    allowed_words = {"Project", "Name", "Title"}

    def leaves(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from leaves(v, k)
        elif isinstance(node, list):
            for v in node:
                yield from leaves(v, key)
        elif isinstance(node, str):
            yield key, node

    for key, value in leaves(spec):
        if key == "_doc":
            continue
        for word in re.findall(r"[A-Z][a-z]+", value):
            assert word in allowed_words, (key, value)
    # No configured project id either.
    raw = path.read_text(encoding="utf-8")
    assert projects_mod.MASTER_CORPUS_SOURCE_PROJECT_ID not in raw


def test_ready_reports_rules_loaded_with_seeded_source(seeded):
    seeded()
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        body = client.get("/ready").json()
    assert body["identifier_scrub"]["enabled"] is True
    assert body["identifier_scrub"]["rules_loaded"] > 0
