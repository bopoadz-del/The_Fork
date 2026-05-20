"""Formula Executor Block — DEPRECATED.

Reasoning Engine Plan 4: this block is superseded by FormulaExecutorV2Block
(`app/blocks/formula_executor_v2.py`), which uses real LLM code generation
instead of pattern matching. This wrapper stays registered under the original
`formula_executor` key so existing chains do not break; it forwards every call
to a v2 instance. New code should target `formula_executor_v2` directly.

Output shape changed in v2. The old block returned a dict keyed
``execution_result``; v2 returns ``result``. This wrapper does NOT reproduce
the old shape — it returns v2's dict as-is, plus a single back-compat alias:
on a successful v2 result it also sets ``execution_result`` to the ``result``
value. No other legacy key is aliased; callers depending on the old shape
should migrate to ``formula_executor_v2``.

The old block also served a formula-library *listing* for
``operation == "list"`` (or a listing-style description). That feature was
removed in v2 — those inputs are detected here and answered with a clear
"deprecated" dict instead of being forwarded to the LLM as a task.
"""

import warnings
from typing import Any, Dict

from app.core.universal_base import UniversalBlock
from app.blocks.formula_executor_v2 import FormulaExecutorV2Block

# Description strings that the old block treated as a library-listing request.
_LIST_DESCRIPTIONS = {"list", "library", "show formulas"}


class FormulaExecutorBlock(UniversalBlock):
    name = "formula_executor"
    version = "2.0.0"          # bumped — now backed by v2
    description = "DEPRECATED — delegates to formula_executor_v2 (LLM code-gen)."
    layer = 3
    tags = ["domain", "construction", "formula", "deprecated"]
    requires = []

    default_config = FormulaExecutorV2Block.default_config
    ui_schema = FormulaExecutorV2Block.ui_schema

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block=hal_block, config=config)
        self._v2 = FormulaExecutorV2Block(hal_block=hal_block, config=config)

    @staticmethod
    def _is_list_request(input_data: Any) -> bool:
        """True when the input is the old formula-library *listing* request:
        ``operation == "list"`` or a listing-style description string."""
        if not isinstance(input_data, dict):
            return False
        if str(input_data.get("operation", "")).strip().lower() == "list":
            return True
        for field in ("formula_description", "task", "description", "text", "input"):
            value = input_data.get(field)
            if isinstance(value, str) and value.strip().lower() in _LIST_DESCRIPTIONS:
                return True
        return False

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        warnings.warn(
            "FormulaExecutorBlock is deprecated; use formula_executor_v2.",
            DeprecationWarning, stacklevel=2,
        )

        # The formula-library listing was removed in v2; do NOT forward such
        # inputs as a task — the LLM would turn "list" into nonsense.
        if self._is_list_request(input_data):
            return {
                "status": "deprecated",
                "error": (
                    "The formula library listing was removed in v2; "
                    "describe the calculation you want instead."
                ),
            }

        result = await self._v2.process(input_data, params)

        # Back-compat alias: the old block returned `execution_result`; v2
        # returns `result`. Keep every v2 key, add the legacy alias on success.
        if isinstance(result, dict) and result.get("status") == "success":
            result = {**result, "execution_result": result.get("result")}
        return result
