"""Dynamic UNDERSTAND — the orchestrator's LLM-driven intent read.

Replaces the brittle keyword classifier for the workflow decision: the LLM reads
the message and returns which known workflow it is (if any) and whether the user
wants a DELIVERABLE or an ANSWER. This is the "dynamic reasoning over predefined
routes" the operator asked for — the model understands "how long is procurement"
is a QUESTION, which keyword routing (procurement_list_generator) cannot.

Runs on whatever LLM is configured (Ollama Cloud in prod). Bounded output: a
known workflow name or "none", so a bad/hallucinated read simply falls through
to the dynamic agent — never invents a step.
"""
from __future__ import annotations

from typing import Any, Dict

from app.core.llm_client import complete_json

# Semantic workflow -> smart_orchestrator action (WORKFLOW_REGISTRY key).
WORKFLOW_TO_ACTION = {
    "schedule": "generate_wbs",
}

_SYSTEM = (
    "You are the intent router for a construction project platform. Read the "
    "user's message and return ONLY a JSON object, no prose:\n"
    '{"workflow": "<schedule|none>", "mode": "<produce|question>", "params": {}}\n'
    "- workflow \"schedule\": the user wants a construction schedule / WBS / "
    "programme BUILT, or asks a question ABOUT one.\n"
    "- workflow \"none\": anything else.\n"
    "- mode \"produce\": wants a deliverable created/exported (produce, generate, "
    "create, build, export, make, prepare, draft a schedule).\n"
    "- mode \"question\": is ASKING about a schedule (how long, what, when, which, "
    "status, explain) — NOT requesting a file.\n"
    "- params (optional): target_count (int), project_type (string), start_date "
    "(YYYY-MM-DD) only if the user states them.\n"
    "Examples:\n"
    '"produce a 300 activity data center schedule" -> '
    '{"workflow":"schedule","mode":"produce","params":{"target_count":300,"project_type":"data_center"}}\n'
    '"how long is procurement on the schedule?" -> '
    '{"workflow":"schedule","mode":"question","params":{}}\n'
    '"what is the tallest building" -> {"workflow":"none","mode":"question","params":{}}'
)


async def understand_intent(message: str, has_documents: bool = False) -> Dict[str, Any]:
    """Return {action, mode, params, workflow} for a message, or workflow
    'none' (action None) when it is not a known workflow. Never raises."""
    empty = {"workflow": "none", "action": None, "mode": "question", "params": {}}
    if not message or not message.strip():
        return empty
    user = message.strip()
    if has_documents:
        user += "\n\n(Note: the user has attached document(s) to this project.)"
    try:
        out = await complete_json(_SYSTEM, user, max_tokens=300)
    except Exception:
        return empty
    workflow = str(out.get("workflow") or "none").lower()
    action = WORKFLOW_TO_ACTION.get(workflow)
    if not action:
        return empty
    mode = str(out.get("mode") or "question").lower()
    params = out.get("params") if isinstance(out.get("params"), dict) else {}
    return {
        "workflow": workflow,
        "action": action,
        "mode": mode,
        "deliverable": mode == "produce",
        "params": params,
    }
