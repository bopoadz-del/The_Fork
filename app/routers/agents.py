"""HTTP routes for runtime agents (the AI assistants users chat with).

Routes:
    GET  /v1/agents                       — list all agents
    GET  /v1/agents/{name}                — describe one agent (system prompt + tools)
    POST /v1/agents/{name}/chat           — single-turn JSON response (with tool calls)
    POST /v1/agents/{name}/chat/stream    — SSE: start / tool_call / tool_result / token / end / error

Auth: same Bearer cb_dev_key (or any registered key) as the rest of /v1.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents import AGENT_REGISTRY, get_agent
from app.dependencies import require_api_key

router = APIRouter()


class AgentChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = None
    model: Optional[str] = None  # override agent default if needed


@router.get("/v1/agents")
async def list_agents(auth: dict = Depends(require_api_key)):
    return {
        "count": len(AGENT_REGISTRY),
        "agents": [
            {
                "name": a.name,
                "description": a.description,
                "icon": a.icon,
                "model": a.model,
                "tools": a.allowed_blocks,
                "tool_count": len(a.allowed_blocks),
            }
            for a in AGENT_REGISTRY.values()
        ],
    }


@router.get("/v1/agents/{name}")
async def get_agent_info(name: str, auth: dict = Depends(require_api_key)):
    agent = get_agent(name)
    if not agent:
        raise HTTPException(404, f"Agent '{name}' not found")
    return {
        "name": agent.name,
        "description": agent.description,
        "icon": agent.icon,
        "model": agent.model,
        "temperature": agent.temperature,
        "max_tokens": agent.max_tokens,
        "tools": agent.allowed_blocks,
        "system_prompt": agent.system_prompt,
    }


@router.post("/v1/agents/{name}/chat")
async def agent_chat(name: str, req: AgentChatRequest, auth: dict = Depends(require_api_key)):
    agent = get_agent(name)
    if not agent:
        raise HTTPException(404, f"Agent '{name}' not found")
    if req.model:
        agent = _agent_with_override(agent, model=req.model)
    result = await agent.chat(req.message, history=req.history)
    return result


@router.post("/v1/agents/{name}/chat/stream")
async def agent_chat_stream(name: str, request: Request, auth: dict = Depends(require_api_key)):
    agent = get_agent(name)
    if not agent:
        raise HTTPException(404, f"Agent '{name}' not found")
    try:
        body = await request.json()
    except Exception:
        body = {}
    message = body.get("message", "")
    history = body.get("history") or []
    model = body.get("model")
    if model:
        agent = _agent_with_override(agent, model=model)

    async def event_stream():
        try:
            async for evt in agent.chat_stream(message, history=history):
                yield f"data: {json.dumps(evt, default=str)}\n\n"
                await asyncio.sleep(0)  # yield to the event loop
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _agent_with_override(base_agent, **overrides):
    """Return a shallow copy of the agent with the given fields overridden."""
    from dataclasses import replace
    return replace(base_agent, **overrides)
