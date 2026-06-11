"""Tinker-hosted LoRA adapter serving wrapper.

Gated by ``GROUNDED_ADAPTER_ENABLED`` (env var; default off). When the
flag is on AND a sampler-weights path is configured, the chat block
tries this backend first; on any failure it transparently falls back
to the existing Ollama / llama.cpp path.

Sampler-weights are *not* the same checkpoint Tinker writes during
training. The training script saves to ``/weights/...`` ; sampling
requires a ``/sampler_weights/...`` path produced by
``save_weights_for_sampler``. The eval scripts already handle this
conversion; for production we configure the sampler-weights URI
directly via ``GROUNDED_ADAPTER_TINKER_PATH``.

Env vars
========
- ``GROUNDED_ADAPTER_ENABLED``       — bool, off by default. "1" / "true" / "yes" turn it on.
- ``GROUNDED_ADAPTER_TINKER_PATH``   — full ``tinker://<session>:train:0/sampler_weights/<name>`` URI.
- ``GROUNDED_ADAPTER_TIMEOUT``       — seconds (default 60). Hard cap on a single sample call.
- ``GROUNDED_ADAPTER_REWRITE_PASS``  — bool, off by default. When on, the agent
  runtime re-grounds a tool-free cloud response through the adapter (broad path).
- ``TINKER_API_KEY``                 — required for the Tinker SDK to authenticate.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level cache: building a ServiceClient + sampling client is
# expensive (~1-2s; loads tokenizer + opens a session). Hold one per
# process and serialize concurrent calls with a thread lock so the
# cache initialization is idempotent.
_CACHE: Dict[str, Any] = {"service": None, "client": None, "tokenizer": None, "path": None}
_INIT_LOCK = threading.Lock()


def is_enabled() -> bool:
    flag = os.getenv("GROUNDED_ADAPTER_ENABLED", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def is_rewrite_pass_enabled() -> bool:
    """Separate, narrower gate for the broad rewrite-pass path. Independent of
    ``is_enabled`` so a deployer can turn rewrite on without changing the
    narrow forced-final wiring. Both flags must be true to actually rewrite."""
    flag = os.getenv("GROUNDED_ADAPTER_REWRITE_PASS", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def configured_path() -> Optional[str]:
    p = os.getenv("GROUNDED_ADAPTER_TINKER_PATH", "").strip()
    return p or None


def is_available() -> bool:
    """Lightweight check the chat block can call before each turn.
    No SDK import, no client init — just the env vars."""
    return is_enabled() and configured_path() is not None and bool(os.getenv("TINKER_API_KEY"))


def _ensure_client(path: str) -> tuple:
    """Create / return the cached (sampling_client, tokenizer) pair for
    ``path``. If the configured path changed (deploy with new adapter),
    the cache is rebuilt."""
    global _CACHE
    if _CACHE["client"] is not None and _CACHE["path"] == path:
        return _CACHE["client"], _CACHE["tokenizer"]
    with _INIT_LOCK:
        if _CACHE["client"] is not None and _CACHE["path"] == path:
            return _CACHE["client"], _CACHE["tokenizer"]
        import tinker
        svc = _CACHE.get("service") or tinker.ServiceClient()
        client = svc.create_sampling_client(model_path=path)
        tokenizer = client.get_tokenizer()
        _CACHE.update({"service": svc, "client": client, "tokenizer": tokenizer, "path": path})
        logger.info("grounded adapter client initialized (%s)", path)
        return client, tokenizer


def _sample_sync(message: str, system_prompt: Optional[str], max_tokens: int, temperature: float) -> Dict[str, Any]:
    """Blocking sample. Called via ``asyncio.to_thread`` from the async
    chat block."""
    import tinker
    from tinker.types import ModelInput

    path = configured_path()
    if not path:
        return {"status": "error", "error": "GROUNDED_ADAPTER_TINKER_PATH not set"}

    client, tokenizer = _ensure_client(path)

    # Build the same chat envelope the training set used. When a system
    # prompt is supplied (RAG inject path), we mirror the training shape:
    # ``Context:\n<system>\n\nQuestion: <user>`` in the user message. This
    # keeps inference format identical to training format — the whole
    # reason we're doing grounded training.
    if system_prompt and system_prompt.strip():
        user_content = f"Context:\n{system_prompt.strip()}\n\nQuestion: {message}"
    else:
        user_content = message
    user_msg = {"role": "user", "content": user_content}
    prompt_ids = tokenizer.encode_message_with_chat_template(user_msg, [user_msg])

    params = tinker.SamplingParams(
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=0.9,
    )
    future = client.sample(
        prompt=ModelInput.from_ints(prompt_ids),
        num_samples=1,
        sampling_params=params,
    )
    resp = future.result()
    seqs = getattr(resp, "sequences", None) or []
    if not seqs:
        return {"status": "error", "error": "no sequence returned"}
    seq = seqs[0]
    full_tokens = list(getattr(seq, "tokens_np", None) or seq._tokens_list)
    if full_tokens[: len(prompt_ids)] == prompt_ids:
        gen_tokens = full_tokens[len(prompt_ids):]
    else:
        gen_tokens = full_tokens
    text = tokenizer.decode(gen_tokens, skip_special_tokens=True).strip()
    return {
        "status": "success",
        "response": text,
        "provider": "tinker_grounded_adapter",
        "model": path,
    }


async def call(
    message: str,
    system_prompt: Optional[str],
    max_tokens: int,
    temperature: float,
    timeout_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Async wrapper: enforces a wall-clock timeout and routes the
    blocking SDK call to a worker thread so the FastAPI event loop
    isn't pinned. ``timeout_override`` lets callers (e.g. rewrite-pass)
    impose a tighter cap than ``GROUNDED_ADAPTER_TIMEOUT`` without
    mutating the env."""
    if timeout_override is not None:
        timeout = float(timeout_override)
    else:
        timeout = float(os.getenv("GROUNDED_ADAPTER_TIMEOUT", "60"))
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_sample_sync, message, system_prompt, max_tokens, temperature),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "error": f"grounded adapter timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001 — chat block will fall back on any failure
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
