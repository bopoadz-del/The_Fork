"""Provider-agnostic LLM completion — a thin, tool-less call the orchestrator's
dynamic reasoning uses for UNDERSTAND / PLAN.

Reuses `runtime._llm_config()` so it follows whatever provider is configured:
Ollama Cloud in production (LLM_PROVIDER=ollama + OLLAMA_URL + OLLAMA_API_KEY +
a -cloud model), local Ollama for dev, or Groq/DeepSeek. The reasoner never
hardcodes a provider — "on the cloud" is a config choice, not a code change.
"""
from __future__ import annotations

import json as _json
import os
from typing import Any, Dict, List, Optional

import httpx


async def complete(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    max_tokens: int = 800,
    model: Optional[str] = None,
    timeout: float = 120.0,
) -> str:
    """Return the assistant message text for a tool-less completion."""
    from app.agents.runtime import _llm_config
    cfg = _llm_config()
    api_key = os.getenv(cfg["env_key"]) if cfg.get("env_key") else ""
    payload: Dict[str, Any] = {
        "model": model or cfg["default_model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(cfg["url"], json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


async def complete_json(
    system: str,
    user: str,
    *,
    max_tokens: int = 500,
    model: Optional[str] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Completion that must return a JSON object. Tolerant parse: strips code
    fences and pulls the first {...} block. Returns {} on unparseable output."""
    text = await complete(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0, max_tokens=max_tokens, model=model, timeout=timeout,
    )
    return _extract_json_object(text)


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    try:
        obj = _json.loads(t)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    start, depth = t.find("{"), 0
    if start == -1:
        return {}
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = _json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else {}
                except Exception:
                    return {}
    return {}
