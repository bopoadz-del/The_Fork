"""LIVE DeepSeek end-to-end test — Reasoning Engine Plan 4.

Skipped until DEEPSEEK_API_KEY is funded. This is the acceptance check for
real LLM code generation. The mock-LLM coverage is in
tests/test_formula_executor_v2.py and runs always.
"""

import os

import pytest

from app.blocks.formula_executor_v2 import FormulaExecutorV2Block

pytestmark = pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not configured — pending refill",
)


@pytest.mark.asyncio
async def test_live_codegen_simple_arithmetic():
    block = FormulaExecutorV2Block()
    out = await block.process({
        "task": "concrete volume of a slab: length x width x thickness",
        "variables": {"length_m": 10, "width_m": 8, "thickness_m": 0.2},
    })
    assert out["status"] == "success"
    assert abs(out["result"] - 16.0) < 1e-6


@pytest.mark.asyncio
async def test_live_codegen_uses_pm_library():
    block = FormulaExecutorV2Block()
    out = await block.process({
        "task": (
            "Given activities A(3 days) -> B(5 days) -> C(2 days) in series, "
            "use app.lib.pm_computations.compute_cpm to get the project "
            "duration. Set result to the integer duration."
        ),
        "variables": {},
    })
    assert out["status"] == "success"
    assert out["result"] == 10
