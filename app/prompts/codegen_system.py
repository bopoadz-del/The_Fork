"""Code-generation system prompt — Reasoning Engine Plan 4.

Pure string building. No AI, no I/O — unit-testable without an API key.
"""

from typing import Any, Dict, Optional

_CONTRACT = """\
You are a Python code generator for a construction-project intelligence \
platform. Write a SHORT Python snippet that solves the described task.

HARD RULES:
- Assign the final answer to a variable named `result`.
- Use ONLY the input variables listed below — they are already in scope.
- You MAY import from the standard library `math` module and from \
`app.lib.pm_computations` (tested project-management functions, e.g. \
`compute_cpm`, `resource_histogram`, `gantt_data`, `compress_schedule`).
- Do NOT read files, make network calls, or import anything else.
- Return ONLY a single fenced ```python code block — no prose.
"""


def build_codegen_prompt(
    task: str,
    variables: Dict[str, Any],
    *,
    prior_code: Optional[str] = None,
    prior_error: Optional[str] = None,
) -> str:
    """Assemble the system prompt for one code-generation attempt.

    `prior_code` / `prior_error` are supplied on a retry so the LLM can see
    what failed and why; they are omitted on the first attempt.
    """
    if variables:
        var_lines = "\n".join(
            f"  - {k} = {v!r}" for k, v in variables.items()
        )
        var_block = f"INPUT VARIABLES (already in scope):\n{var_lines}"
    else:
        var_block = "INPUT VARIABLES: none."

    parts = [_CONTRACT, f"TASK:\n{task}", var_block]

    if prior_code is not None and prior_error is not None:
        parts.append(
            "YOUR PREVIOUS ATTEMPT FAILED. Fix it.\n"
            f"Previous code:\n```python\n{prior_code}\n```\n"
            f"Error:\n{prior_error}"
        )

    return "\n\n".join(parts)
