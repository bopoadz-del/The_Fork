import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.blocks import BLOCK_REGISTRY
from app.core.action_router import (
    best_action,
    hint_for_orchestrator_result,
    needs_planning,
)
from app.core.dynamic_reasoning import understand_intent
from app.dependencies import require_user
from app.dependencies import block_instances
from app.infra.monitoring import capture_llm_transport_failure, get_request_id

logger = logging.getLogger(__name__)

router = APIRouter()


def _report_sse_llm_failure(error_message: str, *, path: str) -> None:
    """Surface dead-tunnel LLM failures to Sentry from the SSE wrapper (not runtime internals)."""
    capture_llm_transport_failure(
        str(error_message),
        request_id=get_request_id(),
        path=path,
        provider="ollama",
    )


class ChatRequest(BaseModel):
    message: str
    model: str = "kimi-k2.6"
    stream: bool = False
    project_id: Optional[str] = None


async def _classify_intent(prompt: str) -> tuple[Optional[str], float]:
    """Run the smart_orchestrator over the user's message and return the top
    (action, confidence) classification, or (None, 0.0) on any error.

    Used by the chat router to decide whether to take the multi-step
    heavy-reasoning agent path (action is generative + confidence ≥ 0.5)
    or stay on the fast single-shot chat block path.
    """
    if not prompt or not prompt.strip():
        return None, 0.0
    try:
        if "smart_orchestrator" not in block_instances:
            block_instances["smart_orchestrator"] = (
                BLOCK_REGISTRY["smart_orchestrator"]()
            )
        orchestrator = block_instances["smart_orchestrator"]
        result = await orchestrator.process({"user_message": prompt})
        return best_action(result)
    except Exception:
        return None, 0.0


