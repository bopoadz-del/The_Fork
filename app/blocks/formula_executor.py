"""Formula Executor Block — DEPRECATED.

Reasoning Engine Plan 4: this block is superseded by FormulaExecutorV2Block
(`app/blocks/formula_executor_v2.py`), which uses real LLM code generation
instead of pattern matching. This wrapper stays registered under the original
`formula_executor` key so existing chains do not break; it forwards every call
to a v2 instance. New code should target `formula_executor_v2` directly.
"""

import warnings
from typing import Any, Dict

from app.core.universal_base import UniversalBlock
from app.blocks.formula_executor_v2 import FormulaExecutorV2Block


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

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        warnings.warn(
            "FormulaExecutorBlock is deprecated; use formula_executor_v2.",
            DeprecationWarning, stacklevel=2,
        )
        return await self._v2.process(input_data, params)
