"""Agent runtime — system prompt + allowed-blocks tool list + LLM tool-calling loop.

Loads declarative agent configs from `app/agents/configs/*.md` (YAML frontmatter +
markdown body for the system prompt). Each agent can call any block in its
`allowed_blocks` list as a tool. The runtime handles the back-and-forth with the
LLM: turn → optional tool call(s) → run blocks → return results → continue.

Provider: DeepSeek (OpenAI-compatible /v1/chat/completions). Anthropic can be
added with a small adapter — left as a follow-up.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.blocks import BLOCK_REGISTRY
from app.dependencies import block_instances, _create_block_instance


CONFIGS_DIR = Path(__file__).parent / "configs"
MAX_TOOL_ITERATIONS = 8  # hard cap so a runaway loop can't burn budget
MAX_HISTORY_TURNS = 20

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"


@dataclass
class Agent:
    """Declarative agent definition."""

    name: str
    description: str
    system_prompt: str
    allowed_blocks: List[str] = field(default_factory=list)
    model: str = DEEPSEEK_DEFAULT_MODEL
    temperature: float = 0.3
    max_tokens: int = 2048
    icon: str = "🤖"

    def tool_definitions(self) -> List[Dict[str, Any]]:
        """Build OpenAI/DeepSeek-style tool definitions from allowed_blocks."""
        tools = []
        for block_name in self.allowed_blocks:
            block_class = BLOCK_REGISTRY.get(block_name)
            if not block_class:
                continue
            description = (getattr(block_class, "description", "") or f"Block: {block_name}")[:1024]
            tools.append({
                "type": "function",
                "function": {
                    "name": block_name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "description": "Input for the block — string, dict, or chain output.",
                            },
                            "params": {
                                "type": "object",
                                "description": "Optional block-specific parameters (e.g. {'action': 'auto_pipeline'}).",
                            },
                        },
                        "required": [],
                    },
                },
            })
        return tools

    # ── Public chat API ───────────────────────────────────────────────────
    async def chat(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Single round-trip: returns {answer, tool_calls, history}."""
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return {
                "status": "error",
                "error": "No DEEPSEEK_API_KEY configured. Set it in .env or pass via env.",
            }

        messages = self._build_messages(user_message, history or [])
        tool_calls_made: List[Dict[str, Any]] = []

        for iteration in range(MAX_TOOL_ITERATIONS):
            resp = await self._call_llm(messages, api_key)
            if resp.get("status") == "error":
                return resp
            choice = resp["choice"]
            assistant_msg = choice.get("message") or {}

            tool_calls = assistant_msg.get("tool_calls") or []
            if not tool_calls:
                final_text = assistant_msg.get("content") or ""
                messages.append({"role": "assistant", "content": final_text})
                return {
                    "status": "success",
                    "answer": final_text,
                    "tool_calls": tool_calls_made,
                    "iterations": iteration + 1,
                    "messages": messages,
                }

            # Persist the assistant turn that contained the tool calls
            messages.append(assistant_msg)
            for tc in tool_calls:
                tool_result = await self._run_tool_call(tc)
                tool_calls_made.append(tool_result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": tool_result["name"],
                    "content": json.dumps(tool_result["result"], default=str)[:8000],
                })

        # Hit the cap without a final answer
        return {
            "status": "error",
            "error": f"Agent exceeded {MAX_TOOL_ITERATIONS} tool iterations without a final answer.",
            "tool_calls": tool_calls_made,
            "messages": messages,
        }

    async def chat_stream(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        api_key: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Generator: yields {type, ...} events. Types: start, tool_call, tool_result, token, end, error.

        Tool-calling is non-streamed (we collect the whole assistant turn before deciding),
        but the FINAL assistant answer streams token-by-token.
        """
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            yield {"type": "error", "message": "No DEEPSEEK_API_KEY configured."}
            return

        yield {"type": "start", "agent": self.name}

        messages = self._build_messages(user_message, history or [])

        for iteration in range(MAX_TOOL_ITERATIONS):
            resp = await self._call_llm(messages, api_key)
            if resp.get("status") == "error":
                yield {"type": "error", "message": resp.get("error", "LLM call failed")}
                return
            assistant_msg = resp["choice"].get("message") or {}
            tool_calls = assistant_msg.get("tool_calls") or []

            if not tool_calls:
                # Final answer — stream it (we have the whole text but emit it in chunks
                # so the UI feels live without an extra round-trip to the streaming endpoint).
                final_text = assistant_msg.get("content") or ""
                for chunk in _chunks(final_text, 80):
                    yield {"type": "token", "content": chunk}
                yield {"type": "end", "iterations": iteration + 1}
                return

            messages.append(assistant_msg)
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                yield {
                    "type": "tool_call",
                    "tool": fn.get("name"),
                    "args_preview": (fn.get("arguments") or "")[:200],
                }
                tool_result = await self._run_tool_call(tc)
                yield {
                    "type": "tool_result",
                    "tool": tool_result["name"],
                    "ok": tool_result.get("ok", True),
                    "summary": _summarize_result(tool_result["result"])[:400],
                }
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": tool_result["name"],
                    "content": json.dumps(tool_result["result"], default=str)[:8000],
                })

        yield {"type": "error", "message": f"Hit {MAX_TOOL_ITERATIONS}-iteration cap."}

    # ── Internals ─────────────────────────────────────────────────────────
    def _build_messages(self, user_message: str, history: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        for turn in (history or [])[-MAX_HISTORY_TURNS:]:
            role = (turn.get("role") or "user").lower()
            if role not in ("user", "assistant"):
                continue
            content = (turn.get("content") or "")[:8000]
            if not content:
                continue
            msgs.append({"role": role, "content": content})
        msgs.append({"role": "user", "content": user_message})
        return msgs

    async def _call_llm(self, messages: List[Dict[str, Any]], api_key: str) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        tools = self.tool_definitions()
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    DEEPSEEK_API_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                if r.status_code >= 400:
                    return {"status": "error", "error": f"DeepSeek HTTP {r.status_code}: {r.text[:300]}"}
                data = r.json()
                choice = (data.get("choices") or [{}])[0]
                return {"status": "success", "choice": choice, "raw": data}
        except httpx.TimeoutException:
            return {"status": "error", "error": "LLM call timed out (120s)."}
        except Exception as e:
            return {"status": "error", "error": f"LLM call failed: {e}"}

    async def _run_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        fn = tool_call.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            return {"name": name, "ok": False, "result": {"status": "error", "error": f"Invalid JSON args: {raw_args[:200]}"}}

        if name not in BLOCK_REGISTRY:
            return {"name": name, "ok": False, "result": {"status": "error", "error": f"Unknown block: {name}"}}
        if name not in self.allowed_blocks:
            return {"name": name, "ok": False, "result": {"status": "error", "error": f"Block '{name}' not in agent's allowed_blocks."}}

        instance = block_instances.get(name) or _create_block_instance(name)
        block_input = args.get("input")
        block_params = args.get("params") or {}
        try:
            result = await instance.execute(block_input, block_params)
            return {"name": name, "ok": True, "result": result}
        except Exception as e:
            return {"name": name, "ok": False, "result": {"status": "error", "error": str(e)}}


# ── Loader ────────────────────────────────────────────────────────────────
AGENT_REGISTRY: Dict[str, Agent] = {}


def load_agents(configs_dir: Optional[Path] = None) -> Dict[str, Agent]:
    """Load every `.md` config under `configs_dir` into AGENT_REGISTRY (replaces existing)."""
    configs_dir = configs_dir or CONFIGS_DIR
    AGENT_REGISTRY.clear()
    if not configs_dir.exists():
        return AGENT_REGISTRY
    for md in sorted(configs_dir.glob("*.md")):
        try:
            agent = _parse_agent_file(md)
            AGENT_REGISTRY[agent.name] = agent
        except Exception as e:
            print(f"⚠ failed to load agent {md.name}: {e}")
    return AGENT_REGISTRY


def get_agent(name: str) -> Optional[Agent]:
    return AGENT_REGISTRY.get(name)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_agent_file(path: Path) -> Agent:
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"missing YAML frontmatter in {path}")
    frontmatter, body = m.group(1), m.group(2).strip()

    # Lightweight YAML parsing — we don't import PyYAML to keep deps minimal.
    # Supports: key: value scalars, and `key:` followed by `  - item` lists.
    config: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") or line.startswith("\t- "):
            if current_list_key:
                config[current_list_key].append(line.strip()[2:].strip().strip("\"'"))
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                config[key] = []
                current_list_key = key
            else:
                config[key] = value.strip("\"'")
                current_list_key = None
    name = config.get("name") or path.stem
    if not body:
        raise ValueError(f"empty system prompt in {path}")
    return Agent(
        name=name,
        description=config.get("description", ""),
        system_prompt=body,
        allowed_blocks=list(config.get("allowed_blocks") or []),
        model=config.get("model") or DEEPSEEK_DEFAULT_MODEL,
        temperature=float(config.get("temperature", 0.3)),
        max_tokens=int(config.get("max_tokens", 2048)),
        icon=config.get("icon", "🤖"),
    )


def _chunks(text: str, n: int) -> List[str]:
    return [text[i:i + n] for i in range(0, len(text), n)]


def _summarize_result(result: Any) -> str:
    if isinstance(result, dict):
        if result.get("status") == "error":
            return f"error: {result.get('error', '?')}"
        keys = list(result.keys())[:6]
        return f"keys=[{', '.join(keys)}]"
    if isinstance(result, list):
        return f"list[{len(result)}]"
    return str(result)[:200]