async def _stream_from_heavy_reasoning(
    user_message: str,
    project_id: Optional[str],
    user_id: Optional[str],
    history: List[Dict[str, Any]],
    session_id: str,
):
    """Run the heavy-reasoning runtime agent and yield SSE events.

    This is the multi-step generative path. The agent's tool-call loop emits
    iteration / tool_call / tool_result events via the new on_event callback
    on Agent.chat() so the UI can render a live reasoning trace. The final
    answer is then streamed to the browser in word-sized chunks so the
    existing chat UI continues to update progressively.

    On any agent error: emit one error SSE event and stop — the caller will
    see the error and can decide whether to retry the fast path.

    Tenant isolation (PR #18 security fix — Codex P1 #1).
    ----------------------------------------------------
    The agent's ``search_project_documents`` tool reads by ``project_id``
    only — if we pass an unowned ``project_id`` through, an authenticated
    user can supply any tenant's id and the agent will pull that tenant's
    indexed docs. The fast chat path already gates this via
    ``_with_project_memory`` / ``_with_doc_search``; the heavy path
    bypassed it because it goes through the agent runtime instead.

    Fix: silently drop ``project_id`` when the caller doesn't own the
    project (matches the fast path's "if you don't own it, we just don't
    inject it" behaviour rather than returning 403, so probing for a
    valid id doesn't leak an oracle).

    Cross-user conversation isolation (PR #18 security fix — Codex P1 #2).
    --------------------------------------------------------------------
    ``conversation_id=f"hr-{session_id}"`` was keyed only on
    ``session_id``, which defaults to ``"default"`` on the fast path's
    callers. Two users both on the default session would write to the
    SAME ``hr-default`` row in ``agent_memory`` and see each other's
    prior turns surfacing in replies. Fix: prefix with ``user_id`` so
    the key is per-user; ``anon`` fallback is acceptable because this
    route already requires ``auth: dict = Depends(require_user)``.
    """
    yield f"data: {json.dumps({'type': 'start', 'session_id': session_id, 'mode': 'heavy_reasoning', 'request_id': get_request_id()})}\n\n"

    # Tenant gate: drop project_id when the caller doesn't own it.
    # Looked up once here so the agent's tool calls inherit a None
    # project_id and the search_project_documents tool naturally
    # no-ops (it already early-returns on missing project_id).
    safe_project_id = project_id
    if project_id:
        try:
            from app.core import projects as projects_store
            if projects_store.get_project_accessible(project_id, user_id) is None:
                logger.warning(
                    "heavy-reasoning: user=%s does not own project=%s; "
                    "dropping project_id (matches fast-path tenant guard)",
                    user_id or "<anon>", project_id,
                )
                safe_project_id = None
        except Exception:  # noqa: BLE001
            # Lookup failure → fail closed: drop the project_id rather
            # than risk handing it to the agent on a transient store
            # error. Worst case: the user loses RAG context for one
            # request, which is preferable to a cross-tenant leak.
            logger.exception(
                "heavy-reasoning: project ownership check failed; "
                "dropping project_id (fail-closed)"
            )
            safe_project_id = None

    # Per-user conversation key so two users on the default session don't
    # alias into the same agent_memory row.
    safe_user = user_id or "anon"
    conversation_id = f"hr-{safe_user}-{session_id}" if session_id else None

    # Pipe agent events into an asyncio queue so we can yield SSE chunks as
    # they arrive instead of buffering everything until the agent returns.
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    async def on_event(name: str, payload: Dict[str, Any]) -> None:
        await queue.put((name, payload))

    async def run_agent() -> Dict[str, Any]:
        try:
            from app.agents import get_agent
            agent = get_agent("heavy-reasoning")
            if agent is None:
                return {"status": "error", "error": "heavy-reasoning agent not loaded"}
            result = await agent.chat(
                user_message=user_message,
                history=history,
                project_id=safe_project_id,
                conversation_id=conversation_id,
                on_event=on_event,
            )
            return result
        except Exception as e:
            logger.exception("heavy-reasoning agent crashed")
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}
        finally:
            await queue.put((SENTINEL, None))

    agent_task = asyncio.create_task(run_agent())

    # Drain events as they arrive, emitting SSE for each.
    while True:
        name, payload = await queue.get()
        if name is SENTINEL:
            break
        # Forward only the events the UI knows how to render.
        if name in ("iteration", "tool_call", "tool_result", "final"):
            yield f"data: {json.dumps({'type': name, **payload})}\n\n"

    result = await agent_task

    if result.get("status") == "error":
        err = result.get("error") or "Heavy reasoning failed"
        _report_sse_llm_failure(str(err), path="/v1/chat/stream")
        yield f"data: {json.dumps({'type': 'error', 'message': err, 'request_id': get_request_id()})}\n\n"
        return

    # Stream the final answer in word chunks so the UI's existing
    # progressive renderer continues to work. The 'final' event has
    # already been emitted by the agent loop above with the same answer,
    # but the UI's main bubble re-uses 'token' events to accumulate text.
    answer = result.get("answer") or ""
    if not answer.strip():
        answer = "(no answer produced)"
    for word in answer.split(" "):
        yield f"data: {json.dumps({'type': 'token', 'content': word + ' '})}\n\n"
        await asyncio.sleep(0.01)

    tools_used = sorted({
        (tc.get("name") or "unknown")
        for tc in (result.get("tool_calls") or [])
    })
    end_event = {
        "type": "end",
        "complete": True,
        "mode": "heavy_reasoning",
        "iterations": result.get("iterations", 0),
        "tools_used": tools_used,
        "request_id": get_request_id(),
    }
    yield f"data: {json.dumps(end_event)}\n\n"


# ── Predefined reasoning (Phase 1 pilot) ────────────────────────────────────
# The orchestrator's own PLAN->EXECUTE->DELIVER path for known workflows. Gated
# behind ORCHESTRATOR_PREDEFINED (default off) so the live path is unchanged
# until the pilot proves out; scoped to the schedule workflow only.
def _predefined_enabled() -> bool:
    import os
    return (os.getenv("ORCHESTRATOR_PREDEFINED") or "").lower() in ("1", "true", "yes", "on")


def _predefined_workflows() -> set:
    # Bespoke plan-builder workflows PLUS every construction-container action:
    # the reasoner picks any of ~55 container actions and run_workflow dispatches
    # it generically (container.execute) instead of falling to a short chat
    # answer. A non-construction / unknown action still isn't here, so Q&A and
    # chat keep the fast path.
    from app.core.predefined_reasoning import WORKFLOW_REGISTRY, container_action_names
    return set(WORKFLOW_REGISTRY) | container_action_names()


# Predefined actions whose required FILE param can be resolved deterministically
# from the project's uploaded documents by extension — so an NL turn ("build the
# resource histogram") reaches the calculator with the project's own schedule,
# without a per-turn LLM extraction call. Extend as more file-param actions are
# wired (as_built_deviation_report → as_built_file/design_file, etc.).
_ACTION_FILE_PARAMS: Dict[str, List[tuple]] = {
    "resource_histogram": [("schedule_file", (".xer",))],
}


