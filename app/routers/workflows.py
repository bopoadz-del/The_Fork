"""Saved workflow API — Roadmap V2 · Epic 7 (power-user chaining).

Save a chain as a named workflow, list/inspect/delete it, and re-run it.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import workflows as store
from app.dependencies import require_api_key
from app.routers.chain import ChainRequest, ChainStep, chain_execute

router = APIRouter()


class SaveWorkflowRequest(BaseModel):
    name: str
    steps: List[Dict[str, Any]]
    project_id: Optional[str] = None


class RunWorkflowRequest(BaseModel):
    initial_input: Optional[Any] = None


@router.post("/v1/workflows", status_code=201)
async def save_workflow(
    req: SaveWorkflowRequest, auth: dict = Depends(require_api_key)
):
    """Save a chain of block steps as a named, re-runnable workflow."""
    if not req.name.strip():
        raise HTTPException(400, "Workflow 'name' is required")
    if not req.steps:
        raise HTTPException(400, "A workflow needs at least one step")
    for s in req.steps:
        if "block" not in s:
            raise HTTPException(400, "Every step needs a 'block'")
    return store.save_workflow(req.name.strip(), req.steps, req.project_id)


@router.get("/v1/workflows")
async def list_workflows(
    project_id: Optional[str] = None, auth: dict = Depends(require_api_key)
):
    """List saved workflows (optionally filtered to one project)."""
    return {"workflows": store.list_workflows(project_id)}


@router.get("/v1/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, auth: dict = Depends(require_api_key)):
    w = store.get_workflow(workflow_id)
    if not w:
        raise HTTPException(404, f"Workflow '{workflow_id}' not found")
    return w


@router.delete("/v1/workflows/{workflow_id}")
async def delete_workflow(
    workflow_id: str, auth: dict = Depends(require_api_key)
):
    if not store.delete_workflow(workflow_id):
        raise HTTPException(404, f"Workflow '{workflow_id}' not found")
    return {"status": "deleted", "id": workflow_id}


@router.post("/v1/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    req: RunWorkflowRequest,
    auth: dict = Depends(require_api_key),
):
    """Re-run a saved workflow through the chain orchestrator."""
    w = store.get_workflow(workflow_id)
    if not w:
        raise HTTPException(404, f"Workflow '{workflow_id}' not found")
    try:
        chain_req = ChainRequest(
            steps=[ChainStep(**s) for s in w["steps"]],
            initial_input=req.initial_input,
        )
    except Exception as e:
        raise HTTPException(400, f"Workflow steps are invalid: {e}")
    result = await chain_execute(chain_req, auth)
    return {"workflow_id": workflow_id, "name": w["name"], "run": result}
