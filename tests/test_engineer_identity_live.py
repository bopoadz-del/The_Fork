"""Live regression for the Engineer-identity answer.

Defect (2026-09-14, deepseek-flash, build 2b48857): "Who is the Engineer?"
answered "The Engineer is 90 days of the effective date of a Letter of Award…"
4-5/5 — the appointment-timing clause in the identity slot.

Owner ruling, 2026-09-19 (app/core/party_names.py): "No names at all from this
RAG. No employer, project name, no consultant, no contractor, no engineer."
So the right answer points to WHERE the Engineer is named (the Contract Data)
and withholds the firm. The test asserts exactly that: the identity slot holds
the Contract Data pointer, never the appointment-timing clause, and never the
firm's name (JACOBS / CH2M, the firm this corpus names).

This test hits the LIVE deployed build (tests/_live_api.py). CI runs it in
the production-like job's "Live-deploy tests" step with the FORK_API_KEY
repository secret; without a key it skips (fails under LIVE_API_REQUIRED=1).
Run it after deploy:

    FORK_API_KEY=... python -m pytest tests/test_engineer_identity_live.py -q -s

It requires 5/5.
"""
from __future__ import annotations

import pytest

from tests._live_api import require_live_api, stream_chat


@pytest.fixture(autouse=True)
def _live():
    require_live_api()


def _ask(message: str) -> str:
    return stream_chat(message, project_id="master_corpus", timeout=120)


def test_who_is_the_engineer_points_to_the_contract_data_without_the_firm_5x():
    """5/5 required: Contract Data pointer, no firm name, no timing clause."""
    runs = []
    for _ in range(5):
        a = _ask("Who is the Engineer under this contract?")
        low = a.lower()
        points = "contract data" in low
        leaked = "jacobs" in low or "ch2m" in low
        junk = "letter of award" in low or "90 days" in low
        runs.append((points and not leaked and not junk, a[:120]))

    # Count only the passing runs (a `sum(1 for ok, _ in runs)` form counts
    # every tuple and can never fail -- it shipped once in #599).
    passes = sum(1 for ok, _ in runs if ok)
    detail = "\n".join(f"  {'PASS' if ok else 'FAIL'}: {snip}" for ok, snip in runs)
    assert passes == 5, f"Engineer identity {passes}/5 (need 5/5):\n{detail}"