def _resolve_predefined_file_params(
    action: str, project_id: Optional[str], params: Dict[str, Any]
) -> tuple:
    """Populate an action's file params from the project's uploaded documents.

    Returns ``(params, error_or_None)``. Deterministic (no LLM). Honesty rules:
      * a param the caller already supplied is never overridden;
      * NO matching file → leave the slot unset so the action returns its own
        honest "needs X" error;
      * 2+ matching files → return an honest ask-which message instead of
        silently picking one (the no-assumptions rule applies to files too).
    """
    specs = _ACTION_FILE_PARAMS.get(action)
    if not specs or not project_id:
        return params, None
    try:
        from app.core import projects as projects_store
        docs = projects_store.list_documents(project_id)
    except Exception:  # noqa: BLE001 — resolution is best-effort
        return params, None

    resolved = dict(params)
    for slot, exts in specs:
        if resolved.get(slot):
            continue  # caller-supplied value wins
        matches = [
            d for d in docs
            if (d.get("original_name") or "").lower().endswith(exts)
            and d.get("file_path")
        ]
        if not matches:
            continue  # let the action honest-error on the missing input
        if len(matches) > 1:
            names = ", ".join(sorted((d.get("original_name") or "?") for d in matches))
            return resolved, (
                f"This project has {len(matches)} Primavera schedule files ({names}). "
                f"Tell me which one to use for the {action.replace('_', ' ')} (by name), "
                f"or attach a single schedule."
            )
        resolved[slot] = matches[0]["file_path"]
    return resolved, None


# ── S4: attachment-marker parsing ────────────────────────────────────────────
# The composer inserts `[attached: NAME]` (photos: `[attached: NAME | observations: …]`)
# into the message text. Until S4 this marker was never parsed server-side, so
# "work on the attached file" had nothing to route to (PILOT_READINESS T6b).
# The parser below turns markers into concrete documents, deterministically
# (no LLM), reusing the doc→slot honesty rules from DECISIONS 2026-07-13.
_ATTACHED_MARKER_RE = re.compile(r"\[attached:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]", re.IGNORECASE)


def _parse_attached_markers(message: str) -> List[str]:
    """Extract attached-file names from a chat message, in order, de-duplicated."""
    if not message:
        return []
    seen: set = set()
    names: List[str] = []
    for m in _ATTACHED_MARKER_RE.finditer(message):
        name = m.group(1).strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _resolve_attached_documents(
    project_id: Optional[str], names: List[str]
) -> List[Dict[str, Any]]:
    """Resolve marker names to project documents. Deterministic; best-effort.

    Exact (case-insensitive) name match wins; duplicates resolve to the most
    recent upload (a re-upload is what "the attached file" means). Falls back
    to a unique substring match. Unresolvable or ambiguous names are skipped —
    the agent still sees the marker text and honest-errors/asks on its own;
    the router never guesses.
    """
    if not project_id or not names:
        return []
    try:
        from app.core import projects as projects_store
        docs = projects_store.list_documents(project_id) or []
    except Exception:  # noqa: BLE001 — resolution is best-effort
        return []

    resolved: List[Dict[str, Any]] = []
    for name in names:
        needle = name.strip().lower()
        exact = [d for d in docs if (d.get("original_name") or "").strip().lower() == needle]
        if exact:
            resolved.append(exact[-1])
            continue
        partial = [
            d for d in docs if needle in (d.get("original_name") or "").strip().lower()
        ]
        if len(partial) == 1:
            resolved.append(partial[0])
    # De-dup by id, preserve order.
    out: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for d in resolved:
        did = d.get("id")
        if did and did not in seen_ids:
            seen_ids.add(did)
            out.append(d)
    return out


