import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.blocks import BLOCK_REGISTRY
from app.core.action_router import (
    best_action,
    hint_for_orchestrator_result,
    needs_planning,
)
from app.dependencies import require_user
from app.dependencies import block_instances

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    model: str = "deepseek-chat"
    stream: bool = False
    project_id: Optional[str] = None


async def _classify_intent(prompt: str) -> tuple[Optional[str], float]:
    """Run the smart_orchestrator over the user's message and return the top
    (action, confidence) classification, or (None, 0.0) on any error.

    Used by the chat router to decide whether to take the multi-step
    heavy-reasoning agent path (action is generative + confidence ≥ 0.5)
    or stay on the fast single-shot chat block path.
    """
    if not prompt or not prompt.strip():
        return None, 0.0
    try:
        if "smart_orchestrator" not in block_instances:
            block_instances["smart_orchestrator"] = (
                BLOCK_REGISTRY["smart_orchestrator"]()
            )
        orchestrator = block_instances["smart_orchestrator"]
        result = await orchestrator.process({"user_message": prompt})
        return best_action(result)
    except Exception:
        return None, 0.0


async def _stream_from_heavy_reasoning(
    user_message: str,
    project_id: Optional[str],
    user_id: Optional[str],
    history: List[Dict[str, Any]],
    session_id: str,
):
    """Run the heavy-reasoning runtime agent and yield SSE events.

    This is the multi-step generative path. The agent's tool-call loop emits
    iteration / tool_call / tool_result events via the new on_event callback
    on Agent.chat() so the UI can render a live reasoning trace. The final
    answer is then streamed to the browser in word-sized chunks so the
    existing chat UI continues to update progressively.

    On any agent error: emit one error SSE event and stop — the caller will
    see the error and can decide whether to retry the fast path.
    """
    yield f"data: {json.dumps({'type': 'start', 'session_id': session_id, 'mode': 'heavy_reasoning'})}\n\n"

    # Pipe agent events into an asyncio queue so we can yield SSE chunks as
    # they arrive instead of buffering everything until the agent returns.
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    async def on_event(name: str, payload: Dict[str, Any]) -> None:
        await queue.put((name, payload))

    async def run_agent() -> Dict[str, Any]:
        try:
            from app.agents import get_agent
            agent = get_agent("heavy-reasoning")
            if agent is None:
                return {"status": "error", "error": "heavy-reasoning agent not loaded"}
            result = await agent.chat(
                user_message=user_message,
                history=history,
                project_id=project_id,
                conversation_id=f"hr-{session_id}" if session_id else None,
                on_event=on_event,
            )
            return result
        except Exception as e:
            logger.exception("heavy-reasoning agent crashed")
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}
        finally:
            await queue.put((SENTINEL, None))

    agent_task = asyncio.create_task(run_agent())

    # Drain events as they arrive, emitting SSE for each.
    while True:
        name, payload = await queue.get()
        if name is SENTINEL:
            break
        # Forward only the events the UI knows how to render.
        if name in ("iteration", "tool_call", "tool_result", "final"):
            yield f"data: {json.dumps({'type': name, **payload})}\n\n"

    result = await agent_task

    if result.get("status") == "error":
        err = result.get("error") or "Heavy reasoning failed"
        yield f"data: {json.dumps({'type': 'error', 'message': err})}\n\n"
        return

    # Stream the final answer in word chunks so the UI's existing
    # progressive renderer continues to work. The 'final' event has
    # already been emitted by the agent loop above with the same answer,
    # but the UI's main bubble re-uses 'token' events to accumulate text.
    answer = result.get("answer") or ""
    if not answer.strip():
        answer = "(no answer produced)"
    for word in answer.split(" "):
        yield f"data: {json.dumps({'type': 'token', 'content': word + ' '})}\n\n"
        await asyncio.sleep(0.01)

    tools_used = sorted({
        (tc.get("name") or "unknown")
        for tc in (result.get("tool_calls") or [])
    })
    end_event = {
        "type": "end",
        "complete": True,
        "mode": "heavy_reasoning",
        "iterations": result.get("iterations", 0),
        "tools_used": tools_used,
    }
    yield f"data: {json.dumps(end_event)}\n\n"


async def _with_domain_hint(prompt: str) -> str:
    """Prepend a smart-orchestrator domain hint when the user's message matches
    a known intent above the confidence threshold.

    The chat block doesn't expose a system-message channel, so the hint piggy-
    backs on the user message as a leading bracketed instruction. The LLM
    treats it as scope-setting context.

    No-ops on any orchestrator error so the chat path stays robust.
    """
    try:
        if "smart_orchestrator" not in block_instances:
            block_instances["smart_orchestrator"] = (
                BLOCK_REGISTRY["smart_orchestrator"]()
            )
        orchestrator = block_instances["smart_orchestrator"]
        result = await orchestrator.process({"user_message": prompt})
        hint = hint_for_orchestrator_result(result)
        if hint:
            return f"[Context for your answer: {hint}]\n\n{prompt}"
    except Exception:
        pass
    return prompt


