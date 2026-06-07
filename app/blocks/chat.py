"""Chat Block — DeepSeek primary + local-inference fallback.

The chat must never go completely dark on the user. Order of attempts:

1. **DeepSeek API** when ``DEEPSEEK_API_KEY`` is set and the endpoint is reachable.
2. **Local LLM** (kept *inside* the platform — no third-party cloud) via:
   - Ollama HTTP at ``OLLAMA_URL`` (default ``http://localhost:11434``) when a
     local model is installed. The default local model is
     ``LOCAL_LLM_MODEL`` (default ``qwen2.5:3b-instruct`` — small, CPU-runnable).
   - llama.cpp via ``LLAMA_CPP_MODEL_PATH`` when ``llama-cpp-python`` is
     importable and a GGUF file is provided.
3. **Graceful template responder** — a deterministic, non-AI fallback that
   acknowledges the question, surfaces the reason the model layer is down,
   and points the operator at the env vars that would restore it. This
   path always succeeds, so the chat never returns an unhandled error.

The block exposes a single ``provider`` field on the response so callers can
see which path served the answer (``deepseek`` / ``local_ollama`` /
``local_llama_cpp`` / ``offline_template``).
"""

import json
import os
import httpx
from typing import Any, Dict
from app.core.typed_block import TypedBlock, Schema, ContentType


DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_LOCAL_MODEL = "qwen2.5:3b-instruct"


