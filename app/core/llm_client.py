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
    timeout: Optional[float] = None,
) -> str:
    """Return the assistant message text for a tool-less completion.

    Mirrors ``Agent._call_llm``'s provider fallback: on a RETRYABLE failure
    from the primary (408/413/429/5xx or a network/timeout error) it retries
    once against ``LLM_FALLBACK_PROVIDER`` so the orchestrator's intent routing
    survives a Groq rate-limit instead of silently losing smart routing for the
    turn. Auth/validation errors (other 4xx) are not retried.
    """
    from app.agents.runtime import _llm_config, _llm_fallback_config, _llm_http_timeout
    cfg = _llm_config()
    fallback_cfg = _llm_fallback_config(cfg)
    if timeout is None:
        timeout = _llm_http_timeout()

    attempts = [(cfg, os.getenv(cfg["env_key"]) if cfg.get("env_key") else "", model or cfg["default_model"])]
    if fallback_cfg:
        fb_key = os.getenv(fallback_cfg["env_key"]) if fallback_cfg["env_key"] else ""
        attempts.append((fallback_cfg, fb_key, fallback_cfg["default_model"]))

    def _retryable(status: int) -> bool:
        return status in (408, 413, 429) or status >= 500

    last_exc: Optional[Exception] = None
    for idx, (a_cfg, a_key, a_model) in enumerate(attempts):
        is_last = idx == len(attempts) - 1
        # Honor a provider's fixed temperature (Kimi k2.6 and other reasoning
        # models REJECT any temperature but 1 with HTTP 400 — the same rule
        # Agent._call_llm applies via fixed_temperature). Without this, the
        # predefined-synthesis path 400'd on Kimi and silently fell back to
        # the deterministic render.
        eff_temperature = a_cfg.get("fixed_temperature", temperature)
        payload: Dict[str, Any] = {
            "model": a_model,
            "messages": messages,
            "temperature": eff_temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if a_key:
            headers["Authorization"] = f"Bearer {a_key}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(a_cfg["url"], json=payload, headers=headers)
                if r.status_code >= 400:
                    exc = httpx.HTTPStatusError(
                        f"{a_cfg['provider']} HTTP {r.status_code}", request=r.request, response=r
                    )
                    if _retryable(r.status_code) and not is_last:
                        last_exc = exc
                        continue
                    raise exc
                data = r.json()
            return (data["choices"][0]["message"].get("content") or "").strip()
        except httpx.HTTPStatusError:
            raise
        except Exception as e:  # network / timeout / parse
            last_exc = e
            if not is_last:
                continue
            raise
    # Unreachable in practice (loop always returns or raises), but keeps types honest.
    raise last_exc or RuntimeError("LLM completion failed")


async def complete_json(
    system: str,
    user: str,
    *,
    max_tokens: int = 500,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
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
