"""Sanitized UI-PHYS catalog stays complete and free of live client figures.

The confidential Drive pack must never land in git. This file is the CI
gate that the Playwright nightly can trust: same case IDs / assertion
shapes, fixture-only numbers, every S1–S6 source present on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ui_phys"
CATALOG = FIXTURE_DIR / "questions.json"

REQUIRED_IDS = (
    [f"A{i}" for i in range(1, 10)]
    + [f"B{i}" for i in range(1, 7)]
    + [f"C{i}" for i in range(1, 5)]
    + [f"D{i}" for i in range(1, 4)]
    + [f"E{i}" for i in range(1, 5)]
    + [f"F{i}" for i in range(1, 4)]
    + [f"G{i}" for i in range(1, 7)]
    + ["H1"]
)
NIGHTLY_IDS = ["A1", "B1", "E4", "G1", "G5", "H1"]


@pytest.fixture(scope="module")
def catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_catalog_declares_sanitized_variant(catalog):
    assert catalog["variant"] == "sanitized"
    assert catalog["do_not_commit_live_client_pack"] is True
    assert catalog["project_name"] == "UI-PHYS Fixture"


def test_catalog_has_the_real_set_case_ids(catalog):
    cases = catalog["cases"]
    missing = [cid for cid in REQUIRED_IDS if cid not in cases]
    assert missing == [], f"sanitized catalog is missing case ids: {missing}"


def test_nightly_subset_is_defined(catalog):
    assert catalog["nightly_ids"] == NIGHTLY_IDS
    for cid in NIGHTLY_IDS:
        assert cid in catalog["cases"]
        if cid != "H1":
            assert catalog["cases"][cid]["ask"]


def test_source_files_exist(catalog):
    for key, name in catalog["sources"].items():
        path = FIXTURE_DIR / name
        assert path.is_file(), f"{key} fixture missing: {path}"
        assert path.stat().st_size > 40


def _forbidden_terms(catalog):
    """Canary terms come from the UI_PHYS_FORBIDDEN env secret, never the repo.

    The canary list is itself confidential: committing live client strings
    to name what must not leak IS the leak (the #330 class). CI injects the
    secret; locally the check skips LOUDLY so an unconfigured run is never
    mistaken for a clean one.
    """
    import os

    env_terms = [
        t.strip()
        for t in os.getenv("UI_PHYS_FORBIDDEN", "").splitlines()
        if t.strip()
    ]
    return env_terms + list(catalog.get("forbidden_substrings") or [])


def test_no_live_client_figures_in_the_fixture_tree(catalog):
    import pytest

    terms = _forbidden_terms(catalog)
    if not terms:
        pytest.skip(
            "UI_PHYS_FORBIDDEN not configured — client-string leak check NOT RUN. "
            "Set the CI secret (newline-separated terms) to enforce."
        )
    blob = ""
    for path in FIXTURE_DIR.glob("*.md"):
        blob += path.read_text(encoding="utf-8")
    for case in catalog["cases"].values():
        blob += " " + str(case.get("ask") or "")
        blob += " " + " ".join(case.get("must") or [])
        blob += " " + " ".join(case.get("must_any") or [])
        blob += " " + " ".join(case.get("cite_any") or [])
    hits = [term for term in terms if term in blob]
    assert hits == [], f"live client strings leaked into the sanitized fixture: {hits}"


def test_llm_stub_covers_nightly_asks(catalog):
    """The Playwright stub must recognize every asked nightly prompt."""
    from tests.browser._llm_stub import _answer_for

    for cid in ("A1", "B1", "E4", "G1", "G5"):
        ask = catalog["cases"][cid]["ask"]
        out = _answer_for(ask)
        assert out, f"stub has no answer for {cid}"
        for token in catalog["cases"][cid].get("must") or []:
            assert token in out, f"{cid} stub missing {token!r}"
        if catalog["cases"][cid].get("must_any"):
            assert any(
                tok.lower() in out.lower()
                for tok in catalog["cases"][cid]["must_any"]
            ), f"{cid} stub missed must_any"


def test_nightly_cases_name_an_agent(catalog):
    """Per-role assertions: each asked nightly case pins a hat."""
    for cid in ("A1", "B1", "E4", "G1", "G5"):
        agent = catalog["cases"][cid].get("agent")
        assert agent, f"{cid} is missing an agent"
    assert catalog["cases"]["A1"]["agent"] == "project-assistant"
    assert catalog["cases"]["B1"]["agent"] == "quantity-surveyor"
    assert catalog["cases"]["E4"]["agent"] == "construction-pm"
