"""Formula Executor v2 — LLM code generation + sandboxed execution.

Reasoning Engine Plan 4. Supersedes the pattern-matching FormulaExecutorBlock.

Flow: build prompt -> LLM writes Python -> run in the Plan-3 sandbox ->
cache a success on the session -> retry with the error message on a failure.

Sandbox contract note: Plan 3's ``run_sandboxed`` has NO ``timeout_seconds``
parameter and no internal timeout. To keep the configurable ``timeout_seconds``
behaviour the plan asks for, each sandbox call is run in a daemon worker thread
joined with a timeout (see ``_run_sandboxed_with_timeout``). A genuinely
runaway snippet leaves the daemon thread orphaned but cannot block the process;
RestrictedPython already bars I/O and dangerous builtins.
"""

import os
import re
import threading
from typing import Any, Dict, Optional

import httpx

from app.core.universal_base import UniversalBlock
from app.core.sandbox import run_sandboxed, SandboxResult

from app.prompts.codegen_system import build_codegen_prompt

_FENCE_RE = re.compile(r"```(?:python)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Pull the code out of a ```python ...``` block; return text as-is if
    the LLM did not fence it."""
    m = _FENCE_RE.search(text or "")
    return (m.group(1) if m else (text or "")).strip()


def _cache_key(task: str, variables: Dict[str, Any]) -> str:
    """Stable key for a code-gen request: the task plus the sorted variable
    NAMES (not values — cached code is re-run with fresh values)."""
    names = ",".join(sorted(variables))
    return f"{task.strip().lower()}|{names}"


def _run_sandboxed_with_timeout(
    code: str, variables: Dict[str, Any], timeout_seconds: int
) -> SandboxResult:
    """Run ``run_sandboxed`` in a daemon thread joined with ``timeout_seconds``.

    The Plan-3 sandbox has no native timeout, so this wrapper supplies one.
    On timeout a synthetic failed :class:`SandboxResult` is returned and the
    worker thread is left to finish on its own (it cannot touch the host).
    """
    box: Dict[str, SandboxResult] = {}

    def _worker() -> None:
        box["result"] = run_sandboxed(code, state=variables)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        return SandboxResult(
            success=False,
            error=f"Execution timed out after {timeout_seconds}s",
            error_type="TimeoutError",
        )
    return box.get(
        "result",
        SandboxResult(
            success=False,
            error="Sandbox produced no result",
            error_type="RuntimeError",
        ),
    )


class FormulaExecutorV2Block(UniversalBlock):
    name = "formula_executor_v2"
    version = "2.0.0"
    description = (
        "LLM code-generation: describe a task, an LLM writes Python, it runs "
        "sandboxed, results are cached, failures retried."
    )
    layer = 3
    tags = ["domain", "construction", "codegen", "llm", "sandbox", "reasoning"]
    requires = []

    default_config = {
        "max_retries": 2,        # extra attempts after the first
        "timeout_seconds": 10,
        "model": "deepseek-chat",
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"task": "concrete volume for a 10x8m slab 0.2m thick", "variables": {"length_m": 10, "width_m": 8, "thickness_m": 0.2}}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "generated_code", "type": "code", "label": "Generated Code"},
                {"name": "result", "type": "text", "label": "Result"},
                {"name": "attempts", "type": "number", "label": "Attempts"},
            ],
        },
        "quick_actions": [
            {"icon": "🧮", "label": "Calculate", "prompt": "Calculate concrete volume for a 10x8m slab, 200mm thick"},
        ],
    }

    async def _call_llm(self, prompt: str) -> str:
        """Send the code-gen prompt to DeepSeek and return the raw reply.

        Overridden by test doubles. Raises RuntimeError when no key is set so
        callers get a clear, non-secret error.
        """
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured")
        model = self.config.get("model", "deepseek-chat")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,   # deterministic code generation
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"DeepSeek API error (HTTP {resp.status_code})"
                )
            return resp.json()["choices"][0]["message"]["content"]

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}
        task = data.get("task") or data.get("formula_description") \
            or params.get("task") or (str(input_data) if not isinstance(input_data, dict) else "")
        variables = dict(data.get("variables") or data.get("input_values") or {})

        if not task.strip():
            return {"status": "error", "error": "No task description provided"}

        timeout_seconds = int(self.config.get("timeout_seconds", 10))
        session = data.get("session") or params.get("session")
        key = _cache_key(task, variables)

        # --- cache hit: re-run the previously generated code, skip the LLM --
        if session is not None and key in session.code_cache:
            cached_code = session.code_cache[key]
            sandbox = _run_sandboxed_with_timeout(
                cached_code, variables, timeout_seconds,
            )
            if sandbox.success:
                return {
                    "status": "success",
                    "generated_code": cached_code,
                    "result": sandbox.result,
                    "stdout": sandbox.stdout,
                    "attempts": 0,
                    "cache_hit": True,
                    "task": task,
                }
            # stale cache (e.g. variable set changed) — fall through to regen

        # --- generate -> run -> retry loop ---------------------------------
        max_attempts = 1 + int(self.config.get("max_retries", 2))
        prior_code: Optional[str] = None
        prior_error: Optional[str] = None
        last_code = ""
        last_error_type: Optional[str] = None
        last_error = "Code generation failed"

        for attempt in range(1, max_attempts + 1):
            prompt = build_codegen_prompt(
                task, variables,
                prior_code=prior_code, prior_error=prior_error,
            )
            try:
                raw = await self._call_llm(prompt)
            except Exception as e:
                return {"status": "error",
                        "error": f"Code generation failed: {e}",
                        "attempts": attempt, "task": task}

            code = _strip_fences(raw)
            last_code = code
            sandbox = _run_sandboxed_with_timeout(
                code, variables, timeout_seconds,
            )
            if sandbox.success:
                if session is not None:
                    session.code_cache[key] = code
                return {
                    "status": "success",
                    "generated_code": code,
                    "result": sandbox.result,
                    "stdout": sandbox.stdout,
                    "attempts": attempt,
                    "cache_hit": False,
                    "task": task,
                }
            # failed — carry context into the next attempt
            last_error = sandbox.error or "Sandbox execution failed"
            last_error_type = sandbox.error_type
            prior_code = code
            prior_error = sandbox.error or last_error

        return {
            "status": "error",
            "error": last_error,
            "generated_code": last_code,
            "traceback": last_error,
            "error_type": last_error_type,
            "attempts": max_attempts,
            "task": task,
        }