class ChatBlock(TypedBlock):
    """AI chat completions — DeepSeek with local-inference fallback."""

    auto_validate = False
    name = "chat"
    version = "3.0.0"
    description = "AI chat completions — DeepSeek primary, local LLM fallback"
    layer = 2
    tags = ["ai", "core", "llm", "chat", "typed"]
    requires = []

    default_config = {
        "default_provider": "deepseek",
        "max_tokens": 2048,
        "temperature": 0.7,
    }

    accepted_input_types = ["Text", "TextContent", "ChatMessage"]
    produced_output_types = ["Text", "TextContent", "ChatMessage"]

    # Canonical text key for chain unwrapping. ChatBlock returns either
    # ``{"text": "..."}`` (DeepSeek path) or ``{"response": "..."}`` (local
    # LoRA path); ``"text"`` is the primary shape, so declaring it locks
    # the contract for the common case. The orchestrator's global fallback
    # still picks up ``"response"`` on the local path because it's in
    # _TEXT_OUTPUT_FIELDS, so no behaviour change either way.
    text_output_field = "text"

    input_schema = Schema(
        content_type=ContentType.TEXT,
        required_fields=[],
        optional_fields=["text", "message", "context"],
        format_hints={"max_length": 100000},
    )

    output_schema = Schema(
        content_type=ContentType.TEXT,
        required_fields=["text"],
        optional_fields=["provider", "model", "tokens", "status"],
        format_hints={},
    )

    ui_schema = {
        "input": {
            "type": "text",
            "accept": None,
            "placeholder": "Ask anything...",
            "multiline": True,
        },
        "output": {
            "type": "text",
            "fields": [
                {"name": "text", "type": "markdown", "label": "Response"},
            ],
        },
        "quick_actions": [
            {"icon": "💡", "label": "Explain", "prompt": "Explain this in simple terms"},
            {"icon": "📝", "label": "Summarize", "prompt": "Summarize the key points"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        if isinstance(input_data, dict):
            message = (
                input_data.get("text")
                or input_data.get("content")
                or input_data.get("extracted_text")
                or str(input_data)
            )
        else:
            message = str(input_data)

        max_tokens = params.get("max_tokens", self.config.get("max_tokens", 2048))
        temperature = params.get("temperature", self.config.get("temperature", 0.7))
        stream = params.get("stream", False)
        model = params.get("model", "deepseek-chat")

        # ── RAG (PR 2) — strictly opt-in. Defaults preserve existing behavior.
        # When use_rag=True AND project_id is provided AND the embedding
        # stack is installed, retrieve top-k chunks and prepend them as
        # context. Failures here never abort the chat call — retrieval is
        # best-effort enrichment, not a hard dependency.
        use_rag = bool(
            params.get("use_rag")
            or (isinstance(input_data, dict) and input_data.get("use_rag"))
        )
        rag_project_id = (
            params.get("project_id")
            or (isinstance(input_data, dict) and input_data.get("project_id"))
        )
        if use_rag and rag_project_id:
            try:
                from app.core.rag.retriever import retrieve as _retrieve
                rag_k = int(params.get("rag_k", 5))
                chunks = _retrieve(message, str(rag_project_id), k=rag_k)
                if chunks:
                    context = "\n\n".join(
                        f"[{c.doc_id}#{c.chunk_index}] {c.text}" for c in chunks
                    )
                    message = (
                        f"Relevant project context:\n{context}\n\n"
                        f"---\n\nUser question: {message}"
                    )
            except Exception as exc:  # noqa: BLE001
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "RAG retrieval failed for project %s: %s", rag_project_id, exc
                )

        # ── Local fine-tuned model (PR 3a) — opt-in. Defaults preserve
        # existing cloud-first behavior. When use_local_model=true AND the
        # local stack is available (deps installed + adapter present), the
        # generation runs through the LoRA-tuned model and bypasses the
        # cloud provider entirely. Failures fall through to the existing
        # cloud chain — chat never goes dark because of a local-model bug.
        use_local_model = bool(
            params.get("use_local_model")
            or (isinstance(input_data, dict) and input_data.get("use_local_model"))
        )
        if use_local_model:
            from app.core.learning import local_model as _local

            if _local.available():
                local_text = _local.generate(
                    message,
                    max_new_tokens=int(max_tokens),
                    temperature=float(temperature),
                )
                if local_text:
                    return {
                        "status": "success",
                        "response": local_text,
                        "provider": "local_lora",
                        "model": os.getenv("LOCAL_BASE_MODEL") or "Qwen/Qwen2.5-3B-Instruct",
                        "adapter": os.getenv("LOCAL_ADAPTER_DIR") or "data/learning/adapters/construction_v1",
                    }
                # generate() returned None — fall through to cloud chain
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "use_local_model requested but generate() returned None; falling back to cloud"
                )
            else:
                import logging as _logging
                _logging.getLogger(__name__).info(
                    "use_local_model requested but local stack unavailable; falling back to cloud"
                )

        # ── Cloud provider selection — DeepSeek or Groq depending on which
        # creds are set. This is the same _llm_config() the agent runtime
        # uses so that LLM_PROVIDER=groq applies uniformly across the chat
        # block route and the agent path.
        from app.agents.runtime import _llm_config  # local import: avoid cycle at module load
        cfg = _llm_config()
        provider_key = os.getenv(cfg["env_key"])
        primary_error = None

        if provider_key:
            # Agent configs (and this block's default) pin "deepseek-chat".
            # When the active provider is NOT DeepSeek, remap that
            # placeholder onto the provider's default model. An explicit
            # provider-specific model id is left alone.
            effective_model = model
            if cfg["provider"] != "deepseek" and effective_model.startswith("deepseek-"):
                effective_model = cfg["default_model"]
            result = await self._call_cloud(
                message, effective_model, max_tokens, temperature, stream,
                provider_key, cfg,
            )
            if result.get("status") == "success":
                return result
            primary_error = result.get("error", f"{cfg['provider']} call failed")
        else:
            primary_error = f"{cfg['env_key']} not configured"

        # ── Local inference fallback ───────────────────────────────────────
        local = await self._call_local(message, max_tokens, temperature, primary_error)
        if local.get("status") == "success":
            return local

        # ── Graceful template — chat must not go dark ──────────────────────
        return self._offline_template(message, primary_error, local.get("error"))

    # ────────────────────────────────────────────────────────────────────────
    # Cloud provider — chat completions (DeepSeek / Groq, OAI-shape protocol)
    # ────────────────────────────────────────────────────────────────────────

    async def _call_cloud(
        self,
        message: str,
        model: str,
        max_tokens: int,
        temperature: float,
        stream: bool,
        api_key: str,
        cfg: Dict[str, str],
    ) -> Dict:
        url = cfg["url"]
        provider_name = cfg["provider"]
        if stream:
            async def _stream_generator():
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream(
                        "POST",
                        url,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": message}],
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            "stream": True,
                        },
                    ) as response:
                        if response.status_code != 200:
                            err = await response.aread()
                            yield json.dumps({
                                "type": "error",
                                "message": f"{provider_name} error {response.status_code}: {err[:200]}",
                            })
                            return
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data)
                                content = chunk["choices"][0].get("delta", {}).get("content", "")
                                if content:
                                    yield content
                            except Exception:
                                continue

            return {
                "status": "success",
                "text": "",
                "provider": provider_name,
                "model": model,
                "stream": _stream_generator(),
            }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": message}],
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                )
                if response.status_code != 200:
                    return {
                        "status": "error",
                        "error": f"{provider_name} API error (HTTP {response.status_code}): {response.text[:200]}",
                    }
                data = response.json()
                return {
                    "status": "success",
                    "text": data["choices"][0]["message"]["content"],
                    "provider": provider_name,
                    "model": model,
                    "tokens": data.get("usage", {}),
                }
        except httpx.TimeoutException:
            return {"status": "error", "error": f"{provider_name} request timed out"}
        except Exception as e:
            return {"status": "error", "error": f"{provider_name} failed: {e}"}

    # ────────────────────────────────────────────────────────────────────────
    # Local inference (Ollama → llama.cpp)
    # ────────────────────────────────────────────────────────────────────────

    async def _call_local(
        self, message: str, max_tokens: int, temperature: float, primary_error: str
    ) -> Dict:
        """Try local inference backends in priority order."""

        ollama_url = os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL)
        local_model = os.getenv("LOCAL_LLM_MODEL", DEFAULT_LOCAL_MODEL)
        ollama_result = await self._call_ollama(
            message, local_model, max_tokens, temperature, ollama_url
        )
        if ollama_result.get("status") == "success":
            ollama_result["fallback_reason"] = primary_error
            return ollama_result

        # llama.cpp — synchronous library, run only if importable AND a model path set
        gguf_path = os.getenv("LLAMA_CPP_MODEL_PATH")
        if gguf_path and os.path.exists(gguf_path):
            llama_result = self._call_llama_cpp(message, gguf_path, max_tokens, temperature)
            if llama_result.get("status") == "success":
                llama_result["fallback_reason"] = primary_error
                return llama_result
            return {
                "status": "error",
                "error": f"ollama: {ollama_result.get('error')}; llama_cpp: {llama_result.get('error')}",
            }

        return {
            "status": "error",
            "error": f"ollama unavailable ({ollama_result.get('error')}); no LLAMA_CPP_MODEL_PATH set",
        }

    async def _call_ollama(
        self,
        message: str,
        model: str,
        max_tokens: int,
        temperature: float,
        base_url: str,
    ) -> Dict:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": message}],
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_tokens,
                        },
                        "stream": False,
                    },
                )
                if response.status_code != 200:
                    return {
                        "status": "error",
                        "error": f"ollama HTTP {response.status_code}: {response.text[:200]}",
                    }
                data = response.json()
                text = (data.get("message") or {}).get("content", "")
                if not text:
                    return {"status": "error", "error": "ollama returned empty content"}
                return {
                    "status": "success",
                    "text": text,
                    "provider": "local_ollama",
                    "model": model,
                    "tokens": {
                        "input_tokens": data.get("prompt_eval_count"),
                        "output_tokens": data.get("eval_count"),
                    },
                }
        except httpx.ConnectError:
            return {"status": "error", "error": f"ollama not reachable at {base_url}"}
        except httpx.TimeoutException:
            return {"status": "error", "error": "ollama request timed out"}
        except Exception as e:
            return {"status": "error", "error": f"ollama failed: {e}"}

    def _call_llama_cpp(
        self, message: str, gguf_path: str, max_tokens: int, temperature: float
    ) -> Dict:
        try:
            from llama_cpp import Llama  # type: ignore
        except Exception as e:
            return {"status": "error", "error": f"llama-cpp-python not importable: {e}"}

        try:
            llm = Llama(model_path=gguf_path, n_ctx=4096, verbose=False)
            out = llm.create_chat_completion(
                messages=[{"role": "user", "content": message}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = (out.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not text:
                return {"status": "error", "error": "llama.cpp returned empty content"}
            return {
                "status": "success",
                "text": text,
                "provider": "local_llama_cpp",
                "model": os.path.basename(gguf_path),
                "tokens": out.get("usage", {}),
            }
        except Exception as e:
            return {"status": "error", "error": f"llama.cpp failed: {e}"}

    # ────────────────────────────────────────────────────────────────────────
    # Graceful offline template — last-resort: chat never goes dark
    # ────────────────────────────────────────────────────────────────────────

    def _offline_template(self, message: str, primary_error: str, local_error: str) -> Dict:
        snippet = (message or "").strip()
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        body = (
            "**Chat is running in offline mode.**\n\n"
            "No cloud or local language model is currently reachable, so I can't "
            "generate an AI response right now. Your message was received intact:\n\n"
            f"> {snippet or '(empty)'}\n\n"
            "**How to restore full chat:**\n"
            "- Set `GROQ_API_KEY` (free tier) or `DEEPSEEK_API_KEY` in `.env` to use a cloud provider, **or**\n"
            "- Run a local model: `ollama serve` + `ollama pull qwen2.5:3b-instruct`\n"
            "  (optionally set `OLLAMA_URL` and `LOCAL_LLM_MODEL`), **or**\n"
            "- Provide a GGUF file via `LLAMA_CPP_MODEL_PATH` with `llama-cpp-python` installed.\n\n"
            f"_Primary provider: {primary_error}_  \n"
            f"_Local inference: {local_error}_"
        )
        return {
            "status": "success",
            "text": body,
            "provider": "offline_template",
            "model": "template:v1",
            "primary_error": primary_error,
            "local_error": local_error,
        }
