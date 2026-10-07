"""One model call for a driver turn: the active provider, the turn's tools,
``tool_choice`` auto. No tool is forced and no message is rewritten."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


async def call(agent: Any, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]],
               model: str = "", timeout: float = 120.0) -> Dict[str, Any]:
    """``{"status": "success", "message": {...}}`` or ``{"status": "error", "error": ...}``."""
    import httpx

    from app.agents.runtime import (
        _llm_config, _provider_max_tokens, _provider_temperature, _resolve_attempt_model,
        _sanitize_messages_for_provider,
    )

    cfg = _llm_config()
    key = os.getenv(cfg["env_key"]) or ""
    if not key:
        return {"status": "error", "error": "The language model is not configured."}
    payload: Dict[str, Any] = {
        "model": model or _resolve_attempt_model(cfg, agent.model),
        "messages": _sanitize_messages_for_provider(messages),
        "temperature": _provider_temperature(cfg, agent.temperature),
        "max_tokens": _provider_max_tokens(cfg, agent.max_tokens),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(cfg["url"], json=payload,
                                     headers={"Authorization": f"Bearer {key}"})
        if resp.status_code != 200:
            return {"status": "error", "error": f"The language model answered HTTP {resp.status_code}."}
        choice = (resp.json().get("choices") or [{}])[0]
        return {"status": "success", "message": choice.get("message") or {}}
    except httpx.HTTPError as exc:
        return {"status": "error", "error": f"The language model could not be reached ({type(exc).__name__})."}
