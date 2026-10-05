"""Live regression for the Engineer-identity answer on deepseek.

Defect (2026-09-14, deepseek-flash, build 2b48857): "Who is the Engineer?"
answered "The Engineer is 90 days of the effective date of a Letter of Award…"
4-5/5 — the appointment-timing clause in the identity slot instead of the firm
(JACOBS / CH2M Saudi Limited). Fixed by rewording the inject hint to ENGINEER
IDENTITY (state the firm, not the date/period).

This test hits the LIVE deployed build (tests/_live_api.py). CI runs it in
the production-like job's "Live-deploy tests" step with the FORK_API_KEY
repository secret; without a key it skips (fails under LIVE_API_REQUIRED=1).
Run it after deploy:

    FORK_API_KEY=... python -m pytest tests/test_engineer_identity_live.py -q -s

It requires 5/5: the answer contains JACOBS and never the appointment-timing
tokens ("Letter of Award", "90 days").
"""
from __future__ import annotations

import pytest

from tests._live_api import require_live_api, stream_chat


@pytest.fixture(autouse=True)
def _live():
    require_live_api()


def _ask(message: str) -> str:
    return stream_chat(message, project_id="master_corpus", timeout=120)


def test_who_is_the_engineer_answers_jacobs_5x():
    """5/5 required: JACOBS present, never the appointment-timing clause."""
    runs = []
    for _ in range(5):
        a = _ask("Who is the Engineer under this contract?")
        low = a.lower()
        ok = ("jacobs" in low or "ch2m" in low)
        junk = ("letter of award" in low or "90 days" in low)
        runs.append((ok and not junk, a[:120]))

    # `sum(1 for ok, _ in runs)` counts every tuple regardless of ok — it is
    # always len(runs), so the assert below could never fail. That vacuous form
    # shipped in #599 and reported green while the live answer was garbled
    # 10/10. Count only the passing runs.
    passes = sum(1 for ok, _ in runs if ok)
    detail = "\n".join(f"  {'PASS' if ok else 'FAIL'}: {snip}" for ok, snip in runs)
    assert passes == 5, f"Engineer identity {passes}/5 (need 5/5):\n{detail}"
