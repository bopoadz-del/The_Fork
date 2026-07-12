"""Predefined reasoning — the orchestrator block's own PLAN -> EXECUTE ->
DELIVER path for KNOWN workflows (absorbed from project_reasoner).

For a known workflow the step list is a fixed template, so no LLM planning call
is needed — deterministic, auditable, no step hallucination. The orchestrator
block calls `run_workflow`; unknown actions return handled=False so the block
falls through to a dynamic agent.

DELIVER is intent-gated: a request to PRODUCE/EXPORT materializes a document
(render_artifact is in the plan); a question answers inline from the staged
summary (no artifact). This is the fix to "produce a document to answer a
question" — the plan composition, not "a tool ran", decides.
"""
from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.execution_plan import ExecutionPlan, PlanStep
from app.schemas.project_session import ProjectSession
from app.core.plan_executor import PlanExecutor

logger = logging.getLogger(__name__)


def container_action_names() -> set:
    """The set of construction-container actions eligible for generic
    reasoner-driven dispatch. Empty when the construction kit isn't loaded."""
    try:
        from app.dependencies import get_block_instance
        con = get_block_instance("construction")
        return set(con.get_actions().keys()) if hasattr(con, "get_actions") else set()
    except Exception:
        return set()

# Verbs that mean "I want the artifact", not "answer my question".
_DELIVERABLE_RE = re.compile(
    r"\b(produce|export|generate|create|build|make|prepare|develop|draft|"
    r"give me|download|issue|render)\b", re.IGNORECASE)


def is_deliverable_request(message: str) -> bool:
    return bool(_DELIVERABLE_RE.search(message or ""))


def build_schedule_plan(context: Dict[str, Any]) -> ExecutionPlan:
    """Fixed compute template for the schedule workflow: extract (if docs) ->
    build_wbs -> cost_load. No render step here — materialization is an
    intent-gated export descriptor built in run_workflow (endpoints are the
    render mechanism / API calls)."""
    p = context.get("params") or {}
    doc_ids = context.get("document_ids") or []
    steps: List[PlanStep] = []
    if doc_ids:
        steps.append(PlanStep(type="extract_document", args={"document_ids": doc_ids},
                              description="extract equipment lead times + milestones"))
    steps.append(PlanStep(type="build_wbs", args={
        "brief": context.get("message", ""),
        "target_count": p.get("target_count", 200),
        "project_type": p.get("project_type"),
        "start_date": p.get("start_date"),
    }, description="build the WBS (+ long-lead procurement)"))
    steps.append(PlanStep(type="cost_load", args={
        "project_name": context.get("project_name") or "Project",
        "currency": p.get("currency", "SAR"),
        "day_rate": p.get("day_rate"),
        "crew_per_trade": p.get("crew_per_trade", 4),
        "start_date": p.get("start_date"),
    }, description="cost-load the schedule (man-days S-curve, milestones)"))
    return ExecutionPlan(understanding="produce/answer a construction schedule", steps=steps)


def deliver_schedule(session: ProjectSession, deliverable: bool) -> str:
    """Deterministic DELIVER: a written answer from the staged summary. (LLM
    polish is optional and can wrap this later — the numbers are fixed.)"""
    s = session.data.get("schedule_summary") or {}
    if not s:
        return "I could not build the schedule for this request."
    parts = [
        f"Schedule built: {s.get('activities')} activities over "
        f"{s.get('duration_days')} working days "
        f"({s.get('critical_count')} on the critical path).",
    ]
    if s.get("procurement_injected"):
        parts.append(
            f"{s['procurement_injected']} long-lead procurement item(s) were "
            "injected from the source documents' real lead times, each linked "
            "ahead of its install.")
    if s.get("total_man_days"):
        parts.append(f"Total effort is about {s['total_man_days']:,} man-days.")
    ms = s.get("target_milestones") or []
    if ms:
        named = ", ".join(f"{m.get('name')} ({m.get('target_date')})" for m in ms[:5] if m.get('name'))
        if named:
            parts.append(f"Target milestones from the source: {named}.")
    if deliverable:
        parts.append(
            "The cost-loaded workbook (CPM, cumulative man-days S-curve, "
            "manpower histogram, milestones) is ready to download.")
    return " ".join(parts)


def _export_descriptor(context: Dict[str, Any]) -> Dict[str, Any] | None:
    """Intent-gated materialization: the export endpoint (the render API call)
    the deliverable should be produced from, on click. None when no project."""
    project_id = context.get("project_id")
    if not project_id:
        return None
    p = context.get("params") or {}
    doc_ids = context.get("document_ids") or []
    if doc_ids:
        endpoint = f"/v1/projects/{project_id}/export/schedule-from-document"
        payload: Dict[str, Any] = {"document_ids": doc_ids}
    else:
        endpoint = f"/v1/projects/{project_id}/export/schedule-from-brief"
        payload = {"brief": context.get("message", "")}
    payload.update({"target_count": p.get("target_count", 200)})
    for k in ("project_type", "start_date", "day_rate"):
        if p.get(k) is not None:
            payload[k] = p[k]
    return {"label": "Schedule (Excel)", "format": "xlsx", "method": "POST",
            "endpoint": endpoint, "payload": payload}