def _with_project_memory(
    prompt: str, project_id: Optional[str], user_id: Optional[str]
) -> str:
    """Prepend a project's accumulated facts to the prompt (Roadmap V2 · Epic 3/4).

    So a question can be answered from project memory without re-attaching the
    source document. No-op when the chat is not scoped to a project, and — for
    tenant isolation — also a no-op when the caller does not own the project,
    so a caller cannot read another tenant's project memory by guessing an id.
    """
    if not project_id:
        return prompt
    try:
        from app.core import projects as projects_store
        if projects_store.get_project(project_id, user_id=user_id) is None:
            return prompt  # not the caller's project — do not inject its memory
        from app.core.project_memory import build_memory_context
        ctx = build_memory_context(project_id, prompt)
        if ctx:
            return f"{ctx}\n\n---\n\n{prompt}"
    except Exception:
        pass
    return prompt


async def _with_doc_search(
    prompt: str, project_id: Optional[str], user_id: Optional[str], top_k: int = 5,
) -> str:
    """Prepend top-k relevant document snippets from the project's zvec index.

    This is the upload→index→chat connection: when the user has a project
    selected, every uploaded file goes into doc_index (via the project
    documents endpoint, which schedules zvec indexing). Here we query that
    index with the user's question and prepend the matching snippets so the
    LLM can answer FROM the actual file content — not just from memory of
    the last upload.

    Same shape as project_reasoner.process() uses for its RELEVANT DOCUMENT
    EXCERPTS section — kept consistent so both paths reason from the same
    grounded data.

    No-op when project_id missing, when caller doesn't own the project, or
    when the project has no indexed documents yet.
    """
    if not project_id or not prompt.strip():
        return prompt
    try:
        from app.core import projects as projects_store
        if projects_store.get_project(project_id, user_id=user_id) is None:
            return prompt  # tenant isolation: don't search another user's docs
        from app.core.doc_index import search_project_documents
        snippets = await search_project_documents(project_id, prompt, top_k=top_k)
        if not snippets:
            return prompt
        # Cap each snippet to keep the prompt bounded. The reasoner uses 800.
        MAX_SNIPPET_CHARS = 800
        lines = []
        for i, s in enumerate(snippets, start=1):
            filename = s.get("filename") or s.get("document_id") or f"doc-{i}"
            text = (s.get("snippet") or "").strip()
            if not text:
                continue
            if len(text) > MAX_SNIPPET_CHARS:
                text = text[:MAX_SNIPPET_CHARS].rstrip() + "..."
            lines.append(f"[{i}] {filename}\n{text}")
        if not lines:
            return prompt
        excerpts_block = (
            "RELEVANT DOCUMENT EXCERPTS (from this project's indexed files; "
            "use as evidence when answering):\n" + "\n\n".join(lines)
        )
        return f"{excerpts_block}\n\n---\n\n{prompt}"
    except Exception:
        # Degrade silently — chat must never break because indexing has a hiccup.
        return prompt


@router.post("/chat")
async def chat(request: ChatRequest, auth: dict = Depends(require_user)):
    """Simple chat endpoint."""
    if "chat" not in BLOCK_REGISTRY:
        raise HTTPException(500, "Chat block not available")

    try:
        if "chat" not in block_instances:
            block_instances["chat"] = BLOCK_REGISTRY["chat"]()

        block = block_instances["chat"]
        message = _with_project_memory(
            request.message, request.project_id, auth["user_id"]
        )
        # Search the project's zvec index for snippets relevant to the user's
        # question — uploaded files become reachable here.
        message = await _with_doc_search(
            message, request.project_id, auth["user_id"]
        )
        message = await _with_domain_hint(message)
        result = await block.execute(message, {
            "model": request.model,
            "stream": False,
        })

        return {
            "text": result.get("result", {}).get("text", ""),
            "model": request.model,
        }

    except HTTPException:
        raise
    except Exception:
        # Do not leak internal exception detail to the client.
        raise HTTPException(500, "Chat failed")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, auth: dict = Depends(require_user)):
    """Streaming chat endpoint."""
    if "chat" not in BLOCK_REGISTRY:
        raise HTTPException(500, "Chat block not available")

    async def event_stream():
        try:
            if "chat" not in block_instances:
                block_instances["chat"] = BLOCK_REGISTRY["chat"]()

            block = block_instances["chat"]
            result = await block.execute(request.message, {
                "model": request.model,
                "stream": True,
            })

            # Get stream generator
            stream_gen = result.get("result", {}).get("stream")
            if stream_gen:
                async for token in stream_gen:
                    # Support both raw strings and JSON-encoded error objects
                    if isinstance(token, str) and token.startswith('{"type": "error"'):
                        yield f"data: {token}\n\n"
                        return
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
            else:
                # Fallback: simulate streaming
                text = result.get("result", {}).get("text", "")
                words = text.split()
                for word in words:
                    yield f"data: {json.dumps({'type': 'token', 'content': word + ' '})}\n\n"
                    await asyncio.sleep(0.05)

            yield f"data: {json.dumps({'type': 'end', 'complete': True})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/v1/chat")
