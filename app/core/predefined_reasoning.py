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
from typing import Any, Dict, List, Tuple

from app.schemas.execution_plan import ExecutionPlan, PlanStep
from app.schemas.project_session import ProjectSession
from app.core.plan_executor import PlanExecutor

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


async def run_workflow(action: str, context: Dict[str, Any],
                       session: ProjectSession) -> Dict[str, Any]:
    """Run the predefined workflow for `action`. Returns handled=False for
    unknown actions so the orchestrator falls through to a dynamic agent."""
    builder = WORKFLOW_REGISTRY.get(action)
    if builder is None:
        return {"handled": False}
    deliverable = is_deliverable_request(context.get("message", ""))
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