async def _stream_from_pinned_agent(
    agent_name: str,
    prompt: str,
    project_id: Optional[str],
    user_id: Optional[str],
    history: List[Any],
    conversation_id: Optional[str],
    attached_documents: Optional[List[Dict[str, Any]]] = None,
):
    """Run a user-PINNED agent (the / picker) directly. Bypasses the predefined
    shortcut and the smart-orchestrator auto-override — the user's explicit
    choice wins. Same tenant gate + SSE shape as the auto path. Yields SSE
    strings. Never raises (a bad agent name / stream error becomes an event)."""
    rid = get_request_id()
    from app.agents import get_agent
    from app.core import projects as projects_store

    agent = get_agent(agent_name)
    if agent is None:
        yield f"data: {json.dumps({'type': 'error', 'message': f'Unknown agent: {agent_name}', 'request_id': rid})}\n\n"
        return

    # Tenant gate — identical to the auto path: drop an unowned project_id,
    # resolve the public master-corpus alias to its source project.
    pin_pid = project_id
    if project_id and project_id != projects_store.MASTER_CORPUS_PROJECT_ID:
        try:
            if projects_store.get_project_accessible(project_id, user_id) is None:
                pin_pid = None
        except Exception:  # noqa: BLE001
            pin_pid = None
    if pin_pid == projects_store.MASTER_CORPUS_PROJECT_ID:
        pin_pid = projects_store.MASTER_CORPUS_SOURCE_PROJECT_ID

    routing = {
        "requested": agent.name, "final": agent.name,
        "action": None, "confidence": 1.0, "reason": "user_pinned",
    }
    yield f"data: {json.dumps({'type': 'route', **routing})}\n\n"
    try:
        async for evt in agent.chat_stream(
            prompt, history=history, project_id=pin_pid,
            conversation_id=conversation_id, user_id=user_id,
            attached_documents=attached_documents,
        ):
            yield f"data: {json.dumps(evt, default=str)}\n\n"
            await asyncio.sleep(0)
    except Exception as e:  # noqa: BLE001
        logger.warning("pinned agent stream failed: %s", e)
        _report_sse_llm_failure(str(e), path="/v1/chat/stream")
        yield f"data: {json.dumps({'type': 'error', 'message': 'The assistant is temporarily unavailable. Please try again.', 'request_id': rid})}\n\n"


async def _stream_from_predefined(
    action: str,
    user_message: str,
    project_id: Optional[str],
    user_id: Optional[str],
    session_id: str,
    document_ids: List[str],
    params: Optional[Dict[str, Any]] = None,
    deliverable: Optional[bool] = None,
    emit_start: bool = True,
):
    """Run the orchestrator's predefined reasoning for a known workflow and
    yield SSE events (same shape as the heavy path). Intent-gated: a question
    streams an inline answer; a 'produce' request also carries an export offer
    pointing at the render endpoint. ``emit_start`` is False when the caller
    (the orchestrator front door) already emitted the ``start`` event."""
    rid = get_request_id()
    if emit_start:
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id, 'mode': 'predefined', 'request_id': rid})}\n\n"

    # Tenant gate — same as the heavy path: drop project_id when not owned.
    safe_project_id = project_id
    project_name = None
    project_location = None
    if project_id:
        try:
            from app.core import projects as projects_store
            proj = projects_store.get_project_accessible(project_id, user_id)
            if proj is None:
                safe_project_id = None
            else:
                project_name = proj.get("name")
                project_location = proj.get("location")
        except Exception:  # noqa: BLE001
            logger.exception("predefined: ownership check failed; dropping project_id")
            safe_project_id = None

    context = {
        "message": user_message,
        "project_id": safe_project_id,
        "project_name": project_name or "Project",
        "document_ids": document_ids if safe_project_id else [],
        "params": {k: v for k, v in (params or {}).items() if v is not None},
    }
    if deliverable is not None:
        context["deliverable"] = deliverable   # dynamic UNDERSTAND verdict wins

    # Deterministically resolve an action's file params from the project's own
    # uploaded documents (e.g. resource_histogram → the project's .xer). An
    # honest ask-which is streamed as the answer when the project holds >1
    # candidate — never silently pick one.
    resolved_params, param_error = _resolve_predefined_file_params(
        action, safe_project_id, context["params"])
    if param_error:
        for word in param_error.split(" "):
            yield f"data: {json.dumps({'type': 'token', 'content': word + ' '})}\n\n"
            await asyncio.sleep(0.01)
        yield f"data: {json.dumps({'type': 'end', 'complete': True, 'mode': 'predefined', 'workflow': action, 'plan_steps': [], 'exports': [], 'request_id': rid})}\n\n"
        return
    context["params"] = resolved_params

    # Fill scalar params from the project's CONFIRMED metadata (never from the
    # chat message). daily_site_report's weather location comes from here (F4);
    # unset → the action's honest "no location" path, never a scraped/fabricated
    # city. A caller-supplied location always wins.
    if (action == "daily_site_report" and safe_project_id
            and project_location and not context["params"].get("location")):
        context["params"]["location"] = project_location

    try:
        from app.core.predefined_reasoning import run_workflow
        from app.schemas.project_session import ProjectSession
        session = ProjectSession.new(f"wf-{user_id or 'anon'}-{session_id}",
                                     user_id=user_id or "system")
        out = await run_workflow(action, context, session)
    except Exception as e:  # noqa: BLE001
        logger.exception("predefined reasoning crashed")
        _report_sse_llm_failure(str(e), path="/v1/chat/stream")
        yield f"data: {json.dumps({'type': 'error', 'message': 'The assistant is temporarily unavailable. Please try again.', 'request_id': rid})}\n\n"
        return

    if not out.get("handled"):
        yield f"data: {json.dumps({'type': 'error', 'message': 'workflow not handled', 'request_id': rid})}\n\n"
        return

    answer = out.get("answer") or "(no answer produced)"
    for word in answer.split(" "):
        yield f"data: {json.dumps({'type': 'token', 'content': word + ' '})}\n\n"
        await asyncio.sleep(0.01)

    yield f"data: {json.dumps({'type': 'end', 'complete': True, 'mode': 'predefined', 'workflow': action, 'plan_steps': out.get('plan_steps', []), 'exports': out.get('exports', []), 'request_id': rid})}\n\n"