async def chat_v1(request: ChatRequest, auth: dict = Depends(require_user)):
    """Simple chat endpoint (v1 API)."""
    return await chat(request, auth)


@router.post("/v1/chat/stream")
async def chat_stream_v1(request: Request, auth: dict = Depends(require_user)):
    """Streaming chat endpoint (v1 API) with flexible JSON body."""
    if "chat" not in BLOCK_REGISTRY:
        raise HTTPException(500, "Chat block not available")

    try:
        body = await request.json()
    except Exception:
        body = {}

    prompt = body.get("prompt", body.get("message", ""))
    model = body.get("model", body.get("provider", "deepseek-chat"))
    session_id = body.get("session_id", "default")
    history = body.get("history", []) or []
    project_id = body.get("project_id")

    # ── Intent classification: route generative multi-step intents to the
    # heavy-reasoning agent; everything else stays on the fast chat path.
    # The classifier reads the RAW prompt (no history flatten, no prepends)
    # so the orchestrator's keyword matcher sees the user's actual words.
    #
    # Routing does NOT require a project — "create a 300-activity schedule"
    # needs the agent's tools (construction.generate_wbs, formula_executor_v2)
    # whether or not there's a project_id. Gating on project_id meant the
    # most common generative requests fell through to the fast single-shot
    # path, where the LLM invented manpower histograms and refused to build
    # schedules. File context still reaches the agent via the prompt itself
    # (the frontend prepends sessionFileContexts), and the agent's own RAG
    # tool gracefully no-ops when project_id is None.
    action, confidence = await _classify_intent(prompt)
    if needs_planning(action, confidence):
        # Heavy-reasoning path. Returns its own StreamingResponse from the
        # event generator and bypasses the fast pipeline entirely.
        logger.info(
            "chat → heavy-reasoning: action=%s confidence=%.2f project=%s",
            action, confidence, project_id or "<none>"
        )
        return StreamingResponse(
            _stream_from_heavy_reasoning(
                user_message=prompt,
                project_id=project_id,
                user_id=auth.get("user_id"),
                history=history,
                session_id=session_id,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Flatten conversation history into a single prompt (the chat block doesn't
    # yet accept structured messages). Cap to last 10 turns to stay under token
    # budgets; trim each turn to 4000 chars to bound payload size.
    if history:
        recent = history[-10:]
        parts = []
        for turn in recent:
            role = (turn.get("role") or "user").lower()
            label = "User" if role == "user" else ("Assistant" if role in ("assistant", "ai") else role.capitalize())
            content = str(turn.get("content") or "")[:4000]
            if content:
                parts.append(f"{label}: {content}")
        parts.append(f"User: {prompt}")
        full_prompt = "\n\n".join(parts)
    else:
        full_prompt = prompt

    # Scope the chat to a project — inject its accumulated memory (Epic 3/4).
    full_prompt = _with_project_memory(
        full_prompt, project_id, auth["user_id"]
    )
    # Search the project's zvec doc index for relevant uploaded content,
    # so the chat answers from the actual files — not just memory facts.
    full_prompt = await _with_doc_search(
        full_prompt, project_id, auth["user_id"]
    )
    # Smart-orchestrator domain hint, when the message matches a known intent
    # but below the routing threshold (≥0.4 hint, ≥0.5 routes to agent).
    full_prompt = await _with_domain_hint(full_prompt)

    async def event_stream():
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"

        try:
            if "chat" not in block_instances:
                block_instances["chat"] = BLOCK_REGISTRY["chat"]()

            block = block_instances["chat"]
            result = await block.execute(
                full_prompt,
                {"model": model, "stream": True}
            )

            # Surface backend errors (no API key, provider 4xx/5xx, etc.)
            # Error can be at top level or nested under result.result.
            if isinstance(result, dict) and result.get("status") == "error":
                inner_err = (result.get("result") or {}) if isinstance(result.get("result"), dict) else {}
                err_msg = (
                    result.get("error")
                    or inner_err.get("error")
                    or "Chat block returned an error"
                )
                yield f"data: {json.dumps({'type': 'error', 'message': err_msg})}\n\n"
                return

            inner = result.get("result", {}) if isinstance(result, dict) else {}
            stream_gen = inner.get("stream")
            if stream_gen:
                async for token in stream_gen:
                    if isinstance(token, str) and token.startswith('{"type": "error"'):
                        yield f"data: {token}\n\n"
                        return
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                    await asyncio.sleep(0.01)
            else:
                text = inner.get("text", "")
                if not text:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'No response from chat provider — check that DEEPSEEK_API_KEY or ANTHROPIC_API_KEY is set in .env'})}\n\n"
                    return
                words = text.split()
                for word in words:
                    yield f"data: {json.dumps({'type': 'token', 'content': word + ' '})}\n\n"
                    await asyncio.sleep(0.05)

            yield f"data: {json.dumps({'type': 'end', 'complete': True})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
