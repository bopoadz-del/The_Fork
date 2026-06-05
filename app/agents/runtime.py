"""Agent runtime — system prompt + allowed-blocks tool list + LLM tool-calling loop.

Loads declarative agent configs from `app/agents/configs/*.md` (YAML frontmatter +
markdown body for the system prompt). Each agent can call any block in its
`allowed_blocks` list as a tool. The runtime handles the back-and-forth with the
LLM: turn → optional tool call(s) → run blocks → return results → continue.

Provider: DeepSeek (`/v1/chat/completions` JSON protocol). A local-inference
adapter is wired into the chat block as a fallback; see ``app/blocks/chat.py``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Union

import httpx

from app.blocks import BLOCK_REGISTRY
from app.dependencies import block_instances, _create_block_instance


CONFIGS_DIR = Path(__file__).parent / "configs"
MAX_TOOL_ITERATIONS = 12  # hard cap so a runaway loop can't burn budget; raised to 12 for complex multi-step tasks
MAX_HISTORY_TURNS = 20
MAX_DELEGATION_DEPTH = 3  # how deep agent → agent delegation may recurse

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"

# Groq provides an OpenAI-compatible chat-completions endpoint, so the only
# things that differ from DeepSeek are the base URL, the env-var name, and the
# default model id. Tool-calling payload shape is identical.
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _llm_config() -> Dict[str, str]:
    """Pick the active LLM provider's URL + env-key + default model.

    Precedence:
      1. Explicit `LLM_PROVIDER` env var (deepseek | groq) wins.
      2. Otherwise: if `GROQ_API_KEY` is set, use Groq (free tier-friendly).
      3. Otherwise: DeepSeek (the historical default).

    A per-provider override env (`GROQ_MODEL` / `DEEPSEEK_MODEL`) lets the
    operator pin a specific model without code changes.
    """
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "groq" if os.getenv("GROQ_API_KEY") else "deepseek"
    if provider == "groq":
        return {
            "provider": "groq",
            "url": GROQ_API_URL,
            "env_key": "GROQ_API_KEY",
            "default_model": os.getenv("GROQ_MODEL", GROQ_DEFAULT_MODEL),
        }
    return {
        "provider": "deepseek",
        "url": DEEPSEEK_API_URL,
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": os.getenv("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL),
    }


# ── DeepSeek DSML tool-call markup handling ─────────────────────────────────
# deepseek-chat sometimes emits a tool call as inline text markup inside the
# message `content` (its own "DSML" token format) instead of, or in addition
# to, the structured `tool_calls` array. If the runtime only reads
# the structured field it treats the raw markup as a final answer and shows
# garbage to the user. The helpers below detect that markup, turn it into
# proper tool_call dicts, and strip any residual fragments from final answers.
#
# The pipe character DeepSeek uses is the fullwidth U+FF5C ("｜"); we also
# tolerate a plain ASCII "|" variant and missing/extra pipes. `[｜|]{0,2}`
# matches either pipe (or none) so partial/garbled markup is still handled.

# Matches the FIRST occurrence of a DSML marker in content so we can truncate
# at that point. Handles both the angle-bracket tag form and a bare token
# sequence (e.g. `｜｜DSML`), with either fullwidth U+FF5C or ASCII `|` pipes.
_DSML_MARKER_RE = re.compile(
    r"(?:<\s*[｜|]{0,3}\s*DSML|[｜|]{1,3}DSML)",
    re.IGNORECASE,
)
# A full tool_calls block: <｜｜DSML｜｜tool_calls> ... </｜｜DSML｜｜tool_calls>
_DSML_TOOLCALLS_RE = re.compile(
    r"<\s*[｜|]{0,2}\s*DSML\s*[｜|]{0,2}\s*tool_calls\s*>(.*?)"
    r"<\s*/\s*[｜|]{0,2}\s*DSML\s*[｜|]{0,2}\s*tool_calls\s*>",
    re.IGNORECASE | re.DOTALL,
)
# A single invoke block inside a tool_calls block.
_DSML_INVOKE_RE = re.compile(
    r"<\s*[｜|]{0,2}\s*DSML\s*[｜|]{0,2}\s*invoke\s+name\s*=\s*[\"']([^\"']+)[\"'][^>]*>"
    r"(.*?)"
    r"<\s*/\s*[｜|]{0,2}\s*DSML\s*[｜|]{0,2}\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
# A single parameter inside an invoke block: name + inner text value.
_DSML_PARAM_RE = re.compile(
    r"<\s*[｜|]{0,2}\s*DSML\s*[｜|]{0,2}\s*parameter\s+name\s*=\s*[\"']([^\"']+)[\"'][^>]*>"
    r"(.*?)"
    r"<\s*/\s*[｜|]{0,2}\s*DSML\s*[｜|]{0,2}\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_dsml(content: str) -> str:
    """Discard the entire DSML region from ``content`` and return only the prose before it.

    DeepSeek emits any tool-call markup AFTER any genuine prose, so we find the
    first DSML marker and throw away everything from that point to the end of
    the string — tags AND the inner parameter text.  This prevents raw parameter
    values (e.g. query strings) from leaking into a displayed final answer.

    If no DSML marker is present, returns ``content.strip()`` unchanged.
    """
    if not content:
        return ""
    if "DSML" not in content:
        return content.strip()
    m = _DSML_MARKER_RE.search(content)
    if m is None:
        return content.strip()
    return content[: m.start()].rstrip()


def _parse_dsml_tool_calls(content: str) -> tuple[str, list[dict]]:
    """Extract DeepSeek DSML tool-call markup from ``content``.

    Returns ``(cleaned_content, tool_calls)`` where ``cleaned_content`` is the
    message text with all DSML markup removed, and ``tool_calls`` is a list of
    dicts shaped exactly like the structured ``tool_calls`` field that
    ``_run_tool_call`` consumes::

        {"id": <generated>, "type": "function",
         "function": {"name": ..., "arguments": <json string>}}

    If no DSML markup is present, returns ``(content, [])`` unchanged.
    """
    if not content or "DSML" not in content:
        return (content or ""), []

    tool_calls: list[dict] = []
    counter = 0
    for block in _DSML_TOOLCALLS_RE.finditer(content):
        for inv in _DSML_INVOKE_RE.finditer(block.group(1)):
            tool_name = inv.group(1).strip()
            if not tool_name:
                continue
            args: dict[str, Any] = {}
            for param in _DSML_PARAM_RE.finditer(inv.group(2)):
                pname = param.group(1).strip()
                pvalue = param.group(2).strip()
                if pname:
                    args[pname] = pvalue
            counter += 1
            tool_calls.append({
                "id": f"dsml_{counter}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args),
                },
            })

    # Strip ALL DSML markup (including any tags outside a well-formed block).
    cleaned = _strip_dsml(content)
    return cleaned, tool_calls


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
    can_delegate: bool = False

    def tool_definitions(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Build DeepSeek-style tool definitions.

        Includes one tool per allowed block, plus synthetic tools:
        - ``remember_fact`` — always available.
        - ``search_project_documents`` — only when ``project_id`` is set.
        - ``delegate_to_agent`` — only when ``self.can_delegate``.
        """
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

        # ── synthetic tool: remember_fact (always available) ─────────────────
        tools.append({
            "type": "function",
            "function": {
                "name": "remember_fact",
                "description": "Persist a fact you should remember in future turns.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Short identifier for the fact."},
                        "value": {"type": "string", "description": "The fact value to remember."},
                    },
                    "required": ["key", "value"],
                },
            },
        })

        # ── synthetic tool: search_project_documents (project-scoped) ────────
        if project_id:
            tools.append({
                "type": "function",
                "function": {
                    "name": "search_project_documents",
                    "description": "Search inside this project's documents (including imported Drive files).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "What to search for."},
                            # Some providers (Groq/llama-3.3-70b in particular) emit numeric tool
                            # args as strings — declaring this as ["integer","string"] avoids the
                            # provider-side tool_use_failed validator rejecting the call. The
                            # Python side at _run_tool_call coerces with `top_k or 5`, so a
                            # string here works at runtime.
                            "top_k": {"type": ["integer", "string"], "description": "Max number of results (default 5)."},
                        },
                        "required": ["query"],
                    },
                },
            })

        # ── synthetic tool: generate_wbs (when construction is allowed) ──────
        # Exposed as a top-level tool with an explicit param schema so the
        # agent never has to guess the params shape. The generic `construction`
        # tool stayed advertised with "input/params" only, and the agent kept
        # emitting empty `action` fields, retrying, and eventually escaping to
        # delegate_to_agent (which hit the iteration cap). This direct tool
        # eliminates that ambiguity.
        if "construction" in self.allowed_blocks:
            tools.append({
                "type": "function",
                "function": {
                    "name": "generate_wbs",
                    "description": (
                        "Generate a CPM-validated Work Breakdown Structure / schedule. "
                        "Returns an activity list with ES/EF/LS/LF/total_float per activity, "
                        "phase tree, and assumptions. CALL ONCE — the tool is deterministic "
                        "and re-calling with the same params returns the same large result."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "brief": {
                                "type": "string",
                                "description": "Project brief / scope description (from RFP, BOD, conversation)."
                            },
                            "target_count": {
                                "type": "integer",
                                "description": "Target number of activities (default 200, clamped to [20, 1000]).",
                            },
                            "project_type": {
                                "type": "string",
                                "enum": ["data_center", "solar_plant", "wind_farm", "building", "infrastructure"],
                                "description": "Project type — determines the WBS template scaffold.",
                            },
                            "start_date": {
                                "type": "string",
                                "description": "Schedule start date in ISO format (YYYY-MM-DD). Optional — defaults to today.",
                            },
                        },
                        "required": ["brief"],
                    },
                },
            })

        # ── synthetic tool: delegate_to_agent (delegating agents only) ───────
        if self.can_delegate:
            tools.append({
                "type": "function",
                "function": {
                    "name": "delegate_to_agent",
                    "description": (
                        "Hand off a sub-task to a specialist agent and receive its answer. "
                        "Use when another agent is better suited to part of the request."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "agent_name": {"type": "string", "description": "Name of the specialist agent to delegate to."},
                            "message": {"type": "string", "description": "The task / question for that agent."},
                        },
                        "required": ["agent_name", "message"],
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
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        on_event: Optional[Callable[[str, Dict[str, Any]], Union[None, Awaitable[None]]]] = None,
        _depth: int = 0,
        _call_stack: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Single round-trip: returns {answer, tool_calls, history}.

        Optional new params (all default to today's behavior when omitted):
        - ``project_id`` — inject project facts/docs and expose document search.
        - ``conversation_id`` — load + persist conversation memory.
        - ``on_event`` — async/sync callback fired during the tool-call loop.
          Receives ``(event_name, payload)`` where event_name is one of:
            * ``"iteration"`` — ``{"n": int}`` at the top of each loop turn.
            * ``"tool_call"`` — ``{"name": str, "args": dict, "id": str}``
              fired immediately BEFORE the tool runs.
            * ``"tool_result"`` — ``{"name": str, "id": str, "ok": bool,
              "duration_ms": int, "error": str?}`` fired AFTER the tool runs.
            * ``"final"`` — ``{"answer": str}`` fired once when the agent
              produces a non-tool-call assistant message.
          The chat router uses this to emit SSE events to the browser so
          the user sees a live reasoning trace instead of a 10-second
          spinner. Callback errors are swallowed; the loop never breaks
          because of an event handler.
        - ``_depth`` / ``_call_stack`` — internal, for inter-agent delegation.
        """
        async def _emit(name: str, payload: Dict[str, Any]) -> None:
            if on_event is None:
                return
            try:
                res = on_event(name, payload)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                # Event handler must never break the agent loop.
                pass
        cfg = _llm_config()
        api_key = api_key or os.getenv(cfg["env_key"])
        if not api_key:
            return {
                "status": "error",
                "error": f"No {cfg['env_key']} configured. Set it in .env or pass via env.",
            }

        _call_stack = _call_stack or [self.name]

        effective_history = list(history or [])
        if conversation_id:
            from app.core import agent_memory
            agent_memory.get_or_create_conversation(conversation_id, self.name, project_id)
            prior = agent_memory.get_messages(conversation_id)
            prior_turns = [
                {"role": m["role"], "content": m["content"]}
                for m in prior
                if m.get("role") in ("user", "assistant")
            ]
            effective_history = prior_turns + effective_history
            # Persist the user turn up front so it survives even if the LLM
            # call errors mid-loop — otherwise the conversation history loses
            # the question and ends up inconsistent.
            agent_memory.append_message(conversation_id, "user", user_message)

        messages = self._build_messages(user_message, effective_history, project_id=project_id)
        tool_calls_made: List[Dict[str, Any]] = []

        for iteration in range(MAX_TOOL_ITERATIONS):
            await _emit("iteration", {"n": iteration + 1})
            resp = await self._call_llm(messages, api_key, project_id=project_id)
            if resp.get("status") == "error":
                return resp
            choice = resp["choice"]
            assistant_msg = choice.get("message") or {}

            tool_calls = assistant_msg.get("tool_calls") or []
            raw_content = assistant_msg.get("content") or ""

            # DeepSeek sometimes emits the tool call as inline DSML markup in
            # `content` with an empty structured `tool_calls` field. Recover it.
            if not tool_calls:
                cleaned_content, dsml_tool_calls = _parse_dsml_tool_calls(raw_content)
                if dsml_tool_calls:
                    # Treat this turn as a tool-calling turn.
                    tool_calls = dsml_tool_calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": cleaned_content,
                        "tool_calls": dsml_tool_calls,
                    }
                else:
                    # Genuine final answer — scrub any partial DSML fragments.
                    final_text = _strip_dsml(raw_content)
                    # If the entire content was DSML (nothing usable before the
                    # first marker), force one no-tools call so the model must
                    # produce a plain-text answer instead of an empty bubble.
                    if not final_text.strip():
                        forced_resp = await self._call_llm(
                            messages, api_key, project_id=project_id, with_tools=False
                        )
                        if forced_resp.get("status") == "error":
                            final_text = "I wasn't able to produce a response — please rephrase."
                        else:
                            forced_msg = forced_resp["choice"].get("message") or {}
                            final_text = _strip_dsml(forced_msg.get("content") or "")
                            if not final_text.strip():
                                final_text = "I wasn't able to produce a response — please rephrase."
                    messages.append({"role": "assistant", "content": final_text})
                    if conversation_id:
                        from app.core import agent_memory
                        # User turn was already persisted up front.
                        agent_memory.append_message(conversation_id, "assistant", final_text)
                    await _emit("final", {"answer": final_text})
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
                # Surface the tool call to the event stream BEFORE running it
                # so the UI can show "⚙️ tool_name — running…" live.
                fn = tc.get("function") or {}
                tc_name = fn.get("name") or tc.get("name") or "unknown"
                tc_args_raw = fn.get("arguments") or tc.get("arguments") or "{}"
                try:
                    tc_args = json.loads(tc_args_raw) if isinstance(tc_args_raw, str) else dict(tc_args_raw)
                except Exception:
                    tc_args = {"_raw": str(tc_args_raw)[:200]}
                await _emit("tool_call", {
                    "name": tc_name,
                    "args": tc_args,
                    "id": tc.get("id") or "",
                })
                _t0 = time.monotonic()
                tool_result = await self._run_tool_call(
                    tc,
                    api_key=api_key,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    _depth=_depth,
                    _call_stack=_call_stack,
                )
                duration_ms = int((time.monotonic() - _t0) * 1000)
                tool_calls_made.append(tool_result)
                # Determine ok/error by introspecting the tool's result payload.
                _inner = tool_result.get("result") if isinstance(tool_result, dict) else None
                ok = True
                err = None
                if isinstance(_inner, dict) and _inner.get("status") == "error":
                    ok = False
                    err = str(_inner.get("error") or "")[:200]
                await _emit("tool_result", {
                    "name": tool_result.get("name", tc_name),
                    "id": tc.get("id") or "",
                    "ok": ok,
                    "duration_ms": duration_ms,
                    **({"error": err} if err else {}),
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": tool_result["name"],
                    "content": json.dumps(tool_result["result"], default=str)[:8000],
                })

        # Hit the cap without a final answer — force one more call with tools disabled
        # so the model is required to emit a plain-text summary.
        forced_resp = await self._call_llm(messages, api_key, project_id=project_id, with_tools=False)
        if forced_resp.get("status") == "error":
            # Even the forced call failed; fall back to the original error shape.
            return {
                "status": "error",
                "error": f"Agent exceeded {MAX_TOOL_ITERATIONS} tool iterations without a final answer.",
                "tool_calls": tool_calls_made,
                "messages": messages,
            }
        forced_msg = forced_resp["choice"].get("message") or {}
        final_text = _strip_dsml(forced_msg.get("content") or "")
        messages.append({"role": "assistant", "content": final_text})
        if conversation_id:
            from app.core import agent_memory
            # User turn was already persisted up front.
            agent_memory.append_message(conversation_id, "assistant", final_text)
        return {
            "status": "success",
            "answer": final_text,
            "tool_calls": tool_calls_made,
            "iterations": MAX_TOOL_ITERATIONS,
            "messages": messages,
            "forced_final": True,
        }

    async def chat_stream(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        _depth: int = 0,
        _call_stack: Optional[List[str]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Generator: yields {type, ...} events. Types: start, tool_call, tool_result, token, end, error.

        Tool-calling is non-streamed (we collect the whole assistant turn before deciding),
        but the FINAL assistant answer streams token-by-token.
        """
        cfg = _llm_config()
        api_key = api_key or os.getenv(cfg["env_key"])
        if not api_key:
            yield {"type": "error", "message": f"No {cfg['env_key']} configured."}
            return

        _call_stack = _call_stack or [self.name]

        yield {"type": "start", "agent": self.name}

        effective_history = list(history or [])
        if conversation_id:
            from app.core import agent_memory
            agent_memory.get_or_create_conversation(conversation_id, self.name, project_id)
            prior = agent_memory.get_messages(conversation_id)
            prior_turns = [
                {"role": m["role"], "content": m["content"]}
                for m in prior
                if m.get("role") in ("user", "assistant")
            ]
            effective_history = prior_turns + effective_history
            # Persist the user turn up front so it survives a mid-loop error.
            agent_memory.append_message(conversation_id, "user", user_message)

        messages = self._build_messages(user_message, effective_history, project_id=project_id)

        for iteration in range(MAX_TOOL_ITERATIONS):
            resp = await self._call_llm(messages, api_key, project_id=project_id)
            if resp.get("status") == "error":
                yield {"type": "error", "message": resp.get("error", "LLM call failed")}
                return
            assistant_msg = resp["choice"].get("message") or {}
            tool_calls = assistant_msg.get("tool_calls") or []
            raw_content = assistant_msg.get("content") or ""

            # DeepSeek sometimes emits the tool call as inline DSML markup in
            # `content` with an empty structured `tool_calls` field. Recover it.
            if not tool_calls:
                cleaned_content, dsml_tool_calls = _parse_dsml_tool_calls(raw_content)
                if dsml_tool_calls:
                    # Treat as a tool-calling turn — do NOT stream the markup.
                    tool_calls = dsml_tool_calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": cleaned_content,
                        "tool_calls": dsml_tool_calls,
                    }
                else:
                    # Final answer — stream it (we have the whole text but emit it in chunks
                    # so the UI feels live without an extra round-trip to the streaming endpoint).
                    final_text = _strip_dsml(raw_content)
                    # If the entire content was DSML (nothing usable before the
                    # first marker), force one no-tools call so the model must
                    # produce a plain-text answer instead of an empty bubble.
                    if not final_text.strip():
                        forced_resp = await self._call_llm(
                            messages, api_key, project_id=project_id, with_tools=False
                        )
                        if forced_resp.get("status") == "error":
                            final_text = "I wasn't able to produce a response — please rephrase."
                        else:
                            forced_msg = forced_resp["choice"].get("message") or {}
                            final_text = _strip_dsml(forced_msg.get("content") or "")
                            if not final_text.strip():
                                final_text = "I wasn't able to produce a response — please rephrase."
                    for chunk in _chunks(final_text, 80):
                        yield {"type": "token", "content": chunk}
                    if conversation_id:
                        from app.core import agent_memory
                        # User turn was already persisted up front.
                        agent_memory.append_message(conversation_id, "assistant", final_text)
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
                tool_result = await self._run_tool_call(
                    tc,
                    api_key=api_key,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    _depth=_depth,
                    _call_stack=_call_stack,
                )
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

        # Hit the cap without a final answer — force one more call with tools disabled.
        forced_resp = await self._call_llm(messages, api_key, project_id=project_id, with_tools=False)
        if forced_resp.get("status") == "error":
            yield {"type": "error", "message": f"Hit {MAX_TOOL_ITERATIONS}-iteration cap."}
            return
        forced_msg = forced_resp["choice"].get("message") or {}
        final_text = _strip_dsml(forced_msg.get("content") or "")
        for chunk in _chunks(final_text, 80):
            yield {"type": "token", "content": chunk}
        if conversation_id:
            from app.core import agent_memory
            # User turn was already persisted up front.
            agent_memory.append_message(conversation_id, "assistant", final_text)
        yield {"type": "end", "iterations": MAX_TOOL_ITERATIONS, "forced_final": True}

    # ── Internals ─────────────────────────────────────────────────────────
    def _build_messages(
        self,
        user_message: str,
        history: List[Dict[str, str]],
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

        # Project context — facts + document listing — as a second system message.
        if project_id:
            try:
                from app.core.project_memory import build_project_context
                ctx = build_project_context(project_id, user_message)
            except Exception:
                ctx = ""
            if ctx:
                msgs.append({"role": "system", "content": ctx})

        # Remembered agent facts — durable across conversations, scoped to
        # this project so one project's facts never leak into another.
        try:
            from app.core import agent_memory
            facts = agent_memory.list_agent_facts(self.name, project_id)
        except Exception:
            facts = []
        if facts:
            lines = ["Known facts (you remembered):"]
            for f in facts:
                lines.append(f"- {f['key']}: {f['value']}")
            msgs.append({"role": "system", "content": "\n".join(lines)})

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

    async def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        api_key: str,
        project_id: Optional[str] = None,
        with_tools: bool = True,
    ) -> Dict[str, Any]:
        cfg = _llm_config()
        # Agent configs default to "deepseek-chat"; when the runtime is routed
        # to a different provider we remap that placeholder to the provider's
        # default model. An agent that explicitly pinned a provider-specific
        # model (e.g. "llama-3.3-70b-versatile") is left alone.
        model = self.model
        if cfg["provider"] != "deepseek" and model.startswith("deepseek-"):
            model = cfg["default_model"]
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        tools = self.tool_definitions(project_id=project_id)
        if tools and with_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    cfg["url"],
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                if r.status_code >= 400:
                    return {"status": "error", "error": f"{cfg['provider']} HTTP {r.status_code}: {r.text[:300]}"}
                data = r.json()
                choice = (data.get("choices") or [{}])[0]
                return {"status": "success", "choice": choice, "raw": data}
        except httpx.TimeoutException:
            return {"status": "error", "error": "LLM call timed out (120s)."}
        except Exception as e:
            return {"status": "error", "error": f"LLM call failed: {e}"}

    async def _run_tool_call(
        self,
        tool_call: Dict[str, Any],
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        _depth: int = 0,
        _call_stack: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        fn = tool_call.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            return {
                "name": name,
                "ok": False,
                "result": {
                    "status": "error",
                    "error": f"Invalid JSON args: {raw_args[:200]}",
                    "hint": "Re-issue the tool call with valid JSON arguments.",
                },
            }

        _call_stack = _call_stack or [self.name]

        # ── synthetic tool: delegate_to_agent ────────────────────────────────
        if name == "delegate_to_agent":
            agent_name = args.get("agent_name") or ""
            message = args.get("message") or ""
            if _depth + 1 > MAX_DELEGATION_DEPTH:
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": "delegation depth exceeded",
                        "hint": f"Maximum delegation depth ({MAX_DELEGATION_DEPTH}) reached; answer directly.",
                    },
                }
            target = get_agent(agent_name)
            if target is None:
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": f"Unknown agent: {agent_name}",
                        "hint": f"Valid agents: {', '.join(sorted(AGENT_REGISTRY.keys())) or '(none)'}.",
                    },
                }
            if agent_name in _call_stack:
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": "delegation loop detected",
                        "hint": f"Delegation loop detected: agent '{agent_name}' is already in the delegation chain; answer directly.",
                    },
                }
            sub = await target.chat(
                message,
                api_key=api_key,
                project_id=project_id,
                _depth=_depth + 1,
                _call_stack=_call_stack + [agent_name],
            )
            return {
                "name": "delegate_to_agent",
                "ok": True,
                "result": {
                    "agent": agent_name,
                    "answer": sub.get("answer"),
                    "status": sub.get("status"),
                },
            }

        # ── synthetic tool: search_project_documents ─────────────────────────
        if name == "search_project_documents":
            if not project_id:
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": "no project in scope",
                        "hint": "This tool requires a project-scoped chat.",
                    },
                }
            try:
                from app.core.doc_index import search_project_documents
            except ImportError as e:
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": f"document search unavailable: {e}",
                        "hint": "Document search is not available; proceed without it.",
                    },
                }
            query = args.get("query") or ""
            top_k = args.get("top_k")
            # Some providers ship integer args as strings ("1" vs 1). Coerce
            # so the downstream sqlite LIMIT clause doesn't choke on a str.
            try:
                top_k = int(top_k) if top_k not in (None, "") else 5
            except (TypeError, ValueError):
                top_k = 5
            results = await search_project_documents(project_id, query, top_k)
            return {
                "name": "search_project_documents",
                "ok": True,
                "result": {"results": results},
            }

        # ── synthetic tool: generate_wbs (direct construction shortcut) ──────
        # Bypasses the generic "construction" tool's input/params ambiguity by
        # giving the model a typed call: brief, target_count, project_type,
        # start_date. Maps straight to ConstructionContainer.generate_wbs().
        if name == "generate_wbs":
            if "construction" not in self.allowed_blocks:
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": "construction container not in agent's allowed_blocks",
                    },
                }
            try:
                from app.dependencies import get_block_instance
                container = get_block_instance("construction")
            except Exception as e:
                return {
                    "name": name,
                    "ok": False,
                    "result": {"status": "error", "error": f"construction unavailable: {e}"},
                }
            params = {
                "brief": args.get("brief") or "",
                "target_count": args.get("target_count", 200),
                "project_type": args.get("project_type"),
                "start_date": args.get("start_date"),
            }
            try:
                result = await container.generate_wbs({}, params)
            except Exception as e:
                return {
                    "name": name,
                    "ok": False,
                    "result": {"status": "error", "error": f"generate_wbs failed: {e}"},
                }
            # Strip the activities array down before returning to the model —
            # 300+ rows × ~30 chars each = ~10 kB which the model doesn't need
            # to re-read into its context. The full list stays in the result
            # for any caller that does (the chat router's "end" event carries
            # tool_calls metadata; the activities themselves are reachable via
            # the /v1/execute API). The model just needs: counts, summary,
            # phase tree, assumptions, and a sample of activities to cite.
            if isinstance(result, dict) and isinstance(result.get("activities"), list):
                acts = result["activities"]
                compact = dict(result)
                compact["activities_total"] = len(acts)
                compact["activities_sample"] = acts[:15]  # first 15 for reference
                # Drop the full activities array from what the model sees.
                compact.pop("activities", None)
                result = compact
            return {
                "name": "generate_wbs",
                "ok": isinstance(result, dict) and result.get("status") == "success",
                "result": result,
            }

        # ── synthetic tool: remember_fact ────────────────────────────────────
        if name == "remember_fact":
            from app.core import agent_memory
            key = args.get("key") or ""
            value = args.get("value") or ""
            agent_memory.set_agent_fact(
                self.name, key, value, conversation_id, project_id
            )
            return {
                "name": "remember_fact",
                "ok": True,
                "result": {
                    "status": "success",
                    "remembered": {key: value},
                },
            }

        if name not in BLOCK_REGISTRY:
            return {
                "name": name,
                "ok": False,
                "result": {
                    "status": "error",
                    "error": f"Unknown block: {name}",
                    "hint": "Choose a tool from the provided tool list.",
                },
            }
        if name not in self.allowed_blocks:
            return {
                "name": name,
                "ok": False,
                "result": {
                    "status": "error",
                    "error": f"Block '{name}' not in agent's allowed_blocks.",
                    "hint": "This tool is not available to you; choose another.",
                },
            }

        instance = block_instances.get(name) or _create_block_instance(name)
        block_input = args.get("input")
        block_params = args.get("params") or {}
        try:
            result = await instance.execute(block_input, block_params)
            return {"name": name, "ok": True, "result": result}
        except Exception as e:
            return {
                "name": name,
                "ok": False,
                "result": {
                    "status": "error",
                    "error": str(e),
                    "hint": "The tool failed; retry with different input or proceed without it.",
                },
            }


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
        can_delegate=str(config.get("can_delegate", "false")).strip().lower() in ("true", "1", "yes"),
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
