"""Tests for the Project Reasoner — Reasoning Engine Plan 5."""

import pytest

from app.schemas.execution_plan import ExecutionPlan, PlanStep
from tests.conftest import requires_construction_kit


def test_plan_step_defaults():
    s = PlanStep(type="compute_cpm")
    assert s.args == {} and s.output_key == ""


def test_execution_plan_construct():
    plan = ExecutionPlan(
        understanding="user wants the critical path",
        steps=[PlanStep(type="compute_cpm")],
    )
    assert len(plan.steps) == 1
    assert plan.understanding


def test_execution_plan_parses_from_llm_json():
    # The reasoner's PLAN call returns JSON — it must validate straight in.
    raw = {
        "understanding": "compute then compress",
        "steps": [
            {"type": "compute_cpm"},
            {"type": "compress", "args": {"reductions": {"B": 3}}},
        ],
    }
    plan = ExecutionPlan.model_validate(raw)
    assert plan.steps[1].type == "compress"
    assert plan.steps[1].args["reductions"] == {"B": 3}


def test_empty_step_type_rejected():
    with pytest.raises(Exception):
        PlanStep(type="")


from app.core.plan_executor import PlanExecutor
from app.core.session_store import InMemorySessionStore


_ACTIVITIES = [
    {"id": "A", "duration": 3, "predecessors": []},
    {"id": "B", "duration": 5, "predecessors": [{"predecessor_id": "A"}]},
    {"id": "C", "duration": 2, "predecessors": [{"predecessor_id": "B"}]},
]


def _session_with_activities():
    s = InMemorySessionStore().get_or_create("s1")
    s.data["activities"] = _ACTIVITIES
    return s


@pytest.mark.asyncio
async def test_executor_runs_compute_cpm_step():
    session = _session_with_activities()
    plan = ExecutionPlan(steps=[PlanStep(type="compute_cpm")])
    result = await PlanExecutor().run(plan, session)
    assert result.status == "success"
    assert session.data["cpm_results"]["project_duration"] == 10


@pytest.mark.asyncio
async def test_executor_runs_compress_step():
    session = _session_with_activities()
    plan = ExecutionPlan(steps=[
        PlanStep(type="compute_cpm"),
        PlanStep(type="compress", args={"reductions": {"B": 3}}),
    ])
    result = await PlanExecutor().run(plan, session)
    assert result.status == "success"
    assert session.data["compressed"]["revised"]["project_duration"] == 7
    assert session.data["compressed"]["days_saved"] == 3


@pytest.mark.asyncio
async def test_executor_runs_gantt_and_histogram_steps():
    session = _session_with_activities()
    plan = ExecutionPlan(steps=[
        PlanStep(type="compute_cpm"),
        PlanStep(type="gantt"),
        PlanStep(type="resource_histogram", args={"period_unit": "week"}),
    ])
    result = await PlanExecutor().run(plan, session)
    assert result.status == "success"
    assert len(session.data["gantt"]) == 3
    assert session.data["manpower"]["period_unit"] == "week"


@pytest.mark.asyncio
async def test_executor_reports_unknown_step_type():
    session = _session_with_activities()
    plan = ExecutionPlan(steps=[PlanStep(type="teleport")])
    result = await PlanExecutor().run(plan, session)
    assert result.status == "error"
    assert "teleport" in result.step_results[0].error


@pytest.mark.asyncio
async def test_executor_compute_cpm_without_activities_errors():
    session = InMemorySessionStore().get_or_create("s1")  # no activities
    plan = ExecutionPlan(steps=[PlanStep(type="compute_cpm")])
    result = await PlanExecutor().run(plan, session)
    assert result.status == "error"


from app.blocks.formula_executor_v2 import FormulaExecutorV2Block


class _MockCodeGen(FormulaExecutorV2Block):
    """Code-gen double — returns canned code, no DeepSeek call."""
    async def _call_llm(self, prompt):
        return "result = a + b"


