"""Chat Block - AI chat completions with OpenAI, Groq, Anthropic + streaming."""

import os
from typing import Any, Dict, List, Optional, AsyncGenerator
from app.core.block import BaseBlock, BlockConfig

class ChatBlock(BaseBlock):
    """AI chat completions supporting OpenAI, Groq, Anthropic, and streaming."""

    def __init__(self):
        super().__init__(BlockConfig(
            name="chat",
            version="1.2",
            description="AI chat completions with OpenAI, Groq, Anthropic + streaming. Chains perfectly with PDF/OCR/etc.",
            requires_api_key=True,
            supported_inputs=["text", "messages", "file_result"],
            supported_outputs=["text", "stream", "tokens", "model"]
        ,
            layer=2,
            tags=["ai", "core", "llm"]))
        self._openai_available = self._check_openai()
        self._anthropic_available = self._check_anthropic()
        self._groq_available = self._check_groq()
        self._deepseek_available = self._check_deepseek()

    def _check_openai(self) -> bool:
        try:
            import openai
            return bool(os.getenv("OPENAI_API_KEY"))
        except ImportError:
            return False

    def _check_anthropic(self) -> bool:
        try:
            import anthropic
            return bool(os.getenv("ANTHROPIC_API_KEY"))
        except ImportError:
            return False

    def _check_groq(self) -> bool:
        try:
            import groq
            return bool(os.getenv("GROQ_API_KEY"))
        except ImportError:
            return False

    def _check_deepseek(self) -> bool:
        try:
            import openai  # DeepSeek uses OpenAI-compatible API
            return bool(os.getenv("DEEPSEEK_API_KEY"))
        except ImportError:
            return False

    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Main processing logic — only part you ever change per block."""
        params = params or {}
        provider = params.get("provider", "deepseek")    # default to cheapest
        # Set appropriate default model based on provider
        default_models = {
            "deepseek": "deepseek-chat",
            "groq": "llama-3.3-70b-versatile",
            "openai": "gpt-3.5-turbo",
            "anthropic": "claude-3-haiku-20240307",
            "mock": "mock-model"
        }
        model = params.get("model", default_models.get(provider, "deepseek-chat"))
        temperature = params.get("temperature", 0.7)
        max_tokens = params.get("max_tokens", 2048)
        stream = params.get("stream", False)
        system = params.get("system", "You are a helpful assistant.")

        # Build messages (works with text, list, or previous block result)
        messages = self._build_messages(input_data, params.get("prompt", ""), system)

        result = {
            "provider": provider,
            "model": model,
            "messages_sent": len(messages),
        }

        if stream:
            result["stream"] = self._get_stream_generator(provider, messages, model, max_tokens, temperature)
            result["text"] = ""
            return result

        # Non-streaming (cheapest first, with fallback to mock)
        try:
            if provider == "deepseek" and self._deepseek_available:
                response = await self._call_deepseek(messages, model or "deepseek-chat", max_tokens, temperature)
            elif provider == "groq" and self._groq_available:
                response = await self._call_groq(messages, model, max_tokens, temperature)
            elif provider == "openai" and self._openai_available:
                response = await self._call_openai(messages, model, max_tokens, temperature)
            elif provider == "anthropic" and self._anthropic_available:
                response = await self._call_anthropic(messages, model, max_tokens, temperature)
            elif provider == "mock":
                response = self._call_mock(messages)
            else:
                # Auto-fallback to mock if no providers available
                response = self._call_mock(messages)
        except Exception as e:
            # If any provider fails, fallback to mock
            response = self._call_mock(messages)

        result.update(response)
        return result

    def _build_messages(self, input_data: Any, prompt: str, system: str) -> List[Dict]:
        messages = [{"role": "system", "content": system}]

        if isinstance(input_data, list):
            messages.extend(input_data)
        elif isinstance(input_data, dict):
            if "text" in input_data:
                messages.append({"role": "user", "content": input_data["text"]})
            elif "result" in input_data and isinstance(input_data["result"], dict):
                text = input_data["result"].get("text", input_data["result"].get("extracted_text", ""))
                if text:
                    messages.append({"role": "user", "content": text})
            elif "messages" in input_data:
                messages.extend(input_data["messages"])
        elif isinstance(input_data, str):
            messages.append({"role": "user", "content": input_data})

        if prompt:
            messages.append({"role": "user", "content": prompt})

        return messages

    def _get_stream_generator(self, provider: str, messages: List[Dict], model: str, max_tokens: int, temperature: float):
        """Returns async generator for streaming."""
        if provider == "deepseek" and self._deepseek_available:
            return self._stream_deepseek(messages, model, max_tokens, temperature)
        elif provider == "groq" and self._groq_available:
            return self._stream_groq(messages, model, max_tokens, temperature)
        elif provider == "openai" and self._openai_available:
            return self._stream_openai(messages, model, max_tokens, temperature)
        elif provider == "anthropic" and self._anthropic_available:
            return self._stream_anthropic(messages, model, max_tokens, temperature)
        else:
            async def mock_stream():
                text = "[Mock stream - provider not available]"
                for word in text.split():
                    yield {"text": word + " ", "done": False}
                yield {"text": "", "done": True}
            return mock_stream()

    # ==================== PROVIDER CALLS ====================

    def _call_mock(self, messages: List[Dict]) -> Dict:
        """Mock provider for demo/development - no API key needed."""
        user_message = messages[-1]["content"] if messages else ""
        
        # Simple canned responses based on keywords
        user_lower = user_message.lower()
        if "hello" in user_lower or "hi" in user_lower:
            response_text = "Hello! I'm running in demo mode. Set DEEPSEEK_API_KEY for real AI responses."
        elif "python" in user_lower or "code" in user_lower:
            response_text = "```python\nprint('Hello, World!')\n```\n\nI'm in demo mode. Add an API key for real code generation."
        elif "ai" in user_lower or "what is" in user_lower:
            response_text = "AI (Artificial Intelligence) refers to computer systems that can perform tasks that typically require human intelligence.\n\n*[Demo mode - add API key for full responses]*"
        elif "summarize" in user_lower:
            response_text = "This is a summary of your text.\n\n*[Demo mode - add API key for real summarization]*"
        else:
            response_text = f"I received: '{user_message[:50]}...'\n\nI'm running in mock/demo mode. To get real AI responses, set DEEPSEEK_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY."
        
        return {
            "text": response_text,
            "model": "mock-model",
            "finish_reason": "stop",
            "tokens_prompt": len(str(messages)) // 4,
            "tokens_completion": len(response_text) // 4,
            "tokens_total": (len(str(messages)) + len(response_text)) // 4,
            "confidence": 1.0,
            "provider": "mock"
        }

    async def _call_deepseek(self, messages: List[Dict], model: str, max_tokens: int, temperature: float) -> Dict:
        """Call DeepSeek API - cheapest provider ($0.14/M tokens)."""
        import openai
        client = openai.AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )
        try:
            # Ensure we use a valid DeepSeek model
            deepseek_model = model if model and "deepseek" in model else "deepseek-chat"
            response = await client.chat.completions.create(
                model=deepseek_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            return {
                "text": response.choices[0].message.content,
                "model": deepseek_model,
                "finish_reason": response.choices[0].finish_reason,
                "tokens_prompt": response.usage.prompt_tokens,
                "tokens_completion": response.usage.completion_tokens,
                "tokens_total": response.usage.total_tokens,
                "confidence": 0.96
            }
        except Exception as e:
            return {"text": f"[DeepSeek Error: {str(e)}]", "confidence": 0.0}

    async def _call_groq(self, messages: List[Dict], model: str, max_tokens: int, temperature: float) -> Dict:
        import groq
        client = groq.AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            return {
                "text": response.choices[0].message.content,
                "model": model or "llama-3.3-70b-versatile",
                "finish_reason": response.choices[0].finish_reason,
                "tokens_prompt": response.usage.prompt_tokens,
                "tokens_completion": response.usage.completion_tokens,
                "tokens_total": response.usage.total_tokens,
                "confidence": 0.98
            }
        except Exception as e:
            return {"text": f"[Groq Error: {str(e)}]", "confidence": 0.0}

    async def _call_openai(self, messages: List[Dict], model: str, max_tokens: int, temperature: float) -> Dict:
        import openai
        client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            return {
                "text": response.choices[0].message.content,
                "finish_reason": response.choices[0].finish_reason,
                "tokens_prompt": response.usage.prompt_tokens,
                "tokens_completion": response.usage.completion_tokens,
                "tokens_total": response.usage.total_tokens,
                "confidence": 0.95
            }
        except Exception as e:
            return {"text": f"[OpenAI Error: {str(e)}]", "confidence": 0.0}

    async def _call_anthropic(self, messages: List[Dict], model: str, max_tokens: int, temperature: float) -> Dict:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        try:
            system_msg = next((m["content"] for m in messages if m["role"] == "system"), None)
            chat_messages = [m for m in messages if m["role"] != "system"]
            response = await client.messages.create(
                model=model or "claude-3-haiku-20240307",
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_msg,
                messages=chat_messages
            )
            return {
                "text": response.content[0].text,
                "confidence": 0.97
            }
        except Exception as e:
            return {"text": f"[Anthropic Error: {str(e)}]", "confidence": 0.0}

    # Streaming helpers (similar pattern)

    async def _stream_deepseek(self, messages, model, max_tokens, temperature):
        """Stream from DeepSeek - cheapest provider."""
        import openai
        client = openai.AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )
        try:
            stream = await client.chat.completions.create(
                model=model or "deepseek-chat", messages=messages, max_tokens=max_tokens,
                temperature=temperature, stream=True
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield {"text": chunk.choices[0].delta.content, "done": False}
            yield {"text": "", "done": True}
        except Exception as e:
            yield {"text": f"[DeepSeek Stream Error: {str(e)}]", "done": True}

    async def _stream_groq(self, messages, model, max_tokens, temperature):
        import groq
        client = groq.AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        try:
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=temperature, stream=True
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield {"text": chunk.choices[0].delta.content, "done": False}
            yield {"text": "", "done": True}
        except Exception as e:
            yield {"text": f"[Groq Stream Error: {str(e)}]", "done": True}

    async def _stream_openai(self, messages, model, max_tokens, temperature):
        import openai
        client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        try:
            stream = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=temperature, stream=True
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield {"text": chunk.choices[0].delta.content, "done": False}
            yield {"text": "", "done": True}
        except Exception as e:
            yield {"text": f"[OpenAI Stream Error: {str(e)}]", "done": True}

    async def _stream_anthropic(self, messages, model, max_tokens, temperature):
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        try:
            system_msg = next((m["content"] for m in messages if m["role"] == "system"), None)
            chat_messages = [m for m in messages if m["role"] != "system"]
            async with client.messages.stream(
                model=model or "claude-3-haiku-20240307",
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_msg,
                messages=chat_messages
            ) as stream:
                async for text in stream.text_stream:
                    yield {"text": text, "done": False}
                yield {"text": "", "done": True}
        except Exception as e:
            yield {"text": f"[Anthropic Stream Error: {str(e)}]", "done": True}
