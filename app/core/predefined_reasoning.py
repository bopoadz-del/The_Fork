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


# Scoped Dispatch v2 — the EXPLICIT allowlist of container actions eligible for
# reasoner-driven predefined dispatch. These are deliverable-PRODUCING actions
# (they generate a register / schedule / certificate / report from project data)
# where deterministic container execution + LLM synthesis beats the agent tool
# loop's short answer. Q&A / lookup / document-processing / RAG-grounded actions
# (chat, process_document, process_contract, process_specification_full,
# benchmark_lookup, recommend, learn, analyze_spec, spec_analyze, health_check,
# status) are DELIBERATELY excluded: they must stay on the RAG-grounded agent
# path, never be intercepted. Membership is explicit, not a heuristic — adding an
# action here is a reviewed decision.
SCOPED_DISPATCH_ALLOWLIST = frozenset({
    "cash_flow_forecast",
    "resource_histogram",
    "risk_register_auto_populate",
    "procurement_list_generator",
    "procurement_optimizer",
    "commissioning_checklist",
    "rfi_generator",
    "submittal_log_generator",
    "payment_certificate",
    "change_order_impact",
    "value_engineering",
    "claims_builder",
    "variation_order_manager",
    "warranty_maintenance_schedule",
    "as_built_deviation_report",
    "om_manual_generator",
    "esg_sustainability_report",
    "carbon_footprint_calculator",
    "safety_compliance_audit",
    "tender_bid_analysis",
    "daily_site_report",
    "forensic_delay_analysis",
})


def container_action_names() -> set:
    """Construction-container actions eligible for reasoner-driven predefined
    dispatch: the explicit SCOPED_DISPATCH_ALLOWLIST intersected with the actions
    the loaded container actually implements. Empty when the kit isn't loaded.
    Never returns Q&A/lookup actions even if they exist on the container."""
    try:
        from app.dependencies import get_block_instance
        con = get_block_instance("construction")
        impl = set(con.get_actions().keys()) if hasattr(con, "get_actions") else set()
        return impl & SCOPED_DISPATCH_ALLOWLIST
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


# ── Grounding gate (Phase-2 2a) ─────────────────────────────────────────────
# Post-synthesis, FLAG-only checks. The synthesis prompt already TELLS the LLM to
# ground every figure in the tool result, but nothing ENFORCES it. These checks
# add honest enforcement WITHOUT ever blocking or regenerating a deliverable — a
# heuristic must not suppress a real answer. Flag-gated so it can be turned off
# instantly in prod. Soundness: this is correct because predefined-dispatch
# synthesis receives ONLY the container `result`; if RAG chunks are ever added to
# the synthesis input, the backing/source sets below MUST expand to cover them.
import os as _os

def _grounding_gate_enabled() -> bool:
    return _os.getenv("GROUNDING_GATE", "1") not in ("0", "false", "False", "")

# A confidence/validation STAMP asserted in prose ("confidence: high",
# "validation passed", "quality-assured"). Deliberately NARROW — bare
# "verify"/"validated" appears in legitimate checklist content, so only
# stamp-shaped phrases are matched to keep false-positives near zero.
_CONF_RATING = r"(?:very\s+high|high|moderate|medium|low|strong|\d{1,3}\s*%|0?\.\d\d?)"
_CONFIDENCE_STAMP_RE = re.compile(
    # rating on either side of the word: "confidence: high" OR "95% confidence".
    rf"\bconfidence\b[\s:*_()-]{{0,4}}{_CONF_RATING}"
    rf"|{_CONF_RATING}[\s-]{{0,4}}\bconfidence\b",
    re.IGNORECASE,
)
_VALIDATION_STAMP_RE = re.compile(
    r"\b(validation\s+(passed|complete|completed|successful|status)|"
    r"independently\s+validated|cross[- ]validated|quality[- ]assured|"
    r"verified\s+against\s+(the\s+)?(tool|engine|calculation|model))\b",
    re.IGNORECASE,
)
# Result keys that legitimately BACK a confidence/validation stamp. When any of
# these is present in the structured result, a stamp in the prose is honest and
# is left untouched (e.g. drawing_qto / historical_benchmark carry real
# confidence / source_note; validation_pipeline results carry pass/checks).
_STAMP_BACKING_KEYS = frozenset({
    "confidence", "confidence_score", "confidence_level", "confidence_interval",
    "validation", "validated", "verified", "validation_report",
    "validation_status", "source_note", "checks", "pass", "passed",
    "quality_check", "qa", "overall_pass", "stage_results",
})