@pytest.mark.asyncio
async def test_executor_runs_generate_code_step():
    session = InMemorySessionStore().get_or_create("s1")
    plan = ExecutionPlan(steps=[PlanStep(
        type="generate_code",
        args={"task": "add a and b", "variables": {"a": 4, "b": 6}},
        output_key="sum",
    )])
    executor = PlanExecutor(code_block=_MockCodeGen([]))
    result = await executor.run(plan, session)
    assert result.status == "success"
    assert session.data["sum"]["result"] == 10


@pytest.mark.asyncio
async def test_generate_code_step_requires_a_task():
    session = InMemorySessionStore().get_or_create("s1")
    plan = ExecutionPlan(steps=[PlanStep(type="generate_code", args={})])
    result = await PlanExecutor(code_block=_MockCodeGen([])).run(plan, session)
    assert result.status == "error"


from app.prompts.reasoner_system import build_reasoner_prompt


def test_reasoner_prompt_lists_the_phases():
    p = build_reasoner_prompt(InMemorySessionStore().get_or_create("s"), "hi")
    for phase in ("UNDERSTAND", "PLAN", "EXECUTE", "DELIVER"):
        assert phase in p


def test_reasoner_prompt_advertises_step_types():
    p = build_reasoner_prompt(InMemorySessionStore().get_or_create("s"), "hi")
    for t in ("compute_cpm", "resource_histogram", "gantt", "compress",
              "generate_code"):
        assert t in p


def test_reasoner_prompt_reflects_empty_session():
    p = build_reasoner_prompt(InMemorySessionStore().get_or_create("s"), "hi")
    assert "no activities" in p.lower() or "not loaded" in p.lower()


def test_reasoner_prompt_reflects_loaded_state():
    s = _session_with_activities()
    s.data["cpm_results"] = {"project_duration": 10}
    p = build_reasoner_prompt(s, "now compress B")
    # the prompt must tell the LLM CPM is already done, so it skips re-running
    assert "cpm" in p.lower()
    assert "now compress B" in p


import json

from app.blocks.project_reasoner import ProjectReasonerBlock
from app.blocks import BLOCK_REGISTRY, get_block


class _MockReasoner(ProjectReasonerBlock):
    """Reasoner double — scripts the PLAN call's JSON and the DELIVER call's
    answer. `_call_llm` is invoked twice per turn: first PLAN, then DELIVER."""

    def __init__(self, plan_json, answer, **kw):
        super().__init__(**kw)
        self._plan_json = plan_json
        self._answer = answer
        self.calls = 0

    async def _call_llm(self, prompt: str) -> str:
        self.calls += 1
        return self._plan_json if self.calls == 1 else self._answer


@pytest.mark.asyncio
async def test_reasoner_runs_full_loop():
    session = _session_with_activities()
    plan_json = json.dumps({
        "understanding": "user wants the critical path",
        "steps": [{"type": "compute_cpm"}],
    })
    block = _MockReasoner(plan_json, "The critical path is A-B-C, 10 days.")
    out = await block.process({"request": "what is the critical path?",
                               "session": session})
    assert out["status"] == "success"
    assert out["answer"] == "The critical path is A-B-C, 10 days."
    assert out["understanding"] == "user wants the critical path"
    assert session.data["cpm_results"]["project_duration"] == 10
    assert block.calls == 2          # one PLAN call, one DELIVER call


@pytest.mark.asyncio
async def test_reasoner_records_turn_in_history():
    session = _session_with_activities()
    plan_json = json.dumps({"understanding": "x",
                            "steps": [{"type": "compute_cpm"}]})
    block = _MockReasoner(plan_json, "done")
    await block.process({"request": "go", "session": session})
    roles = [m.role for m in session.history]
    assert roles == ["user", "assistant"]


@pytest.mark.asyncio
async def test_reasoner_handles_bad_plan_json():
    session = _session_with_activities()
    block = _MockReasoner("not json at all", "unused")
    out = await block.process({"request": "go", "session": session})
    assert out["status"] == "error"
    assert "plan" in out["error"].lower()


