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
    """Pull the first {...} object out of an LLM reply (it may add prose or
    fences). Raises ValueError when there is no parsable object."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in LLM reply")
    return json.loads(text[start:end + 1])


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
        """DeepSeek call. Overridden by test doubles."""
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured")
        model = self.config.get("model", "deepseek-chat")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.2},
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"DeepSeek API error (HTTP {resp.status_code})"
                )
            return resp.json()["choices"][0]["message"]["content"]

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}
        request = data.get("request") or data.get("text") \
            or params.get("request") \
            or (str(input_data) if not isinstance(input_data, dict) else "")
        session: ProjectSession = data.get("session") or params.get("session")

        if not request.strip():
            return {"status": "error", "error": "No request provided"}
        if session is None:
            return {"status": "error", "error": "No session provided"}

        session.add_message("user", request)

        # ── UNDERSTAND + PLAN ────────────────────────────────────────────
        try:
            plan_reply = await self._call_llm(
                build_reasoner_prompt(session, request)
            )
            plan = ExecutionPlan.model_validate(_extract_json(plan_reply))
        except Exception as e:                              # noqa: BLE001
            return {"status": "error",
                    "error": f"Could not build a plan: {e}"}

        # ── EXECUTE ──────────────────────────────────────────────────────
        run = await PlanExecutor().run(plan, session)

        # ── DELIVER ──────────────────────────────────────────────────────
        deliver_prompt = (
            f"You planned and executed steps for this request:\n{request}\n\n"
            f"UNDERSTANDING: {plan.understanding}\n"
            f"EXECUTION STATUS: {run.status}\n"
            f"RESULTS (session data):\n"
            f"{json.dumps(session.data, default=str)[:6000]}\n\n"
            f"Write a clear, concise answer for the user from these results. "
            f"If the status is error or partial, explain what is missing."
        )
        try:
            answer = await self._call_llm(deliver_prompt)
        except Exception as e:                              # noqa: BLE001
            answer = f"(Could not generate the written answer: {e})"

        session.add_message("assistant", answer)

        status = "success" if run.status == "success" else run.status
        return {
            "status": status,
            "answer": answer,
            "understanding": plan.understanding,
            "plan": plan.model_dump(mode="json"),
            "execution": run.model_dump(mode="json"),
        }