def _result_has_backing_field(result: Any) -> bool:
    """True iff the structured result carries any confidence/validation-style
    field that would legitimately back a stamp in the prose. Walks the whole
    result (dicts + lists), case-insensitive on keys."""
    stack = [result]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if str(k).lower() in _STAMP_BACKING_KEYS:
                    return True
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return False

def _gate_confidence_stamps(text: str, result: Any) -> str:
    """FLAG (append a caveat) when the prose asserts a validation/confidence
    stamp that the computed result does NOT back with a corresponding field.
    Never edits a stamp the result actually backs; never blocks the answer."""
    if not (_CONFIDENCE_STAMP_RE.search(text) or _VALIDATION_STAMP_RE.search(text)):
        return text
    if _result_has_backing_field(result):
        return text
    return text + (
        "\n\n> _Note: this response mentions validation/confidence, but the "
        "computed result carries no validation or confidence field — treat any "
        "such rating as unverified._"
    )

def _apply_grounding_gate(text: str, result: Any) -> str:
    """Run the enabled post-synthesis grounding checks (FLAG-only)."""
    if not _grounding_gate_enabled():
        return text
    try:
        text = _gate_confidence_stamps(text, result)
    except Exception:  # noqa: BLE001 — a gate must never break a deliverable
        logger.exception("grounding gate failed; returning ungated answer")
    return text


async def _synthesize_answer(action: str, result: Any, message: Optional[str]) -> str:
    """LLM synthesis of a container action's structured result into a grounded,
    professional narrative. Scoped Dispatch v2 BANS raw-render as the delivered
    answer — the deterministic render is kept only as an emergency fallback if
    the LLM call fails, so we never emit nothing. The LLM is instructed to ground
    every figure strictly in the tool result (no invention)."""
    raw = _render_container_result(action, result)
    try:
        import json as _json
        from app.core import llm_client
        payload = (_json.dumps(result, default=str)[:6000]
                   if isinstance(result, (dict, list)) else str(result)[:6000])
        system = (
            "You are a construction project assistant. A deterministic tool has "
            "already run and produced the structured result below. Write a clear, "
            "professional answer for a construction PM that presents that result. "
            "Ground EVERY figure, name and date strictly in the tool result - do "
            "not invent, estimate, or add facts not present. Be concise, use "
            "markdown. If the result is empty or an error, say so plainly."
        )
        user = (f"User request: {message or action}\n\nTool: {action}\n"
                f"Structured result (JSON):\n{payload}")
        text = await llm_client.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0, max_tokens=900,
        )
        return _apply_grounding_gate(text.strip() or raw, result)
    except Exception:
        logger.exception("synthesis for %s failed; using deterministic render", action)
        return _apply_grounding_gate(raw, result)


async def _run_container_action(action: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Scoped reasoner-driven dispatch: if `action` is in the explicit
    SCOPED_DISPATCH_ALLOWLIST (deliverable-producing) AND implemented by the
    container, run it via the container's own execute() and LLM-synthesize the
    result. Returns None otherwise (so the caller keeps its handled=False
    fallthrough to the RAG-grounded agent path). Q&A / lookup / document actions
    are never in the allowlist, so they are never intercepted here."""
    if action not in SCOPED_DISPATCH_ALLOWLIST:
        return None
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
    answer = await _synthesize_answer(action, result, context.get("message"))
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