@pytest.mark.asyncio
async def test_reasoner_reports_step_failure():
    session = InMemorySessionStore().get_or_create("s1")   # no activities
    plan_json = json.dumps({"understanding": "x",
                            "steps": [{"type": "compute_cpm"}]})
    block = _MockReasoner(plan_json, "unused")
    out = await block.process({"request": "go", "session": session})
    assert out["status"] in ("error", "partial")


@requires_construction_kit
def test_reasoner_is_registered():
    assert "project_reasoner" in BLOCK_REGISTRY
    assert get_block("project_reasoner") is ProjectReasonerBlock


# ── code-review fixes ────────────────────────────────────────────────────


class _CapturingReasoner(_MockReasoner):
    """Like _MockReasoner but records the DELIVER prompt for inspection."""

    async def _call_llm(self, prompt: str) -> str:
        if self.calls == 1:                  # the DELIVER call is the 2nd
            self.deliver_prompt = prompt
        return await super()._call_llm(prompt)


@pytest.mark.asyncio
async def test_deliver_prompt_contains_this_turn_step_output():
    # The DELIVER prompt must be built from this turn's StepResult.output,
    # not from a blunt slice of the whole session blob.
    session = _session_with_activities()
    plan_json = json.dumps({"understanding": "critical path",
                            "steps": [{"type": "compute_cpm"}]})
    block = _CapturingReasoner(plan_json, "answer")
    await block.process({"request": "critical path?", "session": session})
    prompt = block.deliver_prompt
    assert "STEP RESULTS (from this turn)" in prompt
    assert "compute_cpm" in prompt
    # the actual computed value (project_duration 10) must be in the prompt
    assert "project_duration" in prompt
    assert "10" in prompt
    # and the old whole-session dump phrasing must be gone
    assert "session data" not in prompt.lower()


@pytest.mark.asyncio
async def test_from_session_skips_non_allowlisted_key():
    # generate_code's `from_session` is LLM-controlled — a key outside the
    # allowlist must be silently skipped, not injected into the sandbox.
    session = InMemorySessionStore().get_or_create("s1")
    session.data["cpm_results"] = {"project_duration": 10}   # allowlisted
    session.data["secret"] = "leak-me"                       # NOT allowlisted

    captured = {}

    class _SpyCodeGen(_MockCodeGen):
        async def process(self, input_data, params=None):
            captured["variables"] = dict(input_data.get("variables") or {})
            return await super().process(input_data, params)

    plan = ExecutionPlan(steps=[PlanStep(
        type="generate_code",
        args={"task": "x", "variables": {"a": 1, "b": 2},
              "from_session": ["cpm_results", "secret"]},
    )])
    await PlanExecutor(code_block=_SpyCodeGen([])).run(plan, session)
    assert "cpm_results" in captured["variables"]
    assert "secret" not in captured["variables"]


def test_extract_json_survives_trailing_prose_with_brace():
    from app.blocks.project_reasoner import _extract_json
    reply = ('{"understanding": "x", "steps": []}\n'
             "Note: this plan uses the {placeholder} convention. Thanks!")
    parsed = _extract_json(reply)
    assert parsed["understanding"] == "x"
    assert parsed["steps"] == []


def test_extract_json_parses_clean_json_directly():
    from app.blocks.project_reasoner import _extract_json
    parsed = _extract_json('{"understanding": "y", "steps": []}')
    assert parsed["understanding"] == "y"


@pytest.mark.asyncio
async def test_reasoner_handles_none_request():
    # request=None must yield the error dict, not an AttributeError.
    session = _session_with_activities()
    block = _MockReasoner("unused", "unused")
    out = await block.process({"request": None, "session": session})
    assert out["status"] == "error"
    assert "request" in out["error"].lower()


@pytest.mark.asyncio
async def test_step_result_carries_output():
    session = _session_with_activities()
    plan = ExecutionPlan(steps=[PlanStep(type="compute_cpm")])
    result = await PlanExecutor().run(plan, session)
    assert result.step_results[0].output is not None
    assert result.step_results[0].output["project_duration"] == 10
