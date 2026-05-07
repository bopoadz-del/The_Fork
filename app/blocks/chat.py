"""Chat Block - AI Chat with DeepSeek API and Anthropic fallback"""

import json
import os
import httpx
from typing import Any, Dict, Optional
from app.core.typed_block import TypedBlock, Schema, ContentType


class ChatBlock(TypedBlock):
    """AI chat completions with DeepSeek API and typed I/O"""

    name = "chat"
    version = "2.0.0"
    description = "AI chat completions with DeepSeek API"
    layer = 2
    tags = ["ai", "core", "llm", "chat", "typed"]
    requires = []

    default_config = {
        "default_provider": "deepseek",
        "max_tokens": 2048,
        "temperature": 0.7
    }

    # Type schemas for chain validation
    accepted_input_types = ["Text", "TextContent", "ChatMessage"]
    produced_output_types = ["Text", "TextContent", "ChatMessage"]

    input_schema = Schema(
        content_type=ContentType.TEXT,
        required_fields=[],  # Can be string or {text: ...}
        optional_fields=["text", "message", "context"],
        format_hints={"max_length": 100000}
    )

    output_schema = Schema(
        content_type=ContentType.TEXT,
        required_fields=["text"],
        optional_fields=["provider", "model", "tokens", "status"],
        format_hints={}
    )

    ui_schema = {
        "input": {
            "type": "text",
            "accept": None,
            "placeholder": "Ask anything...",
            "multiline": True
        },
        "output": {
            "type": "text",
            "fields": [
                {"name": "text", "type": "markdown", "label": "Response"}
            ]
        },
        "quick_actions": [
            {"icon": "💡", "label": "Explain", "prompt": "Explain this in simple terms"},
            {"icon": "📝", "label": "Summarize", "prompt": "Summarize the key points"}
        ]
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """Process chat request"""
        params = params or {}
        if isinstance(input_data, dict):
            # Accept output from upstream chain steps (pdf, ocr, etc.)
            message = (
                input_data.get("text") or
                input_data.get("content") or
                input_data.get("extracted_text") or
                str(input_data)
            )
        else:
            message = str(input_data)

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        model = params.get("model", "deepseek-chat")
        max_tokens = params.get("max_tokens", self.config.get("max_tokens", 2048))
        temperature = params.get("temperature", self.config.get("temperature", 0.7))
        stream = params.get("stream", False)

        # Try DeepSeek first, fall back to Anthropic Claude
        if deepseek_key:
            result = await self._call_deepseek(message, model, max_tokens, temperature, stream, deepseek_key)
            if result.get("status") == "success":
                return result
            # DeepSeek failed — try Anthropic fallback
            deepseek_error = result.get("error", "DeepSeek failed")
        else:
            deepseek_error = "DEEPSEEK_API_KEY not configured"

        if anthropic_key:
            return await self._call_anthropic(message, max_tokens, temperature, anthropic_key, fallback_reason=deepseek_error)

        return {
            "status": "error",
            "error": f"No AI provider available. {deepseek_error}. Set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY."
        }

    async def _call_deepseek(self, message: str, model: str, max_tokens: int, temperature: float, stream: bool, api_key: str) -> Dict:
        if stream:
            async def _stream_generator():
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream(
                        "POST",
                        "https://api.deepseek.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": model, "messages": [{"role": "user", "content": message}],
                              "max_tokens": max_tokens, "temperature": temperature, "stream": True},
                    ) as response:
                        if response.status_code != 200:
                            err = await response.aread()
                            yield json.dumps({"type": "error", "message": f"DeepSeek error {response.status_code}: {err[:200]}"})
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
            return {"status": "success", "text": "", "provider": "deepseek", "model": model, "stream": _stream_generator()}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": message}],
                          "max_tokens": max_tokens, "temperature": temperature},
                )
                if response.status_code != 200:
                    return {"status": "error", "error": f"DeepSeek API error (HTTP {response.status_code}): {response.text[:200]}"}
                data = response.json()
                return {"status": "success", "text": data["choices"][0]["message"]["content"],
                        "provider": "deepseek", "model": model, "tokens": data.get("usage", {})}
        except httpx.TimeoutException:
            return {"status": "error", "error": "DeepSeek request timed out"}
        except Exception as e:
            return {"status": "error", "error": f"DeepSeek failed: {str(e)}"}

    async def _call_anthropic(self, message: str, max_tokens: int, temperature: float, api_key: str, fallback_reason: str = "") -> Dict:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": message}],
            )
            text = response.content[0].text if response.content else ""
            return {
                "status": "success",
                "text": text,
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "tokens": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
                "fallback_reason": fallback_reason or None,
            }
        except Exception as e:
            return {"status": "error", "error": f"Anthropic fallback also failed: {str(e)}"}