async def _with_domain_hint(prompt: str) -> str:
    """Prepend a smart-orchestrator domain hint when the user's message matches
    a known intent above the confidence threshold.

    The chat block doesn't expose a system-message channel, so the hint piggy-
    backs on the user message as a leading bracketed instruction. The LLM
    treats it as scope-setting context.

    No-ops on any orchestrator error so the chat path stays robust.
    """
    try:
        if "smart_orchestrator" not in block_instances:
            block_instances["smart_orchestrator"] = (
                BLOCK_REGISTRY["smart_orchestrator"]()
            )
        orchestrator = block_instances["smart_orchestrator"]
        result = await orchestrator.process({"user_message": prompt})
        hint = hint_for_orchestrator_result(result)
        if hint:
            return f"[Context for your answer: {hint}]\n\n{prompt}"
    except Exception:
        pass
    return prompt


def _with_project_memory(
    prompt: str, project_id: Optional[str], user_id: Optional[str]
) -> str:
    """Prepend a project's accumulated facts to the prompt (Roadmap V2 · Epic 3/4).

    So a question can be answered from project memory without re-attaching the
    source document. No-op when the chat is not scoped to a project, and — for
    tenant isolation — also a no-op when the caller does not own the project,
    so a caller cannot read another tenant's project memory by guessing an id.
    """
    if not project_id:
        return prompt
    try:
        from app.core import projects as projects_store
        if projects_store.get_project_accessible(project_id, user_id) is None:
            return prompt  # not the caller's project — do not inject its memory
        from app.core.project_memory import build_memory_context
        ctx = build_memory_context(project_id, prompt)
        if ctx:
            return f"{ctx}\n\n---\n\n{prompt}"
    except Exception:
        pass
    return prompt


async def _with_doc_search(
    prompt: str, project_id: Optional[str], user_id: Optional[str], top_k: int = 5,
) -> tuple[str, list]:
    """Prepend top-k relevant document snippets from the project's zvec index.

    This is the upload→index→chat connection: when the user has a project
    selected, every uploaded file goes into doc_index (via the project
    documents endpoint, which schedules zvec indexing). Here we query that
    index with the user's question and prepend the matching snippets so the
    LLM can answer FROM the actual file content — not just from memory of
    the last upload.

    Same shape as project_reasoner.process() uses for its RELEVANT DOCUMENT
    EXCERPTS section — kept consistent so both paths reason from the same
    grounded data.

    Returns ``(augmented_prompt, raw_snippets)``. The raw snippets are the
    grounding evidence the caller feeds to the cost-grounding gate so a
    cost/rate answer is refused unless its figures trace to a rate-semantic
    retrieved chunk (parity with the agent path). ``raw_snippets`` is ``[]``
    on every no-op / degrade branch.

    No-op when project_id missing, when caller doesn't own the project, or
    when the project has no indexed documents yet.
    """
    if not project_id or not prompt.strip():
        return prompt, []
    try:
        from app.core import projects as projects_store
        if projects_store.get_project_accessible(project_id, user_id) is None:
            return prompt, []  # tenant isolation: don't search another user's docs
        from app.core.doc_index import search_project_documents
        snippets = await search_project_documents(project_id, prompt, top_k=top_k)
        if not snippets:
            return prompt, []
        # Cap each snippet to keep the prompt bounded. The reasoner uses 800.
        MAX_SNIPPET_CHARS = 800
        lines = []
        for i, s in enumerate(snippets, start=1):
            filename = s.get("filename") or s.get("document_id") or f"doc-{i}"
            text = (s.get("snippet") or "").strip()
            if not text:
                continue
            if len(text) > MAX_SNIPPET_CHARS:
                text = text[:MAX_SNIPPET_CHARS].rstrip() + "..."
            lines.append(f"[{i}] {filename}\n{text}")
        if not lines:
            return prompt, []
        excerpts_block = (
            "RELEVANT DOCUMENT EXCERPTS (from this project's indexed files; "
            "use as evidence when answering):\n" + "\n\n".join(lines)
        )
        return f"{excerpts_block}\n\n---\n\n{prompt}", snippets
    except Exception:
        # Degrade silently — chat must never break because indexing has a hiccup.
        return prompt, []


