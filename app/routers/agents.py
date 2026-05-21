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
from app.core import agent_memory
from app.core import projects as store
from app.dependencies import require_user

router = APIRouter()

_WS_PREFIX = "ws-"


def _enforce_conversation_access(conversation_id: str, auth: dict) -> None:
    """Authoritative ownership check for a conversation, on both read and write.

    Workspace conversations use the id ``ws-{projectId}`` — the project id is IN
    the id, so ownership is derived from the id itself, NOT from the (spoofable)
    stored ``project_id`` column on the conversation row.

    - ``ws-{pid}`` id  → caller must own project ``pid`` (else 404). Covers both
      "project doesn't exist" and "project belongs to someone else".
    - non-``ws-`` id   → not a project-scoped workspace conversation. If a stored
      conversation row exists with a ``project_id``, that ownership is checked;
      a stored row with NO ``project_id`` has no ownership binding → 404; a
      non-existent row is allowed (ad-hoc API conversation).

    Raises HTTPException(404) when access is denied.
    """
    if conversation_id.startswith(_WS_PREFIX):
        project_id = conversation_id[len(_WS_PREFIX):]
        if store.get_project(project_id, user_id=auth["user_id"]) is None:
            raise HTTPException(404, "Conversation not found")
        return

    # Non-workspace conversation id — fall back to the stored row's binding.
    conv = agent_memory.get_conversation(conversation_id)
    if conv is None:
        return  # ad-hoc API conversation, not yet created
    stored_pid = conv.get("project_id")
    if stored_pid is None:
        # No ownership binding at all — do not serve it.
        raise HTTPException(404, "Conversation not found")
    if store.get_project(stored_pid, user_id=auth["user_id"]) is None:
        raise HTTPException(404, "Conversation not found")


class AgentChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = None
    model: Optional[str] = None  # override agent default if needed
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None


@router.get("/v1/agents/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    auth: dict = Depends(require_user),
):
    """Return the stored messages for a conversation.

    Ownership is enforced FIRST via ``_enforce_conversation_access`` — for a
    ``ws-{pid}`` id this is derived from the id, not the stored row, so an
    attacker probing a victim's conversation gets 404 (never an empty 200 that
    would confirm the id format). A non-existent but owned conversation still
    returns 200 with an empty list.
    """
    _enforce_conversation_access(conversation_id, auth)

    conv = agent_memory.get_conversation(conversation_id)
    if conv is None:
        return {"conversation_id": conversation_id, "messages": []}

    msgs = agent_memory.get_messages(conversation_id)
    return {"conversation_id": conversation_id, "messages": msgs}


@router.get("/v1/agents")
async def list_agents(auth: dict = Depends(require_user)):
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
async def get_agent_info(name: str, auth: dict = Depends(require_user)):
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
async def agent_chat(name: str, req: AgentChatRequest, auth: dict = Depends(require_user)):
    agent = get_agent(name)
    if not agent:
        raise HTTPException(404, f"Agent '{name}' not found")

    # Authoritative ownership check: a ws-{pid} conversation_id binds to a
    # project; the caller must own it. This runs BEFORE the agent so an attacker
    # cannot even create/write a victim's ws-{pid} conversation.
    if req.conversation_id is not None:
        _enforce_conversation_access(req.conversation_id, auth)

    # Defense in depth: if a project_id is provided, the caller must own it.
    if req.project_id is not None:
        project = store.get_project(req.project_id, user_id=auth["user_id"])
        if project is None:
            raise HTTPException(404, "Project not found")

    if req.model:
        agent = _agent_with_override(agent, model=req.model)

    result = await agent.chat(
        req.message,
        history=req.history,
        project_id=req.project_id,
        conversation_id=req.conversation_id,
    )

    # Echo conversation_id back so the client can resume the conversation.
    if req.conversation_id is not None:
        result["conversation_id"] = req.conversation_id

    return result


@router.post("/v1/agents/{name}/chat/stream")
async def agent_chat_stream(name: str, request: Request, auth: dict = Depends(require_user)):
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
    project_id = body.get("project_id")
    conversation_id = body.get("conversation_id")

    # Authoritative ownership check: a ws-{pid} conversation_id binds to a
    # project; the caller must own it. Raised here (before StreamingResponse) so
    # an attacker cannot create/write a victim's ws-{pid} conversation.
    if conversation_id is not None:
        _enforce_conversation_access(conversation_id, auth)

    # Defense in depth: if a project_id is provided, the caller must own it.
    if project_id is not None:
        project = store.get_project(project_id, user_id=auth["user_id"])
        if project is None:
            raise HTTPException(404, "Project not found")

    if model:
        agent = _agent_with_override(agent, model=model)

    async def event_stream():
        try:
            async for evt in agent.chat_stream(
                message,
                history=history,
                project_id=project_id,
                conversation_id=conversation_id,
            ):
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
