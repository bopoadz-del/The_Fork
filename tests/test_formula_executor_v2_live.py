"""LIVE end-to-end test of LLM code generation (Reasoning Engine Plan 4).

Runs ``formula_executor_v2`` on the DEPLOYED build through POST /v1/execute:
the deployment's real LLM writes the Python, the deployment's sandbox runs it.
That is what the old in-process version checked with a local KIMI/GROQ key,
which CI never has; the deployed build does.

Gated by tests/_live_api.py: CI's production-like job runs it with the
FORK_API_KEY repository secret (skips without a key; fails under
LIVE_API_REQUIRED=1). The mocked-LLM contract lives in test_formula_executor_v2.py.
"""
from __future__ import annotations

import pytest

from tests._live_api import call, require_live_api


@pytest.fixture(autouse=True)
def _live():
    require_live_api()


def _run(task: str, variables: dict) -> dict:
    status, body = call(
        "POST", "/v1/execute",
        {"block": "formula_executor_v2",
         "input": {"task": task, "variables": variables}},
        timeout=240,
    )
    assert status == 200, (status, body)
    assert body["status"] == "success", body
    out = body["result"]
    assert out["status"] == "success", out
    return out


def test_live_codegen_simple_arithmetic():
    out = _run("concrete volume of a slab: length x width x thickness",
               {"length_m": 10, "width_m": 8, "thickness_m": 0.2})
    assert abs(out["result"] - 16.0) < 1e-6, out


def test_live_codegen_uses_pm_library():
    out = _run(
        "Given activities A(3 days) -> B(5 days) -> C(2 days) in series, "
        "use app.lib.pm_computations.compute_cpm to get the project "
        "duration. Set result to the integer duration.",
        {},
    )
    assert out["result"] == 10, out