@router.post("/chat")
async def chat(request: ChatRequest, auth: dict = Depends(require_user)):
    """Simple chat endpoint."""
    if "chat" not in BLOCK_REGISTRY:
        raise HTTPException(500, "Chat block not available")

    try:
        if "chat" not in block_instances:
            block_instances["chat"] = BLOCK_REGISTRY["chat"]()

        block = block_instances["chat"]
        with_memory = _with_project_memory(
            request.message, request.project_id, auth["user_id"]
        )
        # The prefix project memory prepended (if any) is grounding evidence too:
        # a rate carried in project memory must be able to ground a cost answer.
        memory_ctx = (
            with_memory[: -len(request.message)]
            if with_memory.endswith(request.message)
            else ""
        )
        # Search the project's zvec index for snippets relevant to the user's
        # question — uploaded files become reachable here. The raw snippets are
        # kept as grounding evidence for the cost-grounding gate below.
        message, doc_snippets = await _with_doc_search(
            with_memory, request.project_id, auth["user_id"]
        )
        message = await _with_domain_hint(message)
        result = await block.execute(message, {
            "model": request.model,
            "stream": False,
        })

        # Report the ACTUAL provider/model that served the turn, not the
        # request's placeholder default ("kimi-k2.6"). The placeholder is
        # remapped onto the active provider's model inside the chat block, so
        # echoing request.model misreported Ollama responses as DeepSeek.
        inner = result.get("result", {}) if isinstance(result, dict) else {}
        answer = inner.get("text", "")
        # Cost-grounding gate (§3.2 coverage): refuse an ungrounded cost/rate
        # figure on this path exactly as the agent path does. Non-cost answers
        # pass through untouched; the helper never raises. The retrieved doc
        # snippets are classified per-chunk for rate-semantics; project-memory
        # facts are treated as authoritative project evidence.
        try:
            from app.agents.runtime import (
                gate_cost_answer,
                format_excerpts_as_rag_context,
            )
            answer = gate_cost_answer(
                answer,
                rag_context=format_excerpts_as_rag_context(doc_snippets),
                authoritative_texts=[memory_ctx] if memory_ctx.strip() else None,
                user_message=request.message,
            )
        except Exception:
            logger.exception("chat: cost-grounding gate failed; passing answer through")
        return {
            "text": answer,
            "model": inner.get("model") or request.model,
            "provider": inner.get("provider"),
        }

    except HTTPException:
        raise
    except Exception:
        # Do not leak internal exception detail to the client.
        raise HTTPException(500, "Chat failed")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, auth: dict = Depends(require_user)):
    """Streaming chat endpoint.

    COST-GROUNDING LIMITATION (§3.2): tokens are emitted to the client as the
    LLM produces them, so there is no complete answer to inspect before the
    first cost figure has already left the server — the cost-grounding gate
    (which must see the WHOLE answer to decide) cannot be applied here without
    defeating streaming. The gate IS enforced on the non-streaming ``/chat``
    path above; the agent path solves this by disabling token streaming for
    cost-shaped queries (see runtime ``_is_cost_shaped_query``) rather than
    gating mid-stream. This simple ``/chat/stream`` block has no RAG/project
    grounding wired in, so it is not a grounded cost-answer path; a client that
    needs grounded, gated cost answers must use ``/chat`` (non-streaming) or the
    agent path. Documented rather than gated to preserve streaming behaviour."""
    if "chat" not in BLOCK_REGISTRY:
        raise HTTPException(500, "Chat block not available")

    async def event_stream():
        rid = get_request_id()
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
                        try:
                            err_payload = json.loads(token)
                            _report_sse_llm_failure(
                                err_payload.get("message") or token,
                                path="/chat/stream",
                            )
                        except json.JSONDecodeError:
                            _report_sse_llm_failure(token, path="/chat/stream")
                        yield f"data: {token}\n\n"
                        return
                    yield f"data: {json.dumps({'type': 'token', 'content': token, 'request_id': rid})}\n\n"
            else:
                # Fallback: simulate streaming
                text = result.get("result", {}).get("text", "")
                words = text.split()
                for word in words:
                    yield f"data: {json.dumps({'type': 'token', 'content': word + ' ', 'request_id': rid})}\n\n"
                    await asyncio.sleep(0.05)

            yield f"data: {json.dumps({'type': 'end', 'complete': True, 'request_id': rid})}\n\n"

        except Exception as e:
            _report_sse_llm_failure(str(e), path="/chat/stream")
            # Keep the real error in the server report above; never stream raw
            # exception text (it can carry infra detail like the LLM tunnel URL).
            yield f"data: {json.dumps({'type': 'error', 'message': 'The assistant is temporarily unavailable. Please try again.', 'request_id': rid})}\n\n"

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
async def chat_v1(request: ChatRequest, auth: dict = Depends(require_user)):
    """Simple chat endpoint (v1 API)."""
    return await chat(request, auth)


