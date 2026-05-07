import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.blocks import BLOCK_REGISTRY
from app.dependencies import require_api_key
from app.dependencies import block_instances

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    model: str = "deepseek-chat"
    stream: bool = False


@router.post("/chat")
async def chat(request: ChatRequest, auth: dict = Depends(require_api_key)):
    """Simple chat endpoint."""
    if "chat" not in BLOCK_REGISTRY:
        raise HTTPException(500, "Chat block not available")

    try:
        if "chat" not in block_instances:
            block_instances["chat"] = BLOCK_REGISTRY["chat"]()

        block = block_instances["chat"]
        result = await block.execute(request.message, {
            "model": request.model,
            "stream": False,
        })

        return {
            "text": result.get("result", {}).get("text", ""),
            "model": request.model,
        }

    except Exception as e:
        raise HTTPException(500, f"Chat failed: {str(e)}")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, auth: dict = Depends(require_api_key)):
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
async def chat_v1(request: ChatRequest, auth: dict = Depends(require_api_key)):
    """Simple chat endpoint (v1 API)."""
    return await chat(request)


@router.post("/v1/chat/stream")
async def chat_stream_v1(request: Request, auth: dict = Depends(require_api_key)):
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
