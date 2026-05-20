"""Plan executor — Reasoning Engine Plan 5.

Runs an ExecutionPlan's steps against a ProjectSession. No AI: each step type
maps to a handler that calls a tested app/lib function (Plans 1/1b) or the
Plan-4 code generator, then writes the result into session.data.
"""

from app.schemas.cpm import CPMInput
from app.schemas.execution_plan import (
    ExecutionPlan, PlanRunResult, PlanStep, StepResult,
)
from app.schemas.project_session import ProjectSession
from app.lib.pm_computations import (
    compute_cpm, resource_histogram, gantt_data, compress_schedule,
)


class PlanExecutionError(Exception):
    """A step could not run (bad args, missing state)."""


def _require_cpm_input(session: ProjectSession) -> CPMInput:
    activities = session.data.get("activities")
    if not activities:
        raise PlanExecutionError(
            "No activities in the session — load a schedule first."
        )
    return CPMInput.model_validate({"activities": activities})


class PlanExecutor:
    """Dispatches ExecutionPlan steps. Stateless — safe to reuse."""

    def __init__(self, code_block=None):
        """`code_block` is a FormulaExecutorV2Block (Plan 4). Constructed
        lazily on first use when not injected, so library-only plans need no
        LLM block."""
        self._code_block = code_block

    def _get_code_block(self):
        if self._code_block is None:
            from app.blocks.formula_executor_v2 import FormulaExecutorV2Block
            self._code_block = FormulaExecutorV2Block()
        return self._code_block

    async def run(
        self, plan: ExecutionPlan, session: ProjectSession
    ) -> PlanRunResult:
        results = []
        any_error = False
        for step in plan.steps:
            try:
                await self._run_step(step, session)
                results.append(StepResult(
                    type=step.type,
                    output_key=step.output_key,
                    status="success",
                ))
            except Exception as e:                       # noqa: BLE001
                any_error = True
                results.append(StepResult(
                    type=step.type,
                    output_key=step.output_key,
                    status="error",
                    error=str(e),
                ))
        if not any_error:
            status = "success"
        elif any(r.status == "success" for r in results):
            status = "partial"
        else:
            status = "error"
        return PlanRunResult(status=status, step_results=results)

    async def _run_step(self, step: PlanStep, session: ProjectSession) -> None:
        handler = getattr(self, f"_step_{step.type}", None)
        if handler is None:
            raise PlanExecutionError(f"Unknown step type: '{step.type}'")
        await handler(step, session)

    # ── library step handlers ────────────────────────────────────────────
    async def _step_compute_cpm(self, step, session):
        out = compute_cpm(_require_cpm_input(session))
        session.data[step.output_key or "cpm_results"] = out.model_dump(mode="json")

    async def _step_resource_histogram(self, step, session):
        cpm_input = _require_cpm_input(session)
        out = compute_cpm(cpm_input)
        hist = resource_histogram(
            out.results, cpm_input.activities,
            period_unit=step.args.get("period_unit", "week"),
        )
        session.data[step.output_key or "manpower"] = hist.model_dump(mode="json")

    async def _step_gantt(self, step, session):
        out = compute_cpm(_require_cpm_input(session))
        bars = gantt_data(out.results)
        session.data[step.output_key or "gantt"] = [
            b.model_dump(mode="json") for b in bars
        ]

    async def _step_compress(self, step, session):
        reductions = step.args.get("reductions")
        if not isinstance(reductions, dict):
            raise PlanExecutionError(
                "compress step needs an args.reductions dict"
            )
        reductions = {k: int(v) for k, v in reductions.items()}
        revised, days_saved = compress_schedule(
            _require_cpm_input(session), reductions
        )
        session.data[step.output_key or "compressed"] = {
            "revised": revised.model_dump(mode="json"),
            "days_saved": days_saved,
        }

    # ── code-generation step handler (delegates to Plan 4) ───────────────
    async def _step_generate_code(self, step, session):
        task = step.args.get("task")
        if not task:
            raise PlanExecutionError("generate_code step needs an args.task")
        variables = dict(step.args.get("variables") or {})
        # let generated code read prior session state when asked
        for key in step.args.get("from_session", []):
            if key in session.data:
                variables[key] = session.data[key]
        out = await self._get_code_block().process({
            "task": task, "variables": variables, "session": session,
        })
        if out.get("status") != "success":
            raise PlanExecutionError(
                out.get("error", "code generation failed")
            )
        session.data[step.output_key or "generated"] = out