@router.post("/v1/chat/stream")
async def chat_stream_v1(request: Request, auth: dict = Depends(require_user)):
    """Streaming chat endpoint (v1 API) with flexible JSON body."""
    if "chat" not in BLOCK_REGISTRY:
        raise HTTPException(500, "Chat block not available")

    try:
        body = await request.json()
    except Exception:
        body = {}

    prompt = body.get("prompt", body.get("message", ""))
    model = body.get("model", body.get("provider", "kimi-k2.6"))
    session_id = body.get("session_id", "default")
    history = body.get("history", []) or []
    project_id = body.get("project_id")
    # ── / agent-picker: the user can pin a specific agent for this turn. A
    # pinned agent (anything other than unset / "auto") bypasses BOTH the
    # predefined shortcut AND the smart-orchestrator auto-override — the user's
    # explicit choice wins. "auto"/unset = today's automatic routing, unchanged.
    pinned_agent_name = (body.get("agent") or "").strip()

    # ── Intent classification: route generative multi-step intents to the
    # heavy-reasoning agent; everything else stays on the fast chat path.
    # The classifier reads the RAW prompt (no history flatten, no prepends)
    # so the orchestrator's keyword matcher sees the user's actual words.
    #
    # Routing does NOT require a project — "create a 300-activity schedule"
    # needs the agent's tools (construction.generate_wbs, formula_executor_v2)
    # whether or not there's a project_id. Gating on project_id meant the
    # most common generative requests fell through to the fast single-shot
    # path, where the LLM invented manpower histograms and refused to build
    # schedules. File context still reaches the agent via the prompt itself
    # (the frontend prepends sessionFileContexts), and the agent's own RAG
    # tool gracefully no-ops when project_id is None.
    # ── Orchestrator front door ────────────────────────────────────────────
    # ONE entry point: emit `start` immediately (no pre-stream blocking, so the
    # gateway never 502s waiting), then decide INSIDE the stream. A known
    # workflow runs predefined reasoning; everything else dispatches to the
    # project-assistant agent, which itself routes generative turns to
    # heavy-reasoning (select_agent_for_message). The agent path preserves the
    # existing document-Q&A behavior exactly.
    from app.agents import get_agent
    from app.agents.runtime import select_agent_for_message
    from app.core import projects as projects_store
    from app.routers.agents import _enforce_conversation_access

    conversation_id = body.get("conversation_id")
    user_id = auth.get("user_id")

    # Authoritative conversation-access gate (raises 404) BEFORE streaming, so a
    # caller cannot stream into a victim's ws-{pid} conversation.
    if conversation_id is not None:
        _enforce_conversation_access(conversation_id, auth)

    # ── S4: resolve the composer's `[attached: X]` marker(s) server-side.
    # Gated on project OWNERSHIP first — marker resolution lists the project's
    # documents, so it must never run against a project this caller doesn't
    # own (the master-corpus alias is public-read but takes no attachments).
    owned_pid = None
    if project_id and project_id != projects_store.MASTER_CORPUS_PROJECT_ID:
        try:
            if projects_store.get_project_accessible(project_id, user_id) is not None:
                owned_pid = project_id
        except Exception:  # noqa: BLE001
            owned_pid = None
    attached_docs = _resolve_attached_documents(
        owned_pid, _parse_attached_markers(prompt)
    )
    # Explicit client-sent document_ids win; marker resolution fills the gap
    # for clients (and history replays) that only carry the inline marker.
    document_ids: List[str] = list(body.get("document_ids") or [])
    for _d in attached_docs:
        if _d.get("id") and _d["id"] not in document_ids:
            document_ids.append(_d["id"])

    async def event_stream():
        rid = get_request_id()
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id, 'request_id': rid})}\n\n"

        # 0) Pinned agent (the / picker). A user-chosen agent wins outright:
        #    skip the predefined shortcut and the auto-router entirely and run
        #    exactly the requested agent. "auto"/unset falls through to 1) & 2).
        if pinned_agent_name and pinned_agent_name.lower() != "auto":
            async for evt in _stream_from_pinned_agent(
                agent_name=pinned_agent_name, prompt=prompt, project_id=project_id,
                user_id=user_id, history=history, conversation_id=conversation_id,
                attached_documents=attached_docs,
            ):
                yield evt
            return

        # 1) Predefined reasoning for a known workflow (flagged; dynamic UNDERSTAND).
        if _predefined_enabled():
            try:
                intent = await understand_intent(
                    prompt, has_documents=bool(document_ids))
            except Exception:  # noqa: BLE001
                intent = {"action": None}
            if intent.get("action") in _predefined_workflows():
                logger.info("chat -> dynamic-predefined: workflow=%s mode=%s",
                            intent.get("workflow"), intent.get("mode"))
                merged = dict(intent.get("params") or {})
                for k in ("target_count", "project_type", "start_date", "day_rate"):
                    if body.get(k) is not None:
                        merged[k] = body.get(k)
                async for evt in _stream_from_predefined(
                    action=intent["action"], user_message=prompt,
                    project_id=project_id, user_id=user_id, session_id=session_id,
                    document_ids=document_ids, params=merged,
                    deliverable=intent.get("deliverable"), emit_start=False,
                ):
                    yield evt
                return

        # 2) Otherwise: dispatch to the project-assistant agent (which self-routes
        #    generative turns to heavy-reasoning). Preserves current Q&A behavior.
        agent = get_agent("project-assistant")
        if agent is None:
            yield f"data: {json.dumps({'type': 'error', 'message': 'The assistant is temporarily unavailable. Please try again.', 'request_id': rid})}\n\n"
            return
        # Tenant gate: drop an unowned project_id (master-corpus alias is public-read).
        safe_pid = project_id
        if project_id and project_id != projects_store.MASTER_CORPUS_PROJECT_ID:
            try:
                if projects_store.get_project_accessible(project_id, user_id) is None:
                    safe_pid = None
            except Exception:  # noqa: BLE001
                safe_pid = None
        resolved_pid = safe_pid
        if safe_pid == projects_store.MASTER_CORPUS_PROJECT_ID:
            resolved_pid = projects_store.MASTER_CORPUS_SOURCE_PROJECT_ID
        try:
            agent, routing = await select_agent_for_message(prompt, agent)
            yield f"data: {json.dumps({'type': 'route', **routing})}\n\n"
            async for evt in agent.chat_stream(
                prompt, history=history, project_id=resolved_pid,
                conversation_id=conversation_id, user_id=user_id,
                attached_documents=attached_docs,
            ):
                yield f"data: {json.dumps(evt, default=str)}\n\n"
                await asyncio.sleep(0)
        except Exception as e:  # noqa: BLE001
            logger.warning("orchestrator agent stream failed: %s", e)
            _report_sse_llm_failure(str(e), path="/v1/chat/stream")
            yield f"data: {json.dumps({'type': 'error', 'message': 'The assistant is temporarily unavailable. Please try again.', 'request_id': rid})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
