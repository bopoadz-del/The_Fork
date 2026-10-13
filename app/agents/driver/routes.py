"""The route catalogue as the model's tool (F-DRIVER Addendum 2: "routes
are the model's tools"). Every predefined workflow -- the plan builders and
the construction container's scoped actions -- is one ``run_workflow`` call
away; the model chooses which, code does not. Workflows run exactly as the
chat router runs them (same tenant gate, same file-parameter resolution)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

RUN_WORKFLOW = "run_workflow"


def catalogue() -> List[str]:
    from app.core.predefined_reasoning import WORKFLOW_REGISTRY, container_action_names

    return sorted(set(WORKFLOW_REGISTRY) | container_action_names())


def _summary(name: str) -> str:
    """What a workflow produces, in its own words: the first sentence of its
    docstring (a container action's or a plan builder's)."""
    from app.core.predefined_reasoning import WORKFLOW_REGISTRY

    fn = WORKFLOW_REGISTRY.get(name)
    doc = " ".join(((getattr(fn, "__doc__", "") or "") if fn else "").split())
    return doc.split(". ")[0].rstrip(".") if doc else ""


def schema(workflows: List[str]) -> Optional[Dict[str, Any]]:
    """The route catalogue as one tool, every workflow described by what it
    produces -- so the model can tell a deliverable from a calculation."""
    if not workflows:
        return None
    lines = [f"- {w}: {_summary(w)}." if _summary(w) else f"- {w}" for w in workflows]
    return {
        "type": "function",
        "function": {
            "name": RUN_WORKFLOW,
            "description": "Run one of the platform's predefined workflows. Use one only when the user "
                           "asks for the deliverable it produces. A figure, duration or programme that a "
                           "calculator computes from the user's own inputs goes to that calculator, "
                           "not to a workflow. It returns the workflow's answer and any export offers.\n"
                           + "\n".join(lines),
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow": {"type": "string", "enum": workflows},
                    "deliverable": {"type": "boolean",
                                    "description": "True when the user asked for the artifact itself."},
                    "params": {"type": "object", "description": "Inputs the workflow takes, if known."},
                },
                "required": ["workflow"],
            },
        },
    }


async def run(args: Dict[str, Any], user_message: str, project_id: Optional[str],
              user_id: Optional[str], conversation_id: Optional[str]) -> Dict[str, Any]:
    from app.core import projects
    from app.core.predefined_reasoning import run_workflow
    from app.routers.chat import _resolve_predefined_file_params
    from app.schemas.project_session import ProjectSession

    action = str(args.get("workflow") or "")
    if action not in catalogue():
        return {"ok": False, "error": f"No workflow named {action!r}."}
    safe_project_id, project_name = project_id, None
    if project_id:
        from app.core.offload import off_loop

        proj = await off_loop(projects.get_project_accessible, project_id, user_id)
        safe_project_id = project_id if proj else None
        project_name = (proj or {}).get("name")
    params = {k: v for k, v in (args.get("params") or {}).items() if v is not None} \
        if isinstance(args.get("params"), dict) else {}
    context: Dict[str, Any] = {
        "message": user_message, "project_id": safe_project_id,
        "project_name": project_name or "Project", "document_ids": [],
        "params": params, "conversation_id": conversation_id,
    }
    if isinstance(args.get("deliverable"), bool):
        context["deliverable"] = args["deliverable"]
    resolved, ask = _resolve_predefined_file_params(action, safe_project_id, params, user_message)
    if ask:
        return {"ok": False, "needs_input": ask}
    context["params"] = resolved
    session = ProjectSession.new(f"driver-{user_id or 'anon'}-{conversation_id or 'turn'}",
                                 user_id=user_id or "system")
    out = await run_workflow(action, context, session)
    if not out.get("handled"):
        return {"ok": False, "error": f"The workflow {action!r} could not run."}
    return {"ok": True, "workflow": action, "answer": out.get("answer"),
            "exports": out.get("exports") or []}