# action (from smart_orchestrator) -> plan builder
WORKFLOW_REGISTRY = {
    "generate_wbs": build_schedule_plan,
}


def _render_container_result(action: str, result: Any) -> str:
    """Turn a container action's return value into a human-readable answer.
    Prefers an explicit answer/summary/markdown/report field; otherwise renders
    the salient fields so the deliverable is substantial rather than a stub."""
    import json as _json
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result)
    for k in ("answer", "markdown", "report", "summary", "text", "narrative"):
        v = result.get(k)
        if isinstance(v, str) and v.strip():
            return v
    # Build a readable rendering from the structured payload.
    title = action.replace("_", " ").title()
    lines = [f"# {title}", ""]
    for k, v in result.items():
        if k in ("status", "action", "ok"):
            continue
        if isinstance(v, (str, int, float, bool)):
            lines.append(f"- **{k.replace('_',' ')}:** {v}")
        elif isinstance(v, list) and v:
            lines.append(f"\n## {k.replace('_',' ').title()} ({len(v)})")
            for item in v[:40]:
                if isinstance(item, dict):
                    lines.append("- " + "; ".join(f"{ik}: {iv}" for ik, iv in list(item.items())[:6]))
                else:
                    lines.append(f"- {item}")
        elif isinstance(v, dict) and v:
            lines.append(f"\n## {k.replace('_',' ').title()}")
            for ik, iv in list(v.items())[:30]:
                lines.append(f"- **{ik}:** {iv}")
    rendered = "\n".join(lines).strip()
    return rendered or _json.dumps(result, indent=2, default=str)


async def _run_container_action(action: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Generic reasoner-driven dispatch: if `action` is a real construction
    container action, run it via the container's own execute() and format the
    result. Returns None when the action isn't a container action (so the caller
    keeps its handled=False fallthrough). No per-action wiring — the reasoner
    picks the action, the container implements it, this runs it."""
    try:
        from app.dependencies import get_block_instance
        con = get_block_instance("construction")
    except Exception:
        return None
    try:
        actions = set(con.get_actions().keys()) if hasattr(con, "get_actions") else set()
    except Exception:
        actions = set()
    if action not in actions:
        return None
    envelope: Dict[str, Any] = {
        "action": action,
        "project_id": context.get("project_id"),
        "message": context.get("message"),
        "document_ids": context.get("document_ids") or [],
    }
    envelope.update({k: v for k, v in (context.get("params") or {}).items() if v is not None})
    try:
        result = await con.execute(envelope)
    except Exception as e:  # noqa: BLE001
        logger.exception("generic container action %s failed", action)
        return {"handled": True, "status": "error",
                "answer": f"The {action.replace('_',' ')} step could not complete: {e}",
                "deliverable": context.get("deliverable"), "exports": [], "plan_steps": [action]}
    answer = _render_container_result(action, result)
    exports = result.get("exports", []) if isinstance(result, dict) else []
    return {
        "handled": True,
        "status": (result.get("status") if isinstance(result, dict) else None) or "success",
        "answer": answer,
        "deliverable": context.get("deliverable"),
        "export": exports[0] if exports else None,
        "exports": exports,
        "plan_steps": [action],
    }


async def run_workflow(action: str, context: Dict[str, Any],
                       session: ProjectSession) -> Dict[str, Any]:
    """Run the predefined workflow for `action`. Returns handled=False for
    unknown actions so the orchestrator falls through to a dynamic agent."""
    builder = WORKFLOW_REGISTRY.get(action)
    if builder is None:
        # No bespoke plan builder — try the generic container dispatch. This is
        # what makes every container-implemented action a first-class,
        # reasoner-driven deliverable instead of falling to a short chat answer.
        generic = await _run_container_action(action, context)
        if generic is not None:
            return generic
        return {"handled": False}
    # Prefer the dynamic UNDERSTAND verdict (context["deliverable"]) when the
    # orchestrator supplied it; fall back to the keyword heuristic otherwise.
    deliverable = (context["deliverable"] if "deliverable" in context
                   else is_deliverable_request(context.get("message", "")))
    plan = builder(context)
    run = await PlanExecutor().run(plan, session)
    answer = deliver_schedule(session, deliverable)
    export = _export_descriptor(context) if deliverable else None
    return {
        "handled": True,
        "status": run.status,
        "answer": answer,
        "deliverable": deliverable,
        "export": export,
        "exports": [export] if export else [],
        "plan_steps": [st.type for st in plan.steps],
    }
