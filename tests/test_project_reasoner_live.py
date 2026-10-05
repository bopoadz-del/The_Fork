"""LIVE end-to-end test of the Project Reasoner (Reasoning Engine Plan 5).

Runs against the DEPLOYED build through POST /v1/project/ask -- the route that
drives ``ProjectReasonerBlock`` with the deployment's real LLM provider and
persists the session between turns. That is the same block, the same real
model and the same session round trip the old in-process version exercised
with a local KIMI/GROQ key, which CI never has; the deployed build does.

Gated by tests/_live_api.py: CI's production-like job runs it with the
FORK_API_KEY repository secret (skips without a key; fails under
LIVE_API_REQUIRED=1). The mocked-LLM contract lives in test_project_reasoner.py.
"""
from __future__ import annotations

import uuid

import pytest

from tests._live_api import call, require_live_api

ACTIVITIES = [
    {"id": "A", "duration": 3, "predecessors": []},
    {"id": "B", "duration": 5, "predecessors": [{"predecessor_id": "A"}]},
    {"id": "C", "duration": 2, "predecessors": [{"predecessor_id": "B"}]},
]


@pytest.fixture(autouse=True)
def _live():
    require_live_api()


def _ask(session_id: str, request: str, activities=None) -> dict:
    payload = {"session_id": session_id, "request": request}
    if activities is not None:
        payload["activities"] = activities
    status, body = call("POST", "/v1/project/ask", payload, timeout=240)
    assert status == 200, (status, body)
    return body


def _session() -> str:
    return f"ci-live-reasoner-{uuid.uuid4().hex[:12]}"


def test_live_reasoner_answers_critical_path_question():
    out = _ask(_session(), "What is the project duration and the critical path?",
               activities=ACTIVITIES)
    assert out["status"] == "success", out
    assert "10" in out["answer"], out["answer"]


def test_live_reasoner_follow_up_uses_prior_state():
    sid = _session()
    _ask(sid, "Compute the critical path.", activities=ACTIVITIES)
    # No activities on the follow-up: the answer must come from the session
    # the deployed store persisted after the first turn.
    out = _ask(sid, "Now shorten B by 3 days — what is the new duration?")
    assert out["status"] == "success", out
    assert "7" in out["answer"], out["answer"]
