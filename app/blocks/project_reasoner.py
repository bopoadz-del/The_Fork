"""Project Reasoner — Reasoning Engine Plan 5.

The LLM agent. One turn: UNDERSTAND + PLAN (one LLM call returning plan JSON)
-> EXECUTE (PlanExecutor runs the steps) -> DELIVER (one LLM call writing the
answer from the executed results).
"""

import json
import os
from typing import Any, Dict

import httpx

from app.core.universal_base import UniversalBlock
from app.core.plan_executor import PlanExecutor
from app.prompts.reasoner_system import build_reasoner_prompt
from app.schemas.execution_plan import ExecutionPlan
from app.schemas.project_session import ProjectSession


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of an LLM reply (it may add prose or fences).
    Tries the whole string first, then decodes the first object starting at
    the first '{' — trailing prose (even prose containing a '}') is ignored.
    Raises ValueError when there is no parsable object."""
    if not isinstance(text, str):
        raise ValueError("no JSON object in LLM reply")
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in LLM reply")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        raise ValueError("no JSON object in LLM reply")
    return obj


# Per-step bound for the DELIVER prompt: every step's result is included, but
# no single step may exceed this, so nothing is silently dropped.
_DELIVER_STEP_LIMIT = 2500


def _render_step_results(run) -> str:
    """Format this turn's step results (step + computed output) for the
    DELIVER prompt. Each output is bounded individually."""
    lines = []
    for i, sr in enumerate(run.step_results, 1):
        if sr.status == "success":
            rendered = json.dumps(sr.output, default=str)
            if len(rendered) > _DELIVER_STEP_LIMIT:
                rendered = rendered[:_DELIVER_STEP_LIMIT] + "… (truncated)"
            lines.append(
                f"{i}. {sr.type} (-> {sr.output_key or 'default'}): "
                f"OK\n   output: {rendered}"
            )
        else:
            lines.append(
                f"{i}. {sr.type} (-> {sr.output_key or 'default'}): "
                f"ERROR — {sr.error}"
            )
    return "\n".join(lines) if lines else "(no steps executed)"


class ProjectReasonerBlock(UniversalBlock):
    name = "project_reasoner"
    version = "1.0.0"
    description = (
        "Reasoning agent: UNDERSTAND -> PLAN -> EXECUTE -> DELIVER over a "
        "project session."
    )
    layer = 3
    tags = ["domain", "construction", "reasoning", "agent", "llm"]
    requires = []

    default_config = {"model": "deepseek-chat"}

    ui_schema = {
        "input": {
            "type": "text",
            "placeholder": "Ask anything about your project...",
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "answer", "type": "markdown", "label": "Answer"},
                {"name": "understanding", "type": "text", "label": "Understood as"},
            ],
        },
        "quick_actions": [
            {"icon": "🧭", "label": "Critical path", "prompt": "What is the critical path?"},
            {"icon": "⏱️", "label": "Compress", "prompt": "How can I finish 2 weeks sooner?"},
        ],
    }

    async def _call_llm(self, prompt: str) -> str:
        """Active-LLM-provider call. Overridden by test doubles.

        Routes via app.agents.runtime._llm_config — auto-uses Groq when
        GROQ_API_KEY is set, otherwise DeepSeek.
        """
        from app.agents.runtime import _llm_config  # local import: avoid cycle at module load
        cfg = _llm_config()
        api_key = os.getenv(cfg["env_key"])
        if not api_key:
            raise RuntimeError(f"{cfg['env_key']} not configured")
        model = self.config.get("model", cfg["default_model"])
        if cfg["provider"] != "deepseek" and isinstance(model, str) and model.startswith("deepseek-"):
            model = cfg["default_model"]
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                cfg["url"],
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.2},
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"{cfg['provider']} API error (HTTP {resp.status_code})"
                )
            return resp.json()["choices"][0]["message"]["content"]

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}
        request = data.get("request") or data.get("text") \
            or params.get("request") \
            or (str(input_data) if not isinstance(input_data, dict) else "")
        session: ProjectSession = data.get("session") or params.get("session")
        project_id = data.get("project_id") or params.get("project_id")

        request = request or ""
        if not request.strip():
            return {"status": "error", "error": "No request provided"}
        if session is None:
            return {"status": "error", "error": "No session provided"}

        session.add_message("user", request)

        # Look up top-k relevant snippets from this project's indexed
        # documents so the planner can ground its steps in the actual
        # uploaded files. Silent no-op when no project_id is given or the
        # project has nothing indexed yet — the reasoner falls back to
        # session-state-only planning, matching the old behavior.
        excerpts: list = []
        if project_id:
            try:
                from app.core.doc_index import search_project_documents
                excerpts = await search_project_documents(
                    project_id, request, top_k=5
                )
            except Exception:
                excerpts = []

        # ── UNDERSTAND + PLAN ────────────────────────────────────────────
        try:
            plan_reply = await self._call_llm(
                build_reasoner_prompt(session, request, excerpts)
            )
            plan = ExecutionPlan.model_validate(_extract_json(plan_reply))
        except Exception as e:                              # noqa: BLE001
            return {"status": "error",
                    "error": f"Could not build a plan: {e}"}

        # ── EXECUTE ──────────────────────────────────────────────────────
        run = await PlanExecutor().run(plan, session)

        # ── DELIVER ──────────────────────────────────────────────────────
        # Build the prompt from THIS turn's step results, not the whole
        # accumulated session blob — so the answer sees exactly what was just
        # computed and no step's output is silently truncated away.
        deliver_prompt = (
            f"You planned and executed steps for this request:\n{request}\n\n"
            f"UNDERSTANDING: {plan.understanding}\n"
            f"EXECUTION STATUS: {run.status}\n"
            f"STEP RESULTS (from this turn):\n"
            f"{_render_step_results(run)}\n\n"
            f"Write a clear, concise answer for the user from these results. "
            f"If the status is error or partial, explain what is missing."
        )
        try:
            answer = await self._call_llm(deliver_prompt)
        except Exception as e:                              # noqa: BLE001
            answer = f"(Could not generate the written answer: {e})"

        session.add_message("assistant", answer)

        status = run.status
        return {
            "status": status,
            "answer": answer,
            "understanding": plan.understanding,
            "plan": plan.model_dump(mode="json"),
            "execution": run.model_dump(mode="json"),
        }
