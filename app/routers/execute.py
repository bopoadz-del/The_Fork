from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.blocks import BLOCK_REGISTRY
from app.dependencies import require_api_key
from app.dependencies import block_instances, _create_block_instance
from app.core.input_adapter import adapt_input

router = APIRouter()


class ExecuteRequest(BaseModel):
    block: str = Field(..., description="Block name (chat, pdf, ocr, voice, etc.)")
    input: Optional[Any] = Field(default=None, description="Input data for the block")
    params: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Block parameters")


@router.post("/execute")
async def execute(request: ExecuteRequest, auth: dict = Depends(require_api_key)):
    """Execute a single block."""
    block_name = request.block

    if block_name not in BLOCK_REGISTRY:
        raise HTTPException(404, f"Block '{block_name}' not found. Available: {list(BLOCK_REGISTRY.keys())}")

    # Skip containers - they belong to Block Store
    if block_name.startswith("container_"):
        raise HTTPException(400, f"Container '{block_name}' cannot be executed directly. Use Block Store.")

    try:
        if block_name not in block_instances:
            block_instances[block_name] = _create_block_instance(BLOCK_REGISTRY[block_name])

        block = block_instances[block_name]
        
        # Adapt input to what block expects
        adapted_input = adapt_input(request.input, block)
        
        result = await block.execute(adapted_input, request.params or {})

        # Attach artifacts for the side panel (Roadmap V2 · Epic 4).
        try:
            from app.core.artifacts import result_to_artifacts
            if isinstance(result, dict) and "artifacts" not in result:
                inner = result.get("result", result)
                result["artifacts"] = result_to_artifacts(
                    inner if isinstance(inner, dict) else {}
                )
        except Exception:
            pass

        return result

    except Exception as e:
        raise HTTPException(500, f"Execution failed: {str(e)}")


@router.post("/v1/execute")
async def execute_v1(request: ExecuteRequest, auth: dict = Depends(require_api_key)):
    """Execute a single block (v1 API)."""
    return await execute(request)
