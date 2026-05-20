"""Dynamic reasoner system prompt — Reasoning Engine Plan 5.

Pure string building. The prompt is rebuilt every turn so it reflects what the
session already knows — a follow-up question then builds on prior state.
"""

from app.schemas.project_session import ProjectSession

_PHASES = """\
You are the Project Reasoner for a construction-project intelligence platform.
Work in four phases:
  UNDERSTAND — restate what the user is really asking for.
  PLAN       — produce an ordered list of steps that answers it.
  EXECUTE    — (the platform runs your plan; you do not run it yourself).
  DELIVER    — write a clear answer from the executed results.

When asked to PLAN, reply with ONLY a JSON object:
  {"understanding": "...", "steps": [{"type": "...", "args": {...},
   "output_key": "...", "description": "..."}]}

AVAILABLE STEP TYPES:
  - compute_cpm          run the Critical Path Method over loaded activities.
  - resource_histogram   time-phased manpower; args: {"period_unit": "week"|"month"}.
  - gantt                Gantt bars for the loaded schedule.
  - compress             shorten the schedule; args: {"reductions": {"<id>": <days>}}.
  - generate_code        novel/custom logic; args: {"task": "...",
                         "variables": {...}, "from_session": ["<key>", ...]}.
"""


def _state_summary(session: ProjectSession) -> str:
    d = session.data
    lines = []
    activities = d.get("activities")
    if activities:
        lines.append(f"- activities: {len(activities)} loaded.")
    else:
        lines.append("- activities: NOT loaded (no schedule yet).")
    for key, label in (
        ("cpm_results", "CPM already computed"),
        ("manpower", "resource histogram already computed"),
        ("gantt", "Gantt data already computed"),
        ("compressed", "a compressed schedule already exists"),
    ):
        if key in d:
            lines.append(f"- {label} (session key '{key}') — reuse it, "
                         f"do not recompute unless the user changed inputs.")
    if session.artifacts:
        lines.append(f"- {len(session.artifacts)} artifact(s) already produced.")
    return "\n".join(lines)


def build_reasoner_prompt(session: ProjectSession, request: str) -> str:
    """Assemble the reasoner system prompt for the current turn."""
    return (
        f"{_PHASES}\n\n"
        f"CURRENT SESSION STATE:\n{_state_summary(session)}\n\n"
        f"USER REQUEST:\n{request}"
    )
