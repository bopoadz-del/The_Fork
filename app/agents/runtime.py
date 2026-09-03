"""Agent runtime — system prompt + allowed-blocks tool list + LLM tool-calling loop.

Loads declarative agent configs from `app/agents/configs/*.md` (YAML frontmatter +
markdown body for the system prompt). Each agent can call any block in its
`allowed_blocks` list as a tool. The runtime handles the back-and-forth with the
LLM: turn → optional tool call(s) → run blocks → return results → continue.

Provider: OpenAI-compatible `/v1/chat/completions` JSON protocol (Kimi / Groq /
Ollama). A local-inference adapter is wired into the chat block as a fallback;
see ``app/blocks/chat.py``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
)

import httpx

from app.blocks import BLOCK_REGISTRY
from app.core.clash_intent import message_wants_clash
from app.core.contract_lookup_intent import message_is_contract_data_lookup
from app.core.rag.inject import rag_inject
from app.core.rag.retriever import project_is_rag_ready
from app.dependencies import _create_block_instance, block_instances

_LOG = logging.getLogger(__name__)

# Final-text fallback used whenever a forced retry returns empty content.
# Without it, the generator emits an `end` event with zero `token` events,
# which the UI renders as an empty assistant bubble (FOLLOW-UP #90).
_EMPTY_RESPONSE_FALLBACK = (
    "I was unable to generate a response for this turn. "
    "Please rephrase the question or try again."
)


_GENERATIVE_VERB_RE = re.compile(
    r"\b(generate|produce|create|draft|prepare|compose|write up|write a |write an |"
    r"build (?:a|an|me)|auto[-\s]?populate|put together|come up with)\b",
    re.IGNORECASE,
)


def _is_generative_request(text: str) -> bool:
    """A request to PRODUCE an artifact (checklist, register, RFI, plan) rather
    than look up a fact. Generative requests must not be strait-jacketed by the
    'answer ONLY from context' directive — with any related template in the
    corpus the model would otherwise refuse ("I can't generate that from the
    provided material"), which is the opposite of what the user asked for."""
    return bool(_GENERATIVE_VERB_RE.search(text or ""))


# ── Capability / self-introspection questions ────────────────────────────────
# Questions ABOUT the agent ("what tools do you have", "what can you do", "what
# documents can you access") must be answered from the agent's REAL tool roster
# and the project's REAL document list — NOT from retrieved document text. The
# grounded-RAG clamp (_apply_rag_context: "answer ONLY from the reference
# context") otherwise makes the agent answer a question about ITSELF using
# whatever documents happened to match the word "tools" (e.g. construction
# power-tool specs), which is confidently wrong. This is a deterministic
# short-circuit, mirroring the rag-miss fast path.
_CAPABILITY_META_PHRASES = (
    "what can you do", "what do you do", "who are you", "what are you",
    "what are your capabilities", "what are your abilities",
    "how can you help", "what can you help", "what help can you",
    "what tools do you", "what tools can you", "what tools you",
    "which tools do you", "which tools can you", "list your tools",
    "what functions do you", "what features do you",
    "what documents can you access", "what files can you access",
    "what can you access", "what documents can you see",
    "what can you work on", "what documents can you work",
)


def _first_sentence(text: str, cap: int = 160) -> str:
    """First sentence (or first ``cap`` chars) of a description, single-lined."""
    s = " ".join((text or "").split())
    if not s:
        return ""
    head = s.split(". ")[0].rstrip(".")
    return head if len(head) <= cap else head[:cap].rstrip() + "…"


def _is_capability_request(text: str) -> bool:
    """True when the user is asking about the AGENT itself (its tools, abilities,
    or what documents it can access) — not about the content of a document."""
    t = " ".join((text or "").lower().split())
    if not t:
        return False
    # An INSTRUCTION that prohibits or constrains tool use is work, not a
    # question about the agent. Live find 2026-08-15 (F22): "do not run any
    # schedule, workbook, or generator tool -- write the document text"
    # contained "you"/"tool" and short-circuited a costing turn into the
    # agent's tool roster. Prohibition phrasing wins over the co-occurrence
    # heuristic below.
    if re.search(
        r"\b(?:do\s+not|don'?t|never|without|no)\s+"
        r"(?:run|use|call|invoke)?\s*(?:any\s+)?(?:\w+[ ,]+){0,4}tool",
        t,
    ) or re.search(r"\brun\s+no\s+tool", t):
        return False
    if any(p in t for p in _CAPABILITY_META_PHRASES):
        return True
    # Generic: self-reference ("you"/"your") + a capability keyword, unless the
    # phrasing is clearly asking what a DOCUMENT states (a real RAG lookup).
    if re.search(r"\byou(r)?\b", t) and any(
        k in t for k in ("tool", "capabilit", "able to", "function", "feature")
    ):
        if not any(
            c in t for c in (
                "does the", "according to", "per the", "spec say",
                "document say", "drawing say", "as per",
            )
        ):
            return True
    return False


def _build_capability_answer(agent: Agent, project_id: str | None) -> str:
    """Deterministic answer describing the agent's REAL tools + the project's
    REAL documents. No LLM, no RAG — cannot fabricate or answer the wrong thing."""
    lines = [f"I'm the **{agent.name}** agent."]
    try:
        defs = agent.tool_definitions(project_id)
    except Exception:
        defs = []
    tool_lines = []
    for d in defs or []:
        if not isinstance(d, dict):
            continue
        fn = d.get("function") if isinstance(d.get("function"), dict) else d
        name = fn.get("name")
        tdesc = fn.get("description", "")
        if name:
            tool_lines.append(f"- **{name}** — {_first_sentence(tdesc)}")
    if tool_lines:
        lines.append("\n**Tools I can use:**\n" + "\n".join(tool_lines))
    if project_id:
        try:
            from app.core import projects as _store
            n = _store.count_documents(project_id)
        except Exception:
            n = 0
        if n:
            docs = _store.list_documents(project_id, limit=15, newest_first=True)
            listing = "\n".join(f"- {d['original_name']}" for d in docs)
            more = n - len(docs)
            tail = f"\n- …and {more} more (ask me to search for a specific one)." if more > 0 else ""
            lines.append(
                f"\n**Documents in this project ({n} total):**\n{listing}{tail}"
            )
        else:
            lines.append(
                "\nThis project has **no documents** uploaded yet, so there's "
                "nothing here for me to read. Upload files or pick a project "
                "that has documents."
            )
    return "\n".join(lines)


def _apply_rag_context(
    messages: list, rag_sys_msg: dict, user_data_authoritative: bool = False
) -> bool:
    """Fold retrieved RAG context INTO the final user turn rather than adding a
    separate system message.

    Models (esp. gpt-oss) de-prioritize system messages: with the context in a
    system block the model cites the doc-ids but still answers from its (often
    wrong) parametric prior — observed live on a FIDIC question where the answer
    was byte-identical whether the context was the correct KB note or unrelated
    templates. Embedding it in the user turn makes the model actually use it.

    The directive is INTENT-AWARE: a lookup/factual turn is grounded strictly
    (answer only from context, don't invent); a GENERATE turn treats the context
    as optional background and lets the model apply domain expertise to produce
    the artifact, while still not fabricating project-specific facts. Returns
    True if applied.
    """
    content = (rag_sys_msg or {}).get("content")
    if not content:
        return False
    if messages and messages[-1].get("role") == "user":
        question = messages[-1].get("content", "")
        if _looks_like_self_contained_calculation(question):
            # ARITHMETIC, not a lookup. Every input is IN the question, so the
            # strict-grounding directive below ("answer using ONLY the
            # reference context ... if it does not contain the answer, say you
            # don't have it") actively forbids the right answer.
            #
            # Live 2026-08-03, verbatim:
            #   "Calculate the concrete volume for a raft 25m x 18m x 1.2m
            #    thick and the number of 6m3 truck loads"
            #   -> "I cannot find that specific information in the provided
            #       reference context ... they do not contain a raft measuring
            #       25 m x 18 m x 1.2 m thick"
            #
            # 25 x 18 x 1.2 = 540 m3; 540 / 6 = 90 loads. The model was not
            # failing — it was obeying. It is the DIRECTIVE that has to change.
            #
            # (tool_choice forcing cannot rescue this: _tool_choice_for returns
            # "auto" for kimi because K2 rejects a specified tool outright, so
            # the deterministic calculators can never be forced. This path is
            # provider-independent by design.)
            #
            # Project-specific facts stay protected: the model may compute from
            # the numbers the USER supplied, not invent project data.
            directive = (
                "\n\n----- END OF REFERENCE CONTEXT -----\n\n"
                "The request below is a CALCULATION whose inputs are supplied "
                "IN THE REQUEST ITSELF. Compute it using standard construction "
                "and engineering formulas. Do NOT refuse because the "
                "dimensions, loads or quantities are absent from the reference "
                "context — they are not supposed to be there; they came from "
                "the user. Treat the context as background only.\n\n"
                "Show the formula, the substitution with the user's numbers, "
                "and the result with correct units. Round sensibly and state "
                "any assumption you make. You may still cite the context for "
                "project-specific rates, specified thicknesses or standards "
                "where it genuinely applies — but never invent "
                "project-specific facts (names, drawing or clause references) "
                "that are not in the context.\n\nCALCULATION REQUEST: "
            )
        elif _is_generative_request(question):
            directive = (
                "\n\n----- END OF REFERENCE CONTEXT -----\n\n"
                "The reference context above is background you MAY draw on. This "
                "is a REQUEST TO GENERATE — produce the complete artifact asked "
                "for, applying standard construction / engineering domain "
                "knowledge where the context is thin or absent. Do NOT fabricate "
                "project-specific facts (real names, numbers, drawing or clause "
                "references) that aren't in the context, but DO generate the "
                "standard professional content requested (checklist items, risk "
                "entries, methodology, etc.).\n\nREQUEST: "
            )
        elif user_data_authoritative:
            # Agents whose JOB is to consume figures the user supplies (the
            # learning agent recording corrections) must not be strict-grounded
            # against the retrieved corpus. Live 2026-08-15: "the actual
            # supplier invoice came to 480 SAR/m3 -- record it" was REFUSED
            # with "480 SAR/m3 does not appear in the authoritative reference
            # context". The user's own correction IS the primary source; the
            # strict directive below actively forbids the agent's purpose.
            # Same class as the self-contained-calculation carve-out above:
            # the model was obeying, the directive had to change.
            directive = (
                "\n\n----- END OF REFERENCE CONTEXT -----\n\n"
                "The message below may contain figures, outcomes or "
                "corrections SUPPLIED BY THE USER (actual costs, measured "
                "quantities, real durations). Those user-supplied figures are "
                "AUTHORITATIVE first-party data -- they are not supposed to "
                "be in the reference context, and their absence from it is "
                "never a reason to refuse. Record or act on them with your "
                "tools as your role requires, and say plainly what you "
                "recorded. Use the reference context only as background for "
                "comparison. Do not invent figures the user did not "
                "supply.\n\nMESSAGE: "
            )
        else:
            # F36: "say you don't have it" must not outrank the agent's TOOLS.
            # Live 2026-08-15: bim-analyst, asked for the element census of an
            # uploaded IFC, made ZERO tool calls and refused with "the
            # reference context does not contain any extracted data from
            # qa_building.ifc" -- obeying this directive to the letter while a
            # bim tool that answers the question sat in its roster. Retrieved
            # text is one source; the agent's extractors are another.
            directive = (
                "\n\n----- END OF REFERENCE CONTEXT -----\n\n"
                "Answer the question below using ONLY the reference context "
                "above. Reproduce its specific facts — numbers, deadlines, clause "
                "references, named principles, definitions — EXACTLY. If the "
                "context conflicts with what you think you know, the context "
                "wins. If the context does not contain the answer but one of "
                "your TOOLS can extract or compute it from the project's files "
                "(BIM/IFC extraction, BOQ parsing, drawing take-off, "
                "calculators), CALL THE TOOL and answer from its result. Only "
                "say you don't have it when neither the context nor your tools "
                "can produce the answer — never invent one.\n\nQUESTION: "
            )
        messages[-1] = {**messages[-1], "content": content + directive + question}
        return True
    # No user turn to attach to (unexpected) — fall back to a system message.
    messages.insert(max(0, len(messages) - 1), rag_sys_msg)
    return True

_UNINDEXED_PROJECT_MESSAGE = (
    "This project has no indexed documents yet, so I cannot answer "
    "questions from its corpus. Please upload a document or wait for "
    "indexing to finish."
)


# Patterns that indicate the model emitted a raw internal tool-call payload
# instead of a user-facing answer. These must never be shown to the user.
_INTERNAL_TOOL_KEYS = {"name", "arguments"}

# Fallback used when the assistant emits raw internal tool/search arguments
# instead of a user-facing answer.
_TOOL_FORMAT_FALLBACK = (
    "I hit an internal search formatting issue before I could produce a "
    "grounded answer. Please retry or narrow the question."
)

# Nudge appended before a forced no-tools retry when sanitization replaced the
# model's reply with _TOOL_FORMAT_FALLBACK (raw search/tool JSON leak).
_TOOL_FORMAT_RETRY_NUDGE = (
    "Your previous reply looked like internal tool JSON, not a user-facing answer. "
    "Write the complete deliverable now in plain text using the tool results already "
    "in this thread. Do not emit JSON tool arguments or search payloads."
)

# Live M15: the hat ended on "let me search / I'll pull the WIR" after the
# search-tool cap, which the UI rendered as an incomplete / empty generation.
# Two shapes, because live failures came in both:
#  * OPENER - the whole turn is a promise ("Let me search...", "I'm searching
#    the executed contract volume...").
#  * TAIL - the turn reasons briefly and then ENDS on the promise ("...absent
#    from the retrieved excerpts, I am running a targeted search ... to pull
#    the clause details."), which is the same dead end for the reader.
# Both require a FIRST-PERSON promise. A bare leading verb ("Search results
# show three specs...") is a real answer and must not match.
_SEARCH_PREAMBLE_RE = re.compile(
    r"^\s*(?:okay[,.]?\s+|sure[,.]?\s+|thanks[,.]?\s+|right[,.]?\s+)?"
    r"(?:let me\b|i'?ll\b|i\s+will\b|i'?m\b|i\s+am\b|i\s+need\s+to\b|"
    r"give me a moment|one moment|hold on)"
    r"[^.!?]{0,120}?"
    r"\b(?:search|searching|look(?:ing)?\s+up|pull(?:ing)?|check(?:ing)?|"
    r"run(?:ning)?|re-?run|fetch(?:ing)?|retriev(?:e|ing))\b",
    re.IGNORECASE,
)
_SEARCH_PROMISE_TAIL_RE = re.compile(
    r"(?:^|[.!?]\s+)(?:[^.!?]{0,160}?)"
    r"\b(?:i\s+am|i'?m|i\s+will|i'?ll|let me|i\s+need\s+to)\b"
    r"[^.!?]{0,160}?"
    r"\b(?:search|searching|run(?:ning)?|pull(?:ing)?|look(?:ing)?\s+up|"
    r"fetch(?:ing)?|retriev(?:e|ing)|check(?:ing)?)\b[^.!?]{0,160}[.!?]?\s*$",
    re.IGNORECASE,
)
_SEARCH_PREAMBLE_RETRY_NUDGE = (
    "Do not search again. Answer now in plain text using the operator facts "
    "and any tool results already in this thread. If the answer is a lookup, "
    "state the value with its source; if the thread does not contain it, say "
    "so plainly and name what is missing. A deliverable (WIR / inspection "
    "request, claim notice, as-built note) must include scope, the stated "
    "numbers, hold/witness or variance, and signatories. Never reply with an "
    "intention to search."
)


# Retry instruction when the model handed back its own input. Distinct from
# the search-promise nudge above: nothing is missing here -- the answer was in
# the material the model was given, and it copied the material instead of
# reading it. So the instruction is "answer from it", not "stop promising".
_CONTEXT_LEAK_RETRY_NUDGE = (
    "Your previous reply repeated the reference material you were given "
    "instead of answering. That material is CONTEXT, not output: never quote "
    "the instructions, the retrieval brief, the [doc_id=... chunk=... "
    "score=...] markers, or any [source: ...] filesystem path back to the "
    "user. Answer the question now, in your own words, in plain text, stating "
    "the value and citing the document by name only."
)


#: A line that narrates work in progress and states no result. On its own it
#: is not an answer, and after a promise it is not the end of one either --
#: it is the promise still running.
#: Applied with .match(), which anchors at position 0 on its own -- no "^".
_PROGRESS_NARRATION_RE = re.compile(
    r"(?:continuing|resuming|reading|re-?reading|fetching|retrieving|"
    r"searching|pulling|checking|loading|processing|extracting|scanning|"
    r"analy[sz]ing|looking)\b[^.!?\n]*[.!?\u2026]*\s*$",
    re.IGNORECASE,
)


def _strip_progress_narration(text: str) -> str:
    """Drop trailing 'Continuing BOQ extraction...' style lines.

    Live on 2d221e6, the build that taught the model to read a truncated
    result window by window. Asked for the m3 demolition quantity it
    answered, in full:

        "I'll continue reading the BOQ to find any cubic-metre items. Let me
         pull the next window.

         Continuing BOQ extraction..."

    _SEARCH_PROMISE_TAIL_RE matches "Let me pull the next window." on its
    own. It did not match here because it is anchored to the END of the text
    and a progress line follows it, so a dangling promise shipped as the
    answer -- the exact failure #454 and #456 exist to prevent, reached by
    new vocabulary that the platform itself taught the model.

    Narration is not an answer, so it cannot be what makes the sentence
    before it one. Strip it, then judge what is left.
    """
    lines = (text or "").rstrip().split("\n")
    while lines and (
        not lines[-1].strip() or _PROGRESS_NARRATION_RE.match(lines[-1].strip())
    ):
        lines.pop()
    return "\n".join(lines).strip()


def _looks_like_search_preamble(text: str) -> bool:
    """True when the model stopped on a search promise instead of writing."""
    t = (text or "").strip()
    if not t or len(t) > 500 or t.count("\n") > 6:
        return False
    if _SEARCH_PREAMBLE_RE.search(t) or _SEARCH_PROMISE_TAIL_RE.search(t):
        return True
    stripped = _strip_progress_narration(t)
    if not stripped:
        # Nothing but narration. "Continuing BOQ extraction..." is a status
        # line, not a reply to a question about quantities.
        return True
    if stripped == t:
        return False
    return bool(
        _SEARCH_PREAMBLE_RE.search(stripped)
        or _SEARCH_PROMISE_TAIL_RE.search(stripped)
    )


def _final_text_needs_forced_retry(
    text: str, *, user_message: str | None = None,
) -> bool:
    """True when sanitization left no usable user-facing answer."""
    if not (text or "").strip():
        return True
    if text == _TOOL_FORMAT_FALLBACK:
        return True
    # A promise to search is never a usable answer to ANY question. This was
    # gated on deliverable/generative requests, so plain factual lookups fell
    # straight through and the user was shown "Let me search more precisely
    # for the Engineer's Representative..." as the final answer (live WAVE-2
    # D1 and E1, plus three earlier turns in the same session). The request
    # type does not change whether a dangling promise is an answer.
    if _looks_like_search_preamble(text):
        return True
    return False

# Keys that strongly indicate an internal search tool argument payload.
_SEARCH_TOOL_ARG_KEYS = {
    "query",
    "project_id",
    "corpus",
    "top_k",
    "filters",
    "source",
    "tool",
    "function",
}

# Keys that indicate legitimate user-requested data JSON, not tool args.
_ANSWER_DATA_KEYS = {
    "answer",
    "result",
    "results",
    "data",
    "items",
    "rows",
    "total",
    "count",
    "status",
    "message",
    "summary",
    "explanation",
    "value",
    "values",
}


def _project_has_non_rag_context(project_id: str, user_message: str) -> bool:
    """True if there is project-specific context (facts, documents, etc.)
    outside the RAG corpus.  Allows project-fact Q&A to keep working even
    when the project has no indexed chunks yet.
    """
    try:
        from app.core.project_memory import build_project_context

        ctx = build_project_context(project_id, user_message)
        return bool(ctx and ctx.strip())
    except Exception:
        return False


# Controlled answer used when an exact construction reference is absent from
# the indexed project sources.  Replaces both the generic empty-content
# fallback and any expensive model call that would otherwise spin on a miss.
# Persisted when a turn dies before producing an answer (provider error,
# crash, iteration cap). WHY THIS EXISTS:
#
# `append_message(user)` runs BEFORE the LLM call, but the assistant message
# is appended only on the SUCCESS paths. So a failed turn persisted the
# question and nothing else. The UI's error bubble is local state only, so
# after a reload the user saw their own message with no reply — permanently,
# with no indication anything had gone wrong.
#
# Observed live 2026-08-02 in the operator's own conversation: "the client project package 1"
# sits in the thread with no assistant turn after it, and the feature sweep
# reproduced the mechanism (kimi HTTP 400 "tokenization failed" on
# document_metadata -> error event -> nothing persisted).
#
# Deliberately SHORT and unmistakably a failure notice: this text re-enters
# the model's context on the next turn, so it must not read as content or
# invite the model to continue from it.
_FAILED_TURN_MESSAGE = (
    "[This turn failed before an answer was produced. Nothing was generated. "
    "Ask again to retry.]"
)


_TOOL_RESULT_MAX_CHARS = 8000


# Empty-router dispatch enforcement (F19, phase-3 campaign). Two live gates
# proved the prose contract alone does not make K2 dispatch after an empty
# router match: the TIMING log showed iter=0 calling only smart_orchestrator
# and iter=1 going straight to final -- while the ANSWER claimed "Routed to:
# formula_executor_v2" and carried a self-computed number. Same failure on
# two consecutive gates (requests 143ac24a and 8c1c9589) despite contract
# revisions, so the enforcement moved into machinery: the runtime detects the
# empty verdict at the moment it happens and injects a corrective user-role
# message between the tool result and the next model call. In-context and
# positionally adjacent, which K2 follows far more reliably than a system
# contract paragraph.
# Tool-ERROR correction (F27, phase-4 campaign). Reproduced twice on
# bim-analyst: the first tool call errored (construction without a valid
# action) and the model NARRATED its next call as the final answer -- "I
# will try bim_extract" -- without ever making it. Same failure family as
# the fabricated-dispatch finding: prose intentions are not calls. After an
# error tool result, the runtime injects this corrective message; capped
# per turn so a hopeless tool cannot loop forever.
_TOOL_ERROR_NUDGE = (
    "That tool call FAILED -- read the error above, correct the call (tool "
    "name, action, or arguments) and MAKE the corrected call now. Do not "
    "describe a call you intend to make; make it. If no correction can "
    "work, state plainly what failed and answer with what you have."
)
_TOOL_ERROR_NUDGE_CAP = 2
# construction_calc "Unknown calculation" means the formula is not in the
# registry. Retrying another calculator name loops; self-coding writes
# Python once and returns. Only injected when the agent can delegate.
_UNKNOWN_CALC_SELF_CODING_NUDGE = (
    "That calculation is not a registered formula. Delegate to agent "
    "'self-coding' EXACTLY ONCE with the original question so it can "
    "write and run Python. Do not retry construction_calc with another "
    "name. Do not invent the number in prose."
)
_SELF_CODING_PHRASES = (
    "self-coding", "self coding", "write python", "generate python",
    "no formula", "no calculator", "no registered formula",
)

# Extension -> the file-consuming tool that analyses it. Used ONLY to build
# the corrective-nudge hint below; never a routing table.
_EXT_TOOL_HINTS = {
    ".ifc": "bim_extractor",
    ".dwg": "drawing_qto",
    ".dxf": "drawing_qto",
    ".xlsx": "boq_processor",
    ".csv": "boq_processor",
    ".pdf": "pdf",
    ".xer": "primavera_parser",
}


# Distinctive filename tokens in the user message (≥12 chars so ``spec``
# never hijacks every specification file).
_FILE_NAME_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]{11,}")


def _user_names_project_file(user_low: str, original_name: str) -> bool:
    """True when the user message names this project file.

    Full ``original_name`` match first. A distinctive stem (≥12 chars) also
    matches so leftover L1 can say ``khor_waterproofing_spec`` without the
    upload timestamp suffix. The user token is the *shorter* string when
    the stored name has a timestamp; match both directions.
    """
    name = (original_name or "").strip().lower()
    if not name or not user_low:
        return False
    if name in user_low:
        return True
    stem = os.path.splitext(name)[0]
    if len(stem) >= 12 and stem in user_low:
        return True
    if len(stem) < 12:
        return False
    for m in _FILE_NAME_TOKEN_RE.finditer(user_low):
        token_stem = os.path.splitext(m.group(0).rstrip(".,;:)"))[0]
        if len(token_stem) >= 12 and token_stem in stem:
            return True
    return False


_HISTOGRAM_PHRASES = (
    "manpower histogram", "labour histogram", "labor histogram",
    "resource histogram", "crew histogram", "workforce histogram",
    "labor loading", "labour loading",
)
_HISTOGRAM_QA_RE = re.compile(
    r"\b(what is|what's|whats|explain|define)\b", re.IGNORECASE,
)


def _message_wants_resource_histogram(text: str) -> bool:
    """True when the turn is a histogram deliverable, not a definition Q&A.

    Leftover M17: "histogram from resource_loaded.xer" must not fall through
    to primavera_parser just because the message contains ``.xer``.
    """
    low = (text or "").lower()
    if not low or _HISTOGRAM_QA_RE.search(low):
        return False
    if any(p in low for p in _HISTOGRAM_PHRASES):
        return True
    return "histogram" in low and any(
        t in low for t in (".xer", "primavera", "p6", "taskrsrc")
    )


def _resolve_histogram_schedule_file(
    project_id: str | None, user_message: str,
) -> tuple[str | None, str]:
    """Pick a project ``.xer`` for a histogram. Prefers a file the user named.

    Two unnamed ``.xer`` files → ``(None, "")`` so we never silently pick.
    """
    if not project_id:
        return None, ""
    try:
        from app.core import projects as _projects
        docs = _projects.list_documents(project_id) or []
    except Exception:  # noqa: BLE001
        return None, ""
    xers = [
        d for d in docs
        if (d.get("original_name") or "").lower().endswith(".xer")
        and d.get("file_path")
    ]
    if not xers:
        return None, ""
    low = (user_message or "").lower()
    named = [
        d for d in xers
        if _user_names_project_file(low, d.get("original_name") or "")
    ]
    pick = None
    if len(named) == 1:
        pick = named[0]
    elif len(named) > 1:
        for d in named:
            on = (d.get("original_name") or "").strip().lower()
            if on and on in low:
                pick = d
                break
        pick = pick or named[0]
    elif len(xers) == 1:
        pick = xers[0]
    if not pick:
        return None, ""
    fp = pick.get("file_path") or ""
    if not os.path.exists(fp):
        return None, ""
    return fp, pick.get("original_name") or os.path.basename(fp)


def _file_tool_hint(messages: list, project_id: str | None,
                    allowed_blocks: list[str]) -> str:
    """When the user's request names a REAL project file and the agent has
    the tool that reads it, say so in the corrective nudge.

    Live 2026-08-16: bim-analyst, asked for an IFC element census, flailed
    across fetch_document and formula_executor_v2 (generated code returning
    zeros) while bim_extractor -- the tool built for exactly this -- sat
    unused in its roster. K2 rejects forced tool_choice, so the only
    deterministic lever is DATA: name the file that exists and the tool
    that opens it. Not keyword routing -- the hint only fires when the
    message literally contains a document that is in the project store."""
    if not project_id:
        return ""
    user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user" and not str(m.get("content", "")).startswith(
                (_TOOL_ERROR_NUDGE[:20], _EMPTY_ROUTER_NUDGE[:20])):
            user_msg = str(m.get("content", ""))
            break
    if not user_msg:
        return ""
    low = user_msg.lower()
    try:
        from app.core import projects as _projects
        docs = _projects.list_documents(project_id) or []
    except Exception:  # noqa: BLE001
        return ""
    for d in docs:
        name = (d.get("original_name") or "").strip()
        if name and _user_names_project_file(low, name):
            ext = os.path.splitext(name)[1].lower()
            if (
                ext == ".xer"
                and _message_wants_resource_histogram(user_msg)
                and "construction" in allowed_blocks
            ):
                return (
                    f" The project file '{name}' EXISTS in this project. "
                    "Call the resource_histogram tool with "
                    f"schedule_file='{name}' — it builds the manpower / "
                    "activity histogram. Do not use primavera_parser for a "
                    "histogram."
                )
            tool = _EXT_TOOL_HINTS.get(ext)
            if tool and tool in allowed_blocks:
                return (
                    f" The project file '{name}' EXISTS in this project. "
                    f"Call the {tool} tool with file_path='{name}' -- it is "
                    "built for exactly this file type."
                )
            if os.path.splitext(name)[1].lower() in _TEXT_PREDISPATCH_EXTS:
                return (
                    f" The project file '{name}' EXISTS. Call fetch_document "
                    f"with filename='{name}' and quote unique tokens verbatim. "
                    "Do not stop after listing the filename."
                )
    return ""


# Extensions whose analysis is deterministically PRE-DISPATCHED: when the
# user's message literally names a project file of this type and the agent
# holds the tool, the runtime runs the tool itself BEFORE the model answers
# and injects the result as authoritative context. Born from the IFC census
# gate: K2 rejects forced tool_choice, and across three live rounds the
# model variously refused (F36), narrated (F27), and flailed into
# code-generation returning zeros -- while bim_extractor sat one call away.
# Machinery, not model behaviour. Kill-switch: AGENT_FILE_PREDISPATCH=0.
_FILE_PREDISPATCH_EXTS = {
    ".ifc": "bim_extractor",
    ".dxf": "drawing_qto",
    ".dwg": "drawing_qto",
}
# Plain-text AND office specs: fetch_document extracts bytes from disk
# even when RAG chunks are empty (leftover L1 khor_*.docx).
_TEXT_PREDISPATCH_EXTS = {".txt", ".md", ".docx", ".doc"}
# PDF drawings share the .pdf extension with specs/contracts. Only
# pre-dispatch drawing_qto when the turn is actually a take-off.
_PDF_QTO_HINT = re.compile(
    r"\b(qto|take-?off|take off|quantit(?:y|ies)|measur)",
    re.IGNORECASE,
)


async def _predispatch_file_tool(
    agent: "Agent", messages: list, project_id: str | None,
) -> dict[str, Any] | None:
    """Run the matching file tool up front and fold its result into the turn.

    Returns the tool-call record (for the response's tool_calls list) or
    None when the conditions don't hold. Never raises -- a pre-dispatch
    failure must not kill the turn; the model then proceeds normally and
    the corrective-nudge machinery covers the rest."""
    if os.getenv("AGENT_FILE_PREDISPATCH", "1") == "0" or not project_id:
        return None
    try:
        user_msg, _history = _messages_user_and_history(messages)
        if not user_msg:
            return None
        # A self-contained L×W×D volume ask must not steal drawing_qto just
        # because the project happens to have a PDF (leftover L6).
        if _looks_like_self_contained_calculation(user_msg):
            return None
        # Contract Data Q&A (delay damages, TfC, Schedule N, …) must not
        # steal to drawing_qto just because a DXF/PDF stem collides with
        # ordinary English in the question (UI-PHYS A5 / diagnostic D2:
        # "the whole of the Works.dxf" ⊂ "…for the whole of the Works?").
        contract_lookup = message_is_contract_data_lookup(user_msg)
        low = user_msg.lower()
        from app.core import projects as _projects
        docs = _projects.list_documents(project_id) or []
        target = None
        text_target = None
        for d in docs:
            name = (d.get("original_name") or "").strip()
            if name and _user_names_project_file(low, name):
                ext = os.path.splitext(name)[1].lower()
                if ext in _TEXT_PREDISPATCH_EXTS and text_target is None:
                    text_target = name
                tool = _FILE_PREDISPATCH_EXTS.get(ext)
                if (
                    tool is None
                    and ext == ".pdf"
                    and _PDF_QTO_HINT.search(user_msg)
                    and not contract_lookup
                ):
                    tool = "drawing_qto"
                if (
                    tool
                    and contract_lookup
                    and tool in ("drawing_qto", "bim_extractor")
                ):
                    tool = None
                if tool and tool in agent.allowed_blocks:
                    target = (name, tool)
                    break
        if text_target and not target:
            content, doc, err = _fetch_document_content(project_id, "", text_target)
            if err or not content:
                return None
            payload = str((content or {}).get("text") or "")[:6000]
            messages.append({
                "role": "user",
                "content": (
                    f"PLATFORM PRE-DISPATCH: fetch_document has ALREADY been "
                    f"run on '{text_target}' because the request names that "
                    f"file. Its authoritative contents:\n{payload}\n"
                    "Quote unique tokens from this result verbatim. Do not "
                    "stop at listing the filename."
                ),
            })
            return {
                "name": "fetch_document",
                "ok": True,
                "predispatched": True,
                "result": {
                    "filename": (doc or {}).get("original_name") or text_target,
                    "content": (content or {}).get("text"),
                    "truncated": (content or {}).get("truncated"),
                },
            }
        if not target:
            return None
        name, tool = target
        resolved = _resolve_file_path(project_id, name)
        instance = block_instances.get(tool) or _create_block_instance(tool)
        want_clash = message_wants_clash(user_msg)
        result = await instance.execute(
            {"file_path": resolved},
            ({"run_clash_detection": True} if want_clash
             else {"run_clash_detection": False}),
        )
        ok = isinstance(result, dict) and result.get("status") != "error"
        if not ok:
            return None  # fall back to the normal loop + nudges
        # UniversalBlock.execute wraps process() as {status, result: inner}.
        # Lead keys and clash_report live on the INNER dict; using the envelope
        # made leftover clash-on look like "element counts only" after the 6k cap.
        inner = result
        nested = result.get("result") if isinstance(result, dict) else None
        if isinstance(nested, dict) and (
            "ifc_schema" in nested
            or "clash_report" in nested
            or "quantities" in nested
            or "building_elements" in nested
        ):
            inner = nested
        if tool == "bim_extractor" and isinstance(inner, dict):
            lead_keys = (
                "status", "ifc_schema", "element_count", "quantities",
                "clash_report",
            )
            ordered = {k: inner[k] for k in lead_keys if k in inner}
            for k, v in inner.items():
                if k not in ordered:
                    ordered[k] = v
            inner = ordered
        clash_lead = ""
        cr = inner.get("clash_report") if isinstance(inner, dict) else None
        if want_clash:
            cr = cr or {}
            clash_lead = (
                f"clash_detection_ran={str(bool(cr)).lower()} clash_count="
                f"{len(cr.get('clashes') or [])} "
                f"method={cr.get('detection_method')}\n"
            )
        payload = clash_lead + json.dumps(inner, default=str)[:6000]
        messages.append({
            "role": "user",
            "content": (
                f"PLATFORM PRE-DISPATCH: the {tool} tool has ALREADY been "
                f"run on '{name}' because the request names that file. Its "
                f"authoritative result:\n{payload}\n"
                "Answer the request directly from this result. Do not "
                "re-run the tool unless a different action is needed."
            ),
        })
        return {"name": tool, "ok": True, "predispatched": True,
                "result": inner}
    except Exception:  # noqa: BLE001
        _LOG.warning("file pre-dispatch failed; continuing normally",
                     exc_info=True)
        return None


def _messages_user_and_history(messages: list) -> tuple[str, list]:
    """Latest operator text plus prior turns (for duration-override inference).

    Skip ``PLATFORM PRE-DISPATCH:`` user bubbles — live M1/M2/M9 on 69b12ff
    let a file/QTO inject become the "user" message, so remaining-deliverable
    detectors never saw the original WBS / cash-flow / claim ask.
    """
    user_msg = ""
    prior: list = []
    for m in messages or []:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        prior.append(m)
        if role == "user":
            content = str(m.get("content") or "")
            if content.lstrip().startswith("PLATFORM PRE-DISPATCH:"):
                continue
            user_msg = content
    history = prior[:-1] if prior else []
    return user_msg, history


def _operator_user_text(messages: list) -> str:
    """All non-predispatch user bubbles, oldest first.

    Live M9 graft must see the original claim ask even when a later user
    bubble is a haul-road file extract (the #410 last-bubble failure mode).
    """
    parts: list[str] = []
    for m in messages or []:
        if m.get("role") != "user":
            continue
        content = str(m.get("content") or "")
        if content.lstrip().startswith("PLATFORM PRE-DISPATCH:"):
            continue
        if content.strip():
            parts.append(content)
    return "\n".join(parts)


async def _predispatch_wbs_duration_override(
    agent: "Agent", messages: list,
) -> dict[str, Any] | None:
    """Re-run ``generate_wbs`` when the user overrides a template duration.

    ``consider 6 and re-run`` must not depend on the model remembering to
    pass ``duration_overrides``. Never raises — a miss falls through to
    the normal tool loop / forced generate_wbs.
    """
    if os.getenv("AGENT_WBS_DURATION_PREDISPATCH", "1") == "0":
        return None
    if "construction" not in getattr(agent, "allowed_blocks", ()):
        return None
    try:
        from app.lib.wbs_duration_overrides import (
            collect_overrides,
            infer_target_count,
            message_wants_wbs_duration_rerun,
        )
        user_msg, history = _messages_user_and_history(messages)
        if not user_msg or not message_wants_wbs_duration_rerun(user_msg, history):
            return None
        overrides = collect_overrides(user_msg, history=history)
        if not overrides:
            return None
        from app.dependencies import get_block_instance
        container = get_block_instance("construction")
        hist_text = "\n".join(
            str(m.get("content") or "") for m in history if m.get("role") == "user"
        )
        brief = hist_text.strip() or user_msg
        params = {
            "brief": brief,
            "target_count": infer_target_count(hist_text or user_msg),
            "user_message": user_msg,
            "history": history,
            "duration_overrides": overrides,
        }
        result = await container.generate_wbs({}, params)
        if not isinstance(result, dict) or result.get("status") != "success":
            return None
        compact = dict(result)
        acts = compact.get("activities") if isinstance(compact.get("activities"), list) else []
        compact["activities_total"] = len(acts)
        compact["activities_sample"] = acts[:15]
        compact.pop("activities", None)
        payload = json.dumps(compact, default=str)[:6000]
        applied = compact.get("duration_overrides_applied") or []
        applied_bits = ", ".join(
            f"{o.get('match')}={o.get('days')}d×{o.get('activities_updated')}"
            for o in applied
        ) or "none matched"
        messages.append({
            "role": "user",
            "content": (
                "PLATFORM PRE-DISPATCH: generate_wbs has ALREADY been re-run "
                f"with the user's duration override ({applied_bits}). "
                "Authoritative result:\n"
                f"{payload}\n"
                "Report the new durations and recomputed total/critical path "
                "from this result. Do not reuse a prior WBS table. Do not "
                "re-call generate_wbs unless a different override is needed."
            ),
        })
        return {
            "name": "generate_wbs",
            "ok": True,
            "predispatched": True,
            "result": compact,
        }
    except Exception:  # noqa: BLE001
        _LOG.warning("WBS duration pre-dispatch failed; continuing normally",
                     exc_info=True)
        return None


async def _predispatch_resource_histogram(
    agent: "Agent", messages: list, project_id: str | None,
) -> dict[str, Any] | None:
    """Run ``resource_histogram`` before the model can steal primavera_parser.

    Pinned ``construction-pm`` is not in the ``_forced_specific_tool`` gate
    (project-assistant / heavy-reasoning only). Leftover M17 therefore never
    saw a forced histogram tool. Predispatch is the deterministic lever —
    same pattern as WBS duration override. Kill-switch:
    ``AGENT_HISTOGRAM_PREDISPATCH=0``.
    """
    if os.getenv("AGENT_HISTOGRAM_PREDISPATCH", "1") == "0":
        return None
    if "construction" not in getattr(agent, "allowed_blocks", ()):
        return None
    if not project_id:
        return None
    try:
        user_msg, _history = _messages_user_and_history(messages)
        if not user_msg or not _message_wants_resource_histogram(user_msg):
            return None
        schedule_file, picked_name = _resolve_histogram_schedule_file(
            project_id, user_msg,
        )
        if not schedule_file:
            return None
        from app.dependencies import get_block_instance
        container = get_block_instance("construction")
        result = await container.resource_histogram(
            {}, {"schedule_file": schedule_file},
        )
        if not isinstance(result, dict) or result.get("status") != "success":
            return None
        compact = dict(result)
        periods = compact.get("periods") if isinstance(compact.get("periods"), list) else []
        compact["periods"] = periods[:20]
        payload = json.dumps(compact, default=str)[:6000]
        kind = compact.get("histogram_kind") or "labor"
        messages.append({
            "role": "user",
            "content": (
                "PLATFORM PRE-DISPATCH: resource_histogram has ALREADY been "
                f"run on '{picked_name}'. Authoritative result ({kind}):\n"
                f"{payload}\n"
                "Report the period buckets from this result. Do not invent "
                "man-hours. Do not call primavera_parser for this histogram. "
                "Do not re-call resource_histogram unless a different file "
                "is named."
            ),
        })
        return {
            "name": "resource_histogram",
            "ok": True,
            "predispatched": True,
            "result": compact,
        }
    except Exception:  # noqa: BLE001
        _LOG.warning("histogram pre-dispatch failed; continuing normally",
                     exc_info=True)
        return None


_WIR_FORM_RE = re.compile(
    r"\b("
    r"work inspection request|\bwir\b|inspection request|"
    r"wir form|wir template"
    r")\b",
    re.IGNORECASE,
)
_WIR_QA_RE = re.compile(
    r"\b(what is|what's|whats|explain|define)\b",
    re.IGNORECASE,
)


def _message_wants_wir_form(text: str) -> bool:
    """True when the turn is a WIR / inspection-request deliverable.

    Live M15: pinned construction-pm searched the named WIR template and
    returned empty. Predispatch writes the form from operator facts.
    Job requisition / claim / cash-flow / WBS / O&M / safety / IPC / as-built
    take priority even when the brief also says "inspection request".
    """
    low = (text or "").strip()
    if not low or _WIR_QA_RE.search(low):
        return False
    if (
        _message_wants_job_requisition(low)
        or _message_wants_delay_claim(low)
        or _message_wants_cash_flow(low)
        or _message_wants_om_manual(low)
        or _message_wants_safety_briefing(low)
        or _message_wants_first_run_wbs(low)
        or _message_wants_as_built_note(low)
        or _message_wants_ipc_draft(low)
        or _message_wants_follow_on_rfi(low)
        or _message_wants_qc_punch(low)
        or _message_wants_design_directive(low)
    ):
        return False
    return bool(_WIR_FORM_RE.search(low))


async def _predispatch_wir_form(
    agent: "Agent",
    messages: list,
    project_id: str | None,
    operator_text: str | None = None,
) -> dict[str, Any] | None:
    """Run ``wir_form`` before the model can search-loop a WIR template.

    Pinned ``construction-pm`` skips predefined intercept (F24). Kill-switch:
    ``AGENT_WIR_PREDISPATCH=0``.
    """
    if os.getenv("AGENT_WIR_PREDISPATCH", "1") == "0":
        return None
    if "construction" not in getattr(agent, "allowed_blocks", ()):
        return None
    try:
        user_msg, _history = _messages_user_and_history(messages)
        detect = (operator_text or user_msg or "").strip()
        if not detect or not _message_wants_wir_form(detect):
            return None
        from app.dependencies import get_block_instance
        container = get_block_instance("construction")
        result = await container.wir_form(
            {"text": detect}, {"user_message": detect},
        )
        if not isinstance(result, dict) or result.get("status") != "success":
            return None
        rendered = _format_wir_form(result)
        messages.append({
            "role": "user",
            "content": (
                "PLATFORM PRE-DISPATCH: wir_form has ALREADY been run from "
                "the operator facts in this turn. Authoritative draft:\n"
                f"{rendered}\n"
                "Present this WIR in full. Do not search again for the "
                "template. Do not claim the inspection was issued."
            ),
        })
        return {
            "name": "wir_form",
            "ok": True,
            "predispatched": True,
            "result": result,
        }
    except Exception:  # noqa: BLE001
        _LOG.warning("WIR pre-dispatch failed; continuing normally",
                     exc_info=True)
        return None


def _message_wants_commissioning(text: str) -> bool:
    raw = text or ""
    if _message_wants_om_manual(raw):
        return False
    return bool(re.search(r"commissioning checklist", raw, re.I))


def _message_wants_ipc_draft(text: str) -> bool:
    raw = text or ""
    if _message_wants_cash_flow(raw) or _message_wants_delay_claim(raw):
        return False
    if not re.search(r"payment certificate|\bipc\b|interim payment", raw, re.I):
        return False
    return _carries_ipc_figures(raw)


def _message_wants_delay_claim(text: str) -> bool:
    return bool(re.search(r"delay claim|claim notice|eot claim", text or "", re.I))


def _message_wants_as_built_note(text: str) -> bool:
    return bool(re.search(r"as-built deviation|as built deviation", text or "", re.I))


def _message_wants_job_requisition(text: str) -> bool:
    return bool(re.search(r"job requisition", text or "", re.I))


def _message_wants_rfp_draft(text: str) -> bool:
    raw = text or ""
    if _message_wants_wir_form(raw):
        return False
    return bool(re.search(r"\brfp\b|request for proposal|invitation to tender", raw, re.I))


def _message_wants_cash_flow(text: str) -> bool:
    return bool(re.search(
        r"cash[- ]flow|s-curve|s curve|spend curve|drawdown",
        text or "",
        re.I,
    ))


def _message_wants_om_manual(text: str) -> bool:
    return bool(re.search(
        r"operations and maintenance|o\s*&\s*m\b|o and m|maintenance manual",
        text or "",
        re.I,
    ))


def _message_wants_follow_on_rfi(text: str) -> bool:
    return bool(re.search(
        r"follow-on rfi|\bdraft a(?:n)? (?:follow[- ]on )?rfi\b|"
        r"request for information",
        text or "",
        re.I,
    ))


def _message_wants_qc_punch(text: str) -> bool:
    return bool(re.search(
        r"qc punch|punch list|hold-point sign-off|hold point sign-off",
        text or "",
        re.I,
    ))


def _message_wants_design_directive(text: str) -> bool:
    return bool(re.search(
        r"design directive|underpass length|rfi016|rfi 016",
        text or "",
        re.I,
    ))


def _message_wants_safety_briefing(text: str) -> bool:
    raw = text or ""
    if (
        _message_wants_first_run_wbs(raw)
        or _message_wants_cash_flow(raw)
        or _message_wants_follow_on_rfi(raw)
        or _message_wants_delay_claim(raw)
        or _message_wants_job_requisition(raw)
        or _message_wants_qc_punch(raw)
        or _message_wants_design_directive(raw)
        or _message_wants_ipc_draft(raw)
    ):
        return False
    if re.search(r"safety briefing|toolbox talk", raw, re.I):
        return True
    # Live M16 (safety-officer): "Green Village diversion briefing ... from the
    # three named files, covering signage, speed and pedestrian" matched
    # NOTHING here, because the site cue demanded the literal word ROAD
    # ("road diversion"). safety_briefing never ran, only fetch_document did,
    # and the hat answered "I'm ready to help, but I need a specific
    # question". A diversion named after its LOCATION rather than its road is
    # the same public-interface ask, so the cue no longer requires "road".
    site = re.search(
        r"haul[- ]road|\bdiversion\b|public-interface|traffic management",
        raw, re.I,
    )
    # Distinct topics, not total hits: "speed ... speed" is one topic, and the
    # briefing-only route below needs genuine breadth to fire.
    topics = {m.group(0).lower()
              for m in re.finditer(r"signage|pedestrian|speed", raw, re.I)}
    if site and topics:
        return True
    # No site cue at all, but the turn asks for a briefing and names at least
    # two of the three public-interface topics. Two is deliberate: one topic
    # plus the bare word "briefing" is too thin to claim the turn, and this
    # predicate already yields to every other deliverable above.
    return bool(re.search(r"\bbriefing\b", raw, re.I) and len(topics) >= 2)


def _message_wants_first_run_wbs(text: str) -> bool:
    raw = text or ""
    if not re.search(r"\b(build a wbs|work breakdown|\bwbs\b for)\b", raw, re.I):
        return False
    try:
        from app.lib.wbs_duration_overrides import message_wants_wbs_duration_rerun
        if message_wants_wbs_duration_rerun(raw, []):
            return False
    except Exception:  # noqa: BLE001 — import/shape miss must not steal first-run WBS
        _LOG.warning(
            "swallowed %s in _message_wants_first_run_wbs() — treating as first-run",
            "Exception", exc_info=True,
        )
    return True


def _message_wants_locked_deliverable(text: str) -> bool:
    """True when this turn already has a remaining/WIR-priority draft to lock."""
    return any(
        fn(text)
        for fn in (
            _message_wants_job_requisition,
            _message_wants_rfp_draft,
            _message_wants_delay_claim,
            _message_wants_cash_flow,
            _message_wants_om_manual,
            _message_wants_safety_briefing,
            _message_wants_follow_on_rfi,
            _message_wants_qc_punch,
            _message_wants_design_directive,
            _message_wants_first_run_wbs,
            _message_wants_as_built_note,
            _message_wants_ipc_draft,
            _message_wants_commissioning,
        )
    )


def _conflicting_tools_after_predispatch(name: str) -> set[str]:
    """Tools that stole the live Infra Pack answers after a correct draft."""
    steal = {
        "generate_wbs": {
            "payment_certificate", "drawing_qto", "wir_form",
            "primavera_parser", "interim_certificate_generator",
            "safety_briefing",
        },
        "cash_flow_forecast": {
            "drawing_qto", "payment_certificate", "wir_form",
            "interim_certificate_generator", "safety_briefing",
        },
        "om_manual_generator": {"commissioning_checklist", "wir_form"},
        "safety_briefing": {"wir_form", "commissioning_checklist"},
        "rfi_generator": {"wir_form", "safety_briefing"},
        "resource_histogram": {"primavera_parser", "safety_briefing"},
        "claims_builder": {
            "payment_certificate", "wir_form", "interim_certificate_generator",
        },
        "job_requisition": {"wir_form"},
        "rfp_draft": {"wir_form"},
        "as_built_deviation_report": {"wir_form", "commissioning_checklist"},
        "payment_certificate": {"wir_form", "claims_builder"},
        "commissioning_checklist": {"wir_form", "om_manual_generator"},
        "wir_form": {"payment_certificate", "job_requisition", "rfp_draft", "rfi_generator"},
    }
    return set(steal.get(name) or ())


def _inject_predispatch(
    messages: list,
    name: str,
    rendered: str,
    instruction: str,
) -> None:
    messages.append({
        "role": "user",
        "content": (
            f"PLATFORM PRE-DISPATCH: {name} has ALREADY been run from "
            f"the operator facts in this turn. Authoritative draft:\n"
            f"{rendered}\n"
            f"{instruction}"
        ),
    })


async def _predispatch_construction_draft(
    agent: "Agent",
    messages: list,
    *,
    env_key: str,
    want_fn,
    action: str,
    format_fn,
    instruction: str,
    operator_text: str | None = None,
) -> dict[str, Any] | None:
    if os.getenv(env_key, "1") == "0":
        return None
    if "construction" not in getattr(agent, "allowed_blocks", ()):
        return None
    try:
        user_msg, _history = _messages_user_and_history(messages)
        detect = (operator_text or user_msg or "").strip()
        if not detect or not want_fn(detect):
            return None
        user_msg = detect
        from app.dependencies import get_block_instance
        container = get_block_instance("construction")
        handler = getattr(container, action, None)
        if handler is None:
            return None
        params = {"user_message": user_msg, "brief": user_msg, "message": user_msg}
        if action == "commissioning_checklist":
            inferred = _infer_commissioning_systems(user_msg)
            if inferred:
                params["systems"] = inferred
        result = await handler(
            {"text": user_msg, "message": user_msg, "user_message": user_msg},
            params,
        )
        if not isinstance(result, dict) or result.get("status") != "success":
            return None
        rendered = format_fn(result)
        _inject_predispatch(messages, action, rendered, instruction)
        return {
            "name": action,
            "ok": True,
            "predispatched": True,
            "result": result,
        }
    except Exception:  # noqa: BLE001
        _LOG.warning("%s pre-dispatch failed; continuing normally", action,
                     exc_info=True)
        return None


async def _predispatch_remaining_deliverables(
    agent: "Agent",
    messages: list,
    project_id: str | None,
    operator_text: str | None = None,
) -> dict[str, Any] | None:
    """First matching non-WIR deliverable draft, so a Groq 413 still has copy."""
    user_msg, _history = _messages_user_and_history(messages)
    detect = (operator_text or user_msg or "").strip()
    if not detect or _message_wants_wir_form(detect):
        return None
    candidates = (
        (
            "AGENT_CASH_FLOW_PREDISPATCH",
            _message_wants_cash_flow,
            "cash_flow_forecast",
            _format_cash_flow,
            "Present this cash-flow S-curve in full. Do not run drawing_qto.",
        ),
        (
            "AGENT_OM_PREDISPATCH",
            _message_wants_om_manual,
            "om_manual_generator",
            _format_om_outline,
            "Present this O&M outline in full. Do not draft a commissioning checklist.",
        ),
        (
            "AGENT_WBS_PREDISPATCH",
            _message_wants_first_run_wbs,
            "generate_wbs",
            _format_wbs_result,
            "Report the operator-stated milestone durations (852 / 397 / 487 "
            "when present) and the WBS branches. Do not invent man-hours.",
        ),
        (
            "AGENT_RFI_PREDISPATCH",
            _message_wants_follow_on_rfi,
            "rfi_generator",
            _format_rfi,
            "Present this follow-on RFI in full. Do not draft a WIR.",
        ),
        (
            "AGENT_JOB_REQ_PREDISPATCH",
            _message_wants_job_requisition,
            "job_requisition",
            _format_job_requisition,
            "Present this job requisition in full. Do not draft a WIR.",
        ),
        (
            "AGENT_SAFETY_PREDISPATCH",
            _message_wants_safety_briefing,
            "safety_briefing",
            _format_safety_briefing,
            "Present this live-haul-road safety briefing in full.",
        ),
        (
            "AGENT_COMMISSIONING_PREDISPATCH",
            _message_wants_commissioning,
            "commissioning_checklist",
            _format_commissioning_tool,
            "Present this commissioning checklist in full. Do not invent "
            "holiday / spark tests on a reservoir wet test.",
        ),
        (
            "AGENT_IPC_PREDISPATCH",
            _message_wants_ipc_draft,
            "payment_certificate",
            _format_payment_certificate,
            "Present this IPC in full. Do not re-call payment_certificate.",
        ),
        (
            "AGENT_CLAIM_PREDISPATCH",
            _message_wants_delay_claim,
            "claims_builder",
            _format_claim_notice,
            "Present this delay-claim notice in full. Do not invent events.",
        ),
        (
            "AGENT_ASBUILT_PREDISPATCH",
            _message_wants_as_built_note,
            "as_built_deviation_report",
            _format_as_built_note,
            "Present this as-built volume note in full.",
        ),
        (
            "AGENT_RFP_PREDISPATCH",
            _message_wants_rfp_draft,
            "rfp_draft",
            _format_rfp_draft,
            "Present this RFP in full paragraphs. Do not draft a WIR.",
        ),
    )
    for env_key, want_fn, action, format_fn, instruction in candidates:
        out = await _predispatch_construction_draft(
            agent, messages,
            env_key=env_key,
            want_fn=want_fn,
            action=action,
            format_fn=format_fn,
            instruction=instruction,
            operator_text=detect,
        )
        if out:
            return out
    return None


def _tool_result_errored(tool_result: Any) -> bool:
    """True when a tool round FAILED, wherever the failure hides.

    F42: container results arrive envelope-green -- ``ok: True`` with
    ``status: "error"`` one or two levels down (the construction container
    wraps its route's error in a success-shaped transport envelope). The
    non-streaming loop only checked ``ok`` and never nudged; walk the
    nested ``result`` chain a few levels instead.
    """
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("ok") is False:
        return True
    node = tool_result.get("result")
    for _ in range(4):
        if not isinstance(node, dict):
            return False
        if node.get("status") == "error":
            return True
        node = node.get("result")
    return False


_EMPTY_ROUTER_NUDGE = (
    "The router matched NO action. If the request is a computation (volume, "
    "quantity, capacity, EVM, any arithmetic), you MUST now call the "
    "construction tool (action=construction_calc) or formula_executor_v2 and "
    "report that tool's result -- do not compute the number yourself. If no "
    "tool fits the request, say you have no tool for it. Never write "
    "'Routed to:' naming a tool you have not actually called in this turn."
)


# Declared hat-manifest action names -> real implementations (closes the
# KNOWN_INCOMPLETE dispatch gap, 2026-08-15). Each alias maps to machinery
# that actually exists: a registry calculator or a construction-container
# route. Names with no honest backing were REMOVED from the manifests
# instead of stubbed. The literal tuples in _run_tool_call keep the
# AST-based synthetic-tool census (test_agent_tool_surface) counting these
# as dispatchable the moment they dispatch.
_HAT_CALC_ALIASES = {
    "concrete_maturity_calculator": "concrete_maturity_strength",
    "grout_pressure_calculator": "grout_pressure_calc",
    "thermal_crack_assessor": "concrete_thermal_cracking_check",
    "mix_design_validator": "concrete_mix_design_sg",
    "crane_planner": "crane_planning",
    "critical_path_calc": "critical_path_float",
    "float_analysis": "critical_path_float",
    "tender_evaluator": "evaluate_tender",
    "supplier_scoring": "evaluate_tender",
    "compliance_gate_checker": "enforce_critical_rules",
    "ncr_tracker": "next_ncr_status",
}
_HAT_CONTAINER_ALIASES = {
    "interim_certificate_generator": "payment_certificate",
    "schedule_analysis": "parse_primavera_schedule",
    "baseline_compare": "forensic_delay_analysis",
    "eot_claim_assessor": "forensic_delay_analysis",
    "dispute_timeline_builder": "claims_builder",
    "variation_order_generator": "variation_order_manager",
    "boq_cost_analyzer": "boq_process",
    "drawing_qto_extract": "drawing_qto",
    "milestone_tracker": "progress_tracker",
    "rate_card_lookup": "benchmark_lookup",
    "itp_generator": "commissioning_checklist",
    "po_generator": "procurement_list_generator",
}
# Retrieval-shaped manifest actions resolve to the search synthetic tool.
_HAT_SEARCH_ALIASES = frozenset({"search_documents", "fidic_clause_lookup", "standards_lookup"})


# Tools whose result is a VERDICT or LOOKUP, not a deliverable. Their success
# must never trigger force-synthesis, because their result exists to be acted
# on by ANOTHER tool call: a routing verdict needs the dispatch that follows
# it. With smart_orchestrator counted as a deliverable, iter=1 ran
# with_tools=False -- the agent was contractually required to dispatch and
# structurally unable to, which is exactly the state that produced fabricated
# "Routed to:" claims on both live gates.
# cache_manager joined the set after a live document-ingestion turn ended on
# "Let me route this through the boq_processor" -- a narrated FUTURE dispatch
# as the final answer. The turn's only tool calls were search (already
# non-deliverable) + a cache lookup, which counted as a deliverable and
# disarmed force-synthesis. A cache hit/miss is infrastructure, never the
# artifact the user asked for.
_NON_DELIVERABLE_TOOLS = frozenset(
    {"search_project_documents", "smart_orchestrator", "cache_manager"}
)


def _tool_process_payload(tool_result: Any) -> dict[str, Any]:
    """Unwrap the execute envelope to the block's ``process()`` dict."""
    if not isinstance(tool_result, dict):
        return {}
    inner = tool_result.get("result")
    if not isinstance(inner, dict):
        return {}
    nested = inner.get("result")
    if isinstance(nested, dict) and any(
        k in nested for k in ("variances", "value", "stages", "overall", "expression")
    ):
        return nested
    return inner


def _should_force_synthesis(tool_result: Any) -> bool:
    """True when this tool round produced the artifact the user asked for.

    Empty sympy_reasoning (no variances, no evaluated expression) is NOT a
    deliverable — live leftover-hat L7: the model called sympy with no
    boq/drawing payload, got formula metadata, then force_synthesis disarmed
    formula_executor_v2 so the turn ended on "I will dispatch to the
    construction formula executor instead" with no 2.4 / 6840.
    """
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("ok") is False:
        return False
    name = tool_result.get("name")
    if name in _NON_DELIVERABLE_TOOLS:
        return False
    if name == "sympy_reasoning":
        payload = _tool_process_payload(tool_result)
        has_expr = payload.get("value") is not None and payload.get("expression")
        has_var = bool(payload.get("variances") or payload.get("cost_impacts"))
        if not has_expr and not has_var:
            return False
    return True


def _has_unread_windows(content: str) -> bool:
    """True when the model has been told to read more of this tool result.

    Live on 1d3c98b, the build that introduced char_offset. Asked for the m3
    demolition quantity, the model reported the truncation, said it could not
    confirm whether an m3 item appears deeper in the bill, offered to narrow
    the search -- and never called boq_processor again. It could not:
    boq_processor is a deliverable tool, so force_synthesis fired on the
    first result and the NEXT call went out with_tools=False.

    So the envelope told the model to call the tool again and the platform
    took its tools away. That is the same failure the sympy_reasoning
    carve-out above exists for, with a different tool: an instruction the
    system then prevents.

    A result with windows left to read is not a finished deliverable. It is
    half a document.
    """
    if not content or "chars_remaining" not in content:
        return False
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    try:
        return int(parsed.get("chars_remaining") or 0) > 0
    except (TypeError, ValueError):
        return False


def _empty_router_verdict(tool_result: Any) -> bool:
    """True when this tool round was smart_orchestrator returning an EMPTY
    match (matched_actions: []) -- the state in which fabricated dispatch
    claims were observed live."""
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("name") != "smart_orchestrator":
        return False
    inner = tool_result.get("result")
    if not isinstance(inner, dict):
        return False
    candidates = [inner]
    if isinstance(inner.get("result"), dict):
        candidates.append(inner["result"])
    for node in candidates:
        if "matched_actions" in node:
            return not node["matched_actions"]
    return False


#: Arguments the model may send that select HOW a result is presented rather
#: than what the block should do. Stripped before dispatch: the blocks are
#: vendored third-party code and an undeclared param is their problem, not
#: something to discover in production.
_PRESENTATION_ARGS = frozenset({"char_offset"})


def _strip_presentation_args(args: Any) -> Any:
    """Remove presentation-only arguments before a block ever sees them."""
    if not isinstance(args, dict):
        return args
    if not _PRESENTATION_ARGS.intersection(args):
        return args
    return {k: v for k, v in args.items() if k not in _PRESENTATION_ARGS}


def _dispatch_args(raw_args: Any) -> Any:
    """Parse a tool call's arguments for DISPATCH.

    One function so the strip cannot be forgotten at a call site: anything
    that wants arguments to send to a block gets them from here, already free
    of presentation-only params. Raises json.JSONDecodeError for the caller
    to turn into the invalid-args result.
    """
    parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    return _strip_presentation_args(parsed)


def _requested_char_offset(tool_call: dict[str, Any] | None) -> int:
    """The ``char_offset`` the model asked for, if any.

    A presentation parameter, not a block parameter: it selects which window
    of an over-long result comes back, and is stripped before dispatch so no
    block ever sees an argument it does not declare.
    """
    fn = (tool_call or {}).get("function") or {}
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return 0
    if not isinstance(args, dict):
        return 0
    try:
        return max(0, int(args.get("char_offset") or 0))
    except (TypeError, ValueError):
        return 0


def _tool_result_content(payload: dict[str, Any], offset: int = 0) -> str:
    """Serialize a tool result for the `tool` message, truncating SAFELY.

    NEVER slice serialized JSON. `json.dumps` emits ASCII with \\uXXXX
    escapes, so a raw ``[:8000]`` can cut mid-escape or mid-string and hand
    the provider malformed JSON. Moonshot rejects that outright:

        HTTP 400 {"message": "Invalid request: tokenization failed",
                  "type": "invalid_request_error"}

    Reproduced 2026-08-03 by the feature sweep on BOTH `document_metadata`
    and `parse_primavera_schedule` — the two actions whose tool results are
    large. The corpus makes it easy to hit: the client filenames contain en
    dashes, which serialize to `\\u2013`, and the cut landed inside one
    ("Unterminated string starting at ... char 7999").

    A failed turn used to vanish silently, so this surfaced as a question
    with no answer — see _persist_failed_turn.

    Over-long results become a VALID object carrying a truncated preview:
    the preview is a JSON *string value*, so json.dumps escapes it properly
    and the payload always parses.
    """
    text = json.dumps(payload, default=str)
    start = max(0, min(int(offset or 0), len(text)))
    if len(text) <= _TOOL_RESULT_MAX_CHARS and not start:
        return text
    window = text[start:]

    def _envelope(keep: int) -> str:
        shown = min(keep, len(window))
        next_offset = start + shown
        more = next_offset < len(text)
        return json.dumps(
            {
                "truncated": True,
                "note": (
                    f"Tool result exceeded {_TOOL_RESULT_MAX_CHARS} characters "
                    "and was truncated. Call this tool again with the SAME "
                    f"file_path and char_offset={next_offset} to read the next "
                    "window, and repeat until chars_remaining is 0. Do that "
                    "BEFORE telling the user you could not find something."
                    if more else
                    f"Tool result exceeded {_TOOL_RESULT_MAX_CHARS} characters "
                    "and was truncated. This is the LAST window -- you have "
                    "now seen the whole result."
                ),
                "char_offset": start,
                "next_char_offset": next_offset if more else None,
                "chars_remaining": max(0, len(text) - next_offset),
                # The size of the loss, and the rule about it. The note above
                # says the result was cut but not by how much, so the model
                # has to infer the extent from the content -- and on the live
                # BOQ question it inferred "pages 1-6 of 16" and reported that
                # no cubic-metre demolition item existed. The 945 m3 line was
                # past the cut. Same reasoning as the compaction envelope in
                # _compacted_tool_payload; both layers cut the same result, so
                # both have to say so.
                "chars_shown": shown,
                "chars_dropped": max(0, len(text) - next_offset),
                "chars_total": len(text),
                "scope_of_absence": (
                    f"You are seeing characters {start} to {next_offset} of "
                    f"{len(text)}. Anything the user asked about may be in "
                    "the part you cannot see. NEVER state or imply that a "
                    "value, line item, or section is absent from the document "
                    "or the data -- you cannot see far enough to know that. "
                    + (
                        "Read the remaining windows with char_offset before "
                        "answering about what is or is not present."
                        if more else
                        "You have now read every window, so a considered "
                        "statement about what this result contains is safe."
                    )
                ),
                "preview": window[:keep],
            },
            default=str,
        )

    # Embedding the preview as a JSON string RE-escapes it — every `"` becomes
    # `\"` and every `\` becomes `\\` — so escape-heavy content (exactly what
    # this guards) can nearly double. A fixed budget therefore overshoots the
    # cap; shrink until the ENVELOPE fits, measuring the real thing.
    keep = _TOOL_RESULT_MAX_CHARS
    out = _envelope(keep)
    while len(out) > _TOOL_RESULT_MAX_CHARS and keep > 0:
        overshoot = len(out) - _TOOL_RESULT_MAX_CHARS
        keep = max(0, keep - max(overshoot, 32))
        out = _envelope(keep)
    return out


def _persist_failed_turn(conversation_id: str | None) -> None:
    """Record that a turn died, so reloaded history is not silently missing it.

    Never raises: a bookkeeping failure must not mask the original error the
    caller is already reporting to the user.
    """
    if not conversation_id:
        return
    try:
        # Function-scope import, matching every other agent_memory use in this
        # module — it is NOT a module-level name here. Referencing it at module
        # scope raised NameError, which this very except block would have
        # swallowed, leaving a helper that silently persisted nothing.
        from app.core import agent_memory

        agent_memory.append_message(
            conversation_id, "assistant", _FAILED_TURN_MESSAGE
        )
    except Exception:  # noqa: BLE001 — best-effort bookkeeping
        _LOG.warning("could not persist failed-turn marker", exc_info=True)


# An export request asks for a FILE, not for a passage inside a document.
# UI-PHYS H1: the export turn was refused by the RAG-miss short-circuit below
# in ~0.6s having dispatched ZERO tools, so the export action never ran and the
# router's refusal was displayed as though it were the answer. Same shape as
# the unit-rate case already excluded there — a request that merely mentions a
# document is not a request to look something up inside one.
#
# Requires BOTH an action verb and a format/target word, so "what does the
# Specification document say about 003113" keeps the fast refusal while
# "export the BOQ to Excel" does not.
_EXPORT_ACTION_RE = re.compile(
    r"\b(export|download|save|send\s+me|give\s+me)\b[^.?!]{0,40}?"
    r"\b(word|docx|doc|pdf|excel|xlsx|csv|file|copy|version|attachment)\b"
    r"|\b(word|docx|pdf|excel|xlsx|csv)\b[^.?!]{0,25}?"
    r"\b(export|download|copy|version)\b",
    re.IGNORECASE,
)


def _asks_for_export(text: str) -> bool:
    """True when the turn wants a file produced, not a reference resolved."""
    return bool(_EXPORT_ACTION_RE.search(text or ""))


_MISSING_REFERENCE_ANSWER = (
    "I could not confirm this reference in the indexed project sources"
)


def _should_short_circuit_rag_miss(
    audit_rec: dict[str, Any] | None,
    rag_sys_msg: dict[str, str] | None,
    user_message: str = "",
) -> bool:
    """True when retrieval has definitively missed an exact reference.

    Conditions:
      * RAG injection produced no context (``rag_sys_msg`` is None).
      * The audit record shows either ``identifier_miss`` or
        ``threshold_fired``.
      * The query contained at least one extracted identifier that looks
        reference-like (contains a digit).  This prevents broad phrases such
        as "contract value" from triggering the fast path.

    Broad questions with no real reference identifiers are deliberately NOT
    short-circuited so the model can still answer from project facts or
    general knowledge. A self-contained calculation / self-coding request
    is never a document lookup — unit rates like ``AED/m2`` used to trip
    this gate and return "could not confirm this reference" in ~2s.
    """
    if rag_sys_msg is not None:
        return False
    if not audit_rec:
        return False
    if user_message and (
        _asks_self_coding(user_message)
        or _looks_like_self_contained_calculation(user_message)
        or _asks_for_export(user_message)
    ):
        return False
    identifiers = audit_rec.get("extracted_identifiers") or []
    # Require a digit to avoid short-circuiting generic phrases like
    # "contract value" that happen to match a reference label.
    if not any(re.search(r"\d", ident) for ident in identifiers):
        return False
    return bool(
        audit_rec.get("identifier_miss") or audit_rec.get("threshold_fired")
    )


def _build_missing_reference_answer(
    project_id: str | None,
    user_id: str | None,
) -> str:
    """Return a user-facing not-found answer, optionally naming the project."""
    project_name = ""
    if project_id:
        try:
            from app.core import projects as _projects

            project = _projects.get_project(
                project_id, user_id=user_id, include_admin_approved=True
            )
            project_name = (project or {}).get("name") or ""
            if not project_name and project_id == _projects.MASTER_CORPUS_PROJECT_ID:
                project_name = _projects.MASTER_CORPUS_NAME
        except Exception:
            _LOG.warning(
                "swallowed %s in _build_missing_reference_answer() — continuing",
                "Exception", exc_info=True,
            )

    if project_name:
        return (
            f"{_MISSING_REFERENCE_ANSWER} for {project_name}. "
            "Please check whether the document is indexed or provide the exact filename."
        )
    return (
        f"{_MISSING_REFERENCE_ANSWER}. "
        "Please check whether the document is indexed or provide the exact filename."
    )


def _normalize_tool_json_text(text: str) -> str:
    """Strip BOM / zero-width chars and unwrap a sole markdown JSON fence."""
    if not text:
        return ""
    cleaned = (
        text.replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .strip()
    )
    md = re.match(
        r"^```(?:json)?\s*([\s\S]*?)\s*```$",
        cleaned,
        re.IGNORECASE,
    )
    if md:
        return md.group(1).strip()
    return cleaned


def _is_search_tool_args_obj(obj: Any) -> bool:
    """True when ``obj`` looks like a search tool argument payload.

    Detects shapes such as:
      {"query": "...", "top_k": 5, "project_id": "..."}
      {"query": "..."}   # bare Scout/Kimi leak (UI-PHYS A5)
      {"tool": "search_project_documents", "arguments": {...}}
      {"name": "search_project_documents", "arguments": {...}}
      {"name": "search_project_documents", "parameters": {...}}
    """
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    # Explicit tool-call envelope shapes (arguments or parameters).
    if ("arguments" in keys or "parameters" in keys) and (
        "name" in keys or "tool" in keys or "function" in keys
    ):
        return True
    # Search argument shape: has "query" plus other search params, and no
    # obvious answer/data keys that would make it user-facing JSON.
    if "query" in keys and not (keys & _ANSWER_DATA_KEYS):
        if isinstance(obj.get("query"), str) and keys <= {
            "query", "top_k", "project_id", "corpus", "filters",
            "source", "tool", "function",
        }:
            # Bare {"query": "..."} is the live Scout/Kimi leak shape — treat
            # it as internal search args when it is not mixed with answer keys.
            return True
    return False


def _is_tool_call_obj(obj: Any) -> bool:
    """True when a parsed JSON object is an internal tool-call / search payload."""
    if not isinstance(obj, dict):
        return False
    if _INTERNAL_TOOL_KEYS.issubset(obj.keys()):
        return True
    if {"name", "parameters"}.issubset(obj.keys()):
        return True
    if obj.get("type") == "function" and "function" in obj:
        return True
    if _is_search_tool_args_obj(obj):
        return True
    return False


# Text the platform writes INTO the model's context and that therefore can
# never legitimately appear in an answer. Every one of these is a literal from
# app/core/rag/inject.py (the retrieval brief) or from a PLATFORM PRE-DISPATCH
# injection in this module.
#
# Live on 554e0b9, Master Corpus, fresh thread. Asked for the Delay Damages
# daily rate, the turn spent 564s, routed to generate_wbs, and then streamed
# the pre-dispatch payload to the user AS THE ANSWER -- opening with
#
#   {"status": "success", "wbs_id": "wbs-ccc4ee6c", "project_type":
#    "building", "brief": "AUTHORITATIVE REFERENCE CONTEXT - the material
#    below was retrieved ...
#
# and closing with the instruction the platform had written for the model
# ("Report the new durations ... Do not re-call generate_wbs"). In between:
# the whole retrieval brief, per-excerpt [doc_id= chunk= score=] telemetry,
# and the customer's absolute local paths --
# "G:\My Drive\Master Folder\the project\Contract Docs\Contractor\..." --
# for documents whose folder is named "Contract docs NOT SIGNED".
#
# _looks_like_internal_tool_json did not catch it and could not: every branch
# of _is_tool_call_obj recognises a tool CALL ({"name","arguments"},
# {"name","parameters"}, {"type":"function"}, search args). A tool RESULT --
# {"status","wbs_id","brief",...} -- matches none of them. The guard was built
# for the model emitting a call it should have made; this is the platform's
# own tool output arriving as content, which nothing tested for.
#
# Matched as substrings rather than by parsing, because the leak is dangerous
# in EVERY shape: whole JSON, JSON inside prose, or the brief quoted as prose
# with no JSON at all. A false positive costs one forced retry; a false
# negative ships the customer's private paths and the platform's own prompt
# to the customer.
_INTERNAL_CONTEXT_MARKERS = (
    "AUTHORITATIVE REFERENCE CONTEXT",
    "SCOPE OF ABSENCE",
    "FILENAMES ARE EVIDENCE",
    "CONTRACT ATTRIBUTION —",
    "PLATFORM PRE-DISPATCH",
)

# Per-excerpt retrieval telemetry, e.g. "[doc_id=cbca195d chunk=11 score=2.199
# src=...]". Internal by construction: the user never asked for a cosine.
_RETRIEVAL_MARKER_RE = re.compile(
    r"\[doc_id=[^\]\s]+\s+chunk=\d+\s+score=", re.IGNORECASE
)


def _looks_like_internal_context_leak(text: str) -> bool:
    """True when the platform's own context text is inside an answer.

    Distinct from :func:`_looks_like_internal_tool_json`, which asks whether
    the model emitted a tool CALL. This asks whether platform-authored
    context -- a retrieval brief, a pre-dispatch payload, excerpt telemetry --
    has come back out as user-facing content, however it is wrapped.
    """
    if not text:
        return False
    if any(marker in text for marker in _INTERNAL_CONTEXT_MARKERS):
        return True
    return bool(_RETRIEVAL_MARKER_RE.search(text))


class _EmitLeakGuard:
    """Last line of defence: no path may ship the platform's own context.

    #457 put the context-leak check in the streamed-synthesis branch and in
    _sanitize_final_text, and E1 came back clean. It recurred once on 87c7996
    -- same shape, same wbs_id/brief/Drive paths -- while the next run of the
    same question was clean. Intermittent means a path that neither guard
    covers, and I have not found which one.

    Rather than keep sampling seven-minute live turns for a path I cannot
    name, this sits where every event the client sees passes through:
    chat_stream wraps _chat_stream_impl, so one check here cannot be bypassed
    by any branch inside, present or future.

    It accumulates, because a marker can straddle two token events. Once the
    accumulated text trips the detector, nothing further is emitted and the
    turn ends on the controlled fallback.

    Residual, stated rather than hidden: tokens emitted BEFORE the marker
    appears have already gone. On the observed leak that is the opening
    ``{"status": "success", "wbs_id": "wbs-..."`` -- bad, and roughly 90
    characters instead of 6,170, with the brief, the excerpt telemetry and
    the customer's Drive paths all after the cut. Buffering the first few
    hundred characters would close that too, at the cost of changing
    time-to-first-token for every turn; not worth doing blind, and worth
    revisiting once the actual path is known.
    """

    __slots__ = ("_acc", "tripped")

    def __init__(self) -> None:
        self._acc: list[str] = []
        self.tripped = False

    def check(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """The event to emit, or None to drop it."""
        if not isinstance(event, dict):
            return event
        kind = event.get("type")
        if kind not in ("token", "end"):
            return event
        content = event.get("content")
        if not isinstance(content, str) or not content:
            return event

        if not self.tripped:
            if kind == "token":
                self._acc.append(content)
                probe = "".join(self._acc)
            else:
                probe = content
            if _looks_like_internal_context_leak(probe):
                self.tripped = True
                _LOG.warning(
                    "chat_stream: internal context reached the emit boundary "
                    "on a %s event -- suppressing the rest of this turn",
                    kind,
                )

        if not self.tripped:
            return event
        if kind == "token":
            return None
        return {**event, "content": _TOOL_FORMAT_FALLBACK}


def _looks_like_internal_tool_json(text: str) -> bool:
    """True if ``text`` is (or contains) JSON shaped like a tool call."""
    if not text:
        return False

    stripped = _normalize_tool_json_text(text)
    # Whole-string JSON.
    if stripped.startswith(("{", "[")):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            _LOG.warning(
                "swallowed %s in _looks_like_internal_tool_json() — continuing",
                "json.JSONDecodeError", exc_info=True,
            )
        else:
            if isinstance(obj, dict):
                if _is_tool_call_obj(obj):
                    return True
            if isinstance(obj, list):
                if obj and all(_is_tool_call_obj(item) for item in obj):
                    return True

    # Embedded JSON objects/lists (common when the model leaks a tool call
    # inside prose / after a formatting preamble).  Handles nested braces.
    scan = stripped or text

    def _candidates(open_ch: str, close_ch: str):
        i = 0
        while i < len(scan):
            if scan[i] == open_ch:
                depth = 1
                j = i + 1
                while j < len(scan) and depth > 0:
                    if scan[j] == open_ch:
                        depth += 1
                    elif scan[j] == close_ch:
                        depth -= 1
                    j += 1
                if depth == 0:
                    yield scan[i:j]
                    i = j
                    continue
            i += 1

    for candidate in _candidates("{", "}"):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and _is_tool_call_obj(obj):
            return True
    for candidate in _candidates("[", "]"):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list) and obj and all(_is_tool_call_obj(item) for item in obj):
            return True
    return False


def _coerce_tool_arguments(raw: Any) -> dict[str, Any] | None:
    """Normalize tool ``arguments`` / ``parameters`` to a dict when possible."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _tool_calls_from_json_obj(obj: Any) -> list[dict]:
    """Build OpenAI-style tool_calls from a parsed JSON object or list."""
    items = obj if isinstance(obj, list) else [obj]
    out: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        # OpenAI nested: {"type":"function","function":{"name":...,"arguments":...}}
        nested = item.get("function")
        if item.get("type") == "function" and isinstance(nested, dict):
            name = nested.get("name")
            args = _coerce_tool_arguments(
                nested.get("arguments", nested.get("parameters"))
            )
            if name and args is not None:
                out.append({
                    "id": f"recovered_{i+1}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                })
                continue
        name = item.get("name") or item.get("tool")
        if isinstance(item.get("function"), str) and not name:
            name = item.get("function")
        args = _coerce_tool_arguments(
            item.get("arguments", item.get("parameters"))
        )
        if name and args is not None:
            out.append({
                "id": f"recovered_{i+1}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            })
            continue
        # Raw search_project_documents args without an envelope.
        if _is_search_tool_args_obj(item) and "query" in item:
            out.append({
                "id": f"recovered_search_{i+1}",
                "type": "function",
                "function": {
                    "name": "search_project_documents",
                    "arguments": json.dumps({
                        k: item[k]
                        for k in ("query", "top_k", "project_id", "corpus", "filters")
                        if k in item
                    }),
                },
            })
    return out


_XML_INVOKE_RE = re.compile(
    r'<\s*invoke\s+name\s*=\s*["\']([^"\']+)["\']\s*>(.*?)</\s*invoke\s*>',
    re.IGNORECASE | re.DOTALL,
)
_XML_PARAM_RE = re.compile(
    r'<\s*parameter\s+name\s*=\s*["\']([^"\']+)["\']\s*>(.*?)</\s*parameter\s*>',
    re.IGNORECASE | re.DOTALL,
)
_XML_TOOL_LEAK_RE = re.compile(
    r"<\s*(?:function_calls|function_results|invoke)\b",
    re.IGNORECASE,
)
_VALIDATION_STAGE_ORDER = (
    "syntactic", "dimensional", "physical", "empirical", "operational",
)


def _looks_like_xml_tool_leak(text: str) -> bool:
    """True when the model dumped Anthropic-style XML tool calls as prose.

    Live leftover-hat L4: after ``validation_pipeline`` returned Physical /
    Tier 4, force_synthesis disarmed tools and the model streamed
    ``<function_calls><invoke name="formula_executor_v2">`` as the answer.
    """
    if not text:
        return False
    return bool(_XML_TOOL_LEAK_RE.search(text))


def _recover_xml_tool_calls(text: str) -> list[dict]:
    """Recover ``<invoke name=...>`` XML leaked into ``content``."""
    if not text or not _looks_like_xml_tool_leak(text):
        return []
    out: list[dict] = []
    for inv in _XML_INVOKE_RE.finditer(text):
        name = (inv.group(1) or "").strip()
        if not name:
            continue
        args: dict[str, Any] = {}
        for param in _XML_PARAM_RE.finditer(inv.group(2) or ""):
            pname = (param.group(1) or "").strip()
            pvalue = (param.group(2) or "").strip()
            if not pname:
                continue
            try:
                args[pname] = json.loads(pvalue)
            except (TypeError, json.JSONDecodeError):
                args[pname] = pvalue
        out.append({
            "id": f"recovered_xml_{len(out) + 1}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    return out


def _as_validation_payload(obj: Any) -> dict[str, Any] | None:
    """Unwrap execute/tool envelopes until ``stages`` + ``overall`` appear."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("stages"), dict) and obj.get("overall"):
        return obj
    for key in ("result", "content"):
        inner = obj.get(key)
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except (TypeError, json.JSONDecodeError):
                continue
        found = _as_validation_payload(inner)
        if found:
            return found
    return None


def _validation_pipeline_payload(
    messages: list[dict[str, Any]] | None = None,
    tool_results: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Most recent validation_pipeline process() dict from this turn."""
    last: dict[str, Any] | None = None
    for rec in tool_results or []:
        if not isinstance(rec, dict):
            continue
        found = _as_validation_payload(rec)
        if found:
            last = found
    for m in messages or []:
        if m.get("role") != "tool":
            continue
        raw = m.get("content")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
        envelope = raw if isinstance(raw, dict) else {}
        if m.get("name"):
            envelope = {"name": m.get("name"), "result": envelope}
        found = _as_validation_payload(envelope)
        if found:
            last = found
    return last


def _format_validation_verdict(payload: dict[str, Any]) -> str:
    """Kernel 5-stage block from a ``validation_pipeline`` result."""
    stages = payload.get("stages") or {}
    lines = [
        "Output under review: validation_pipeline → claim check",
        "",
        "Stages:",
    ]
    for i, name in enumerate(_VALIDATION_STAGE_ORDER, 1):
        st = stages.get(name) or {}
        mark = "✓" if st.get("pass") else "✗"
        reason = (st.get("reason") or "").strip()
        lines.append(f"{i}. {name.capitalize()}: {mark} {reason}".rstrip())
    overall = payload.get("overall") or (
        "fail" if payload.get("first_failure") else "pass"
    )
    lines.append("")
    tier = payload.get("tier")
    if tier is not None:
        lines.append(f"Tier: {tier}")
    lines.append(f"Verdict: {overall}")
    if overall == "fail":
        ff = payload.get("first_failure") or "physical"
        reason = ((stages.get(ff) or {}).get("reason") or "").strip()
        extra = f" — {reason}" if reason else ""
        lines.append(f"Required correction: {ff} stage failed{extra}")
    return "\n".join(lines)


def _graft_validation_verdict_if_unusable(
    text: str,
    messages: list[dict[str, Any]] | None = None,
    tool_results: list[Any] | None = None,
) -> str:
    """Replace leaked XML/JSON synthesis with the pipeline verdict.

    Leftover L4: ``validation_pipeline`` already failed Physical / Tier 4;
    the user-facing turn was ``formula_executor_v2`` XML instead.
    """
    unusable = (not (text or "").strip()) or text == _TOOL_FORMAT_FALLBACK
    if not unusable:
        return text
    payload = _validation_pipeline_payload(messages, tool_results)
    if not payload:
        return text
    return _format_validation_verdict(payload)


def _sanitize_final_text(
    text: str,
    messages: list[dict[str, Any]] | None = None,
    tool_results: list[Any] | None = None,
) -> str:
    """Prepare assistant content for display.

    1. Strip DeepSeek DSML tool-call markup.
    2. Detect raw internal tool-call JSON or Anthropic XML tool leaks.
    3. Return a controlled fallback if the content is raw internal tool args.
    4. If that fallback would hide a ``validation_pipeline`` verdict, graft it.
    5. Return empty string if nothing usable remains.
    """
    if not text:
        cleaned = ""
    else:
        cleaned = _strip_dsml(text).strip()

    if cleaned:
        # Unwrap a sole markdown fence / BOM before the tool-JSON detector so
        # ```json {"name":"search_project_documents",...} ``` cannot bypass.
        normalized = _normalize_tool_json_text(cleaned)
        if normalized != cleaned and _looks_like_internal_tool_json(normalized):
            _LOG.warning("raw tool args inside markdown code block; replacing with fallback")
            cleaned = _TOOL_FORMAT_FALLBACK

        if cleaned != _TOOL_FORMAT_FALLBACK and _looks_like_xml_tool_leak(cleaned):
            _LOG.warning("xml tool-call leak in final answer; replacing with fallback")
            cleaned = _TOOL_FORMAT_FALLBACK
        elif cleaned != _TOOL_FORMAT_FALLBACK and _looks_like_internal_tool_json(cleaned):
            _LOG.warning("raw tool-call JSON detected in final answer; replacing with fallback")
            cleaned = _TOOL_FORMAT_FALLBACK
        elif cleaned != _TOOL_FORMAT_FALLBACK and _looks_like_internal_context_leak(cleaned):
            # Both branches funnel through here, so the platform's own
            # context cannot reach a user down either one. The streaming
            # guards above stop the bytes earlier; this stops what is
            # persisted, exported and fed to sources.
            _LOG.warning(
                "internal context (retrieval brief / pre-dispatch payload / "
                "excerpt telemetry) detected in final answer; replacing with "
                "fallback"
            )
            cleaned = _TOOL_FORMAT_FALLBACK

    return _graft_validation_verdict_if_unusable(cleaned, messages, tool_results)


_KIMI_LEAK_RE = re.compile(r"functions\.([A-Za-z0-9_.\-]+):(\d+)")


def _recover_kimi_leaked_tool_calls(text: str) -> list[dict]:
    """Recover Kimi K2's native tool-call format leaked into ``content``.

    Live 2026-07-26 finding: on lookup questions Kimi sometimes emits its
    tool-call special-token section as PROSE — the stream showed
    ``...Let me search...functions.search_project_documents:0{"query":...``
    (special tokens mangled to garbage bytes). The pure-JSON recovery below
    never fires because the content starts with prose, so the leak streamed
    to the user as a 'final answer' and the search never ran. Each
    ``functions.<name>:<idx>`` marker followed by a JSON object becomes a
    real tool call; truncated JSON is skipped (the sanitizer handles it).
    """
    out: list[dict] = []
    for m in _KIMI_LEAK_RE.finditer(text):
        name = m.group(1).split(".")[-1]
        brace = text.find("{", m.end())
        if brace == -1:
            continue
        try:
            args, _ = json.JSONDecoder().raw_decode(text[brace:])
        except json.JSONDecodeError:
            continue
        if isinstance(args, dict):
            out.append({
                "id": f"recovered_kimi_{len(out) + 1}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            })
    return out


def _recover_tool_calls_from_content(text: str) -> list[dict]:
    """Recover OpenAI-style tool_calls from raw argument JSON/envelopes leaked
    into ``content`` by models (e.g. Groq Llama-4-Scout) that fail to emit the
    structured ``tool_calls`` field. Reuses the same detectors as
    ``_looks_like_internal_tool_json`` so we only recover shapes we already
    recognise as internal tool args. Also recovers Kimi K2's leaked
    ``functions.<name>:<idx>{...}`` markup (see above).
    """
    if not text:
        return []
    kimi = _recover_kimi_leaked_tool_calls(text)
    if kimi:
        return kimi
    xml_calls = _recover_xml_tool_calls(text)
    if xml_calls:
        return xml_calls
    stripped = _normalize_tool_json_text(text)
    if stripped.startswith(("{", "[")):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        if obj is not None:
            recovered = _tool_calls_from_json_obj(obj)
            if recovered:
                return recovered
    # Embedded whole-object leak after a short prose preamble ("Let me search.").
    if _looks_like_internal_tool_json(stripped):
        for m in re.finditer(r"\{", stripped):
            try:
                obj, _end = json.JSONDecoder().raw_decode(stripped[m.start():])
            except json.JSONDecodeError:
                continue
            recovered = _tool_calls_from_json_obj(obj)
            if recovered:
                return recovered
    return []


def _recovered_calls_are_search_only(tool_calls: list[dict]) -> bool:
    """True when every recovered call is ``search_project_documents``."""
    if not tool_calls:
        return False
    for tc in tool_calls:
        name = ((tc.get("function") or {}).get("name") or "")
        if name != "search_project_documents":
            return False
    return True


CONFIGS_DIR = Path(__file__).parent / "configs"
# Hard cap so a runaway loop can't burn budget (backstop). Kept at 12 for legit
# complex multi-step tasks; env-tunable. The real fix for the "keep re-searching"
# hang is the per-turn search-tool cap (AGENT_SEARCH_TOOL_CAP) in the chat loops,
# which stops offering search_project_documents after a couple of calls so the
# model answers from injected context instead of grinding to this cap.
MAX_TOOL_ITERATIONS = int(os.getenv("AGENT_MAX_TOOL_ITERATIONS", "12"))
# History replayed into each LLM call. 20 turns of long answers (schedules,
# checklists) pushed requests to ~100k tokens — slow/costly on any model, and
# over Groq's free-tier 30k TPM (413). 8 keeps requests well under that while
# retaining ample conversational memory. Env-tunable.
MAX_HISTORY_TURNS = int(os.getenv("AGENT_MAX_HISTORY_TURNS", "8"))
MAX_DELEGATION_DEPTH = 3  # how deep agent → agent delegation may recurse

# Blocks whose inputs reference a user-uploaded file. When the LLM passes a
# bare filename (e.g. "Infra-1 - Demolition BOQ.pdf") instead of the
# stored file_path, the block's os.path.exists() always fails. The runtime
# resolves the bare name to the document's actual on-disk path before
# dispatching to the block. See _resolve_block_file_input.
_FILE_CONSUMING_BLOCKS = {
    "boq_processor",
    "spec_analyzer",
    "primavera_parser",
    "drawing_qto",
    "bim",
    "bim_extractor",
    "document_engine",
    "image",
    "file_hasher",
}


def _resolve_file_path(project_id: str, raw: Any) -> Any:
    """Resolve a bare filename to the project's stored absolute file_path.

    Returns ``raw`` unchanged if:
      - ``raw`` is empty or not a string;
      - ``raw`` is already an absolute path that exists;
      - no matching document is found.

    Otherwise looks up the project's documents (via ``projects.list_documents``)
    and returns the document's ``file_path`` when the name matches.

    Matching is tried in two passes:
      1. Exact match on ``original_name`` (case-insensitive).
      2. Substring match either way (case-insensitive). This handles the
         common case where the LLM truncates or rewords the filename.
    """
    if not raw or not isinstance(raw, str):
        return raw
    if not project_id:
        return raw
    import os as _os
    try:
        if _os.path.isabs(raw) and _os.path.exists(raw):
            return raw
    except (TypeError, ValueError):
        return raw
    try:
        from app.core import projects as _projects
        docs = _projects.list_documents(project_id) or []
    except Exception:
        return raw

    needle = _os.path.basename(str(raw)).strip().lower()
    if not needle:
        return raw

    # Pass 1: exact (case-insensitive) match on original_name.
    for doc in docs:
        on = (doc.get("original_name") or "").strip().lower()
        if on and on == needle:
            fp = doc.get("file_path") or ""
            if fp and _os.path.exists(fp):
                return fp

    # Pass 2: substring match either direction (handles the LLM truncating
    # or rewording the filename slightly).
    for doc in docs:
        on = (doc.get("original_name") or "").strip().lower()
        if on and (needle in on or on in needle):
            fp = doc.get("file_path") or ""
            if fp and _os.path.exists(fp):
                return fp

    return raw


# Cap on the text a single fetch_document call feeds back into the model.
# Large contracts run to hundreds of pages; the tool returns the head and
# marks truncation honestly so the model knows to narrow with
# search_project_documents instead of assuming it saw everything.
_FETCH_DOCUMENT_MAX_CHARS = 24_000


def _fetch_document_content(
    project_id: str, doc_id: str, filename: str
) -> tuple:
    """Resolve one project document and return its text content.

    Returns ``(content, doc, error)`` where ``content`` is
    ``{"text", "truncated", "source"}`` and exactly one of
    ``content``/``error`` is set. Honesty rules mirror the deterministic
    doc→slot decision (DECISIONS 2026-07-13): no match → honest error;
    ambiguous name match → ask-which error, never silently pick one.
    An exact id or exact (case-insensitive) name match is never ambiguous —
    for duplicate names the most recently uploaded document wins, since a
    re-upload is what the user means by "the attached file".
    """
    try:
        from app.core import projects as _projects
        docs = _projects.list_documents(project_id) or []
    except Exception as exc:  # noqa: BLE001 — surface as tool error, not crash
        return None, None, f"document lookup unavailable: {exc}"

    # Every document the RETRIEVER can cite must be fetchable, not just the
    # active project's own. Retrieval merges general-knowledge projects and
    # (for a thin project) the Master-Corpus fallback into the context, so the
    # model routinely sees a [doc_id=...] marker for a document that
    # list_documents(project_id) does not contain. It would then call
    # fetch_document, get "no document with id X in this project", and report
    # the file as unavailable — while quoting from it. Live example: the model
    # cited the seeded ksa_saudi_building_code reference, then could not open
    # it. A citation the model cannot resolve is a citation the USER cannot
    # trust, so the fetch scope must match the retrieval scope exactly.
    #
    # Each extra corpus is recorded with the project id it lives under, because
    # the chunk lookup below is project-scoped: querying GK chunks under the
    # ACTIVE project id silently returns nothing.
    doc_sources: list[tuple] = [(project_id, docs)]
    try:
        from app.core.rag import retriever as _rag_retriever

        extra_pids = list(_rag_retriever._general_knowledge_project_ids())
        fallback_pid = _rag_retriever._master_corpus_fallback_id()
        if fallback_pid:
            extra_pids.append(fallback_pid)
        for pid in extra_pids:
            if not pid or pid == project_id:
                continue
            try:
                extra_docs = _projects.list_documents(pid) or []
            except Exception:  # noqa: BLE001 — one bad corpus must not break the tool
                continue
            if extra_docs:
                doc_sources.append((pid, extra_docs))
    except Exception:  # noqa: BLE001 — retrieval config unavailable: active only
        _LOG.warning(
            "could not resolve general-knowledge corpora for fetch_document; "
            "falling back to the active project only", exc_info=True,
        )

    # Flattened view for name matching, keeping each doc's owning project.
    all_docs: list[tuple] = [
        (owner_pid, d) for owner_pid, doc_list in doc_sources for d in doc_list
    ]

    doc = None
    doc_project_id = project_id
    if doc_id:
        for owner_pid, d in all_docs:
            if (d.get("id") or "") == doc_id:
                doc, doc_project_id = d, owner_pid
                break
        if doc is None:
            return None, None, f"no document with id '{doc_id}' in this project"
    else:
        needle = filename.strip().lower()
        exact = [
            (pid, d) for pid, d in all_docs
            if (d.get("original_name") or "").strip().lower() == needle
        ]
        if exact:
            doc_project_id, doc = exact[-1]  # duplicates: latest upload wins
        else:
            partial = [
                (pid, d) for pid, d in all_docs
                if needle in (d.get("original_name") or "").strip().lower()
            ]
            if not partial:
                return None, None, f"no document named '{filename}' in this project"
            if len(partial) > 1:
                names = ", ".join(sorted((d.get("original_name") or "?") for _p, d in partial))
                return None, None, (
                    f"{len(partial)} documents match '{filename}' ({names}) — "
                    "ask the user which one, or call again with its document_id."
                )
            doc_project_id, doc = partial[0]

    # Preferred source: the indexed RAG chunks (already extracted/cleaned).
    text = ""
    source = "chunks"
    try:
        from app.core.rag import retriever as _rag
        from app.core.rag import vector_store as _vs
        if _rag.available():
            # Scoped to the document's OWNING project — a general-knowledge or
            # Master-Corpus document's chunks are not stored under the active
            # project id, so querying that would return nothing and silently
            # fall through to raw extraction (or an empty-document error).
            chunks = _vs.get_store().doc_chunk_texts(doc_project_id, [doc["id"]])
            text = "\n\n".join(chunks.get(doc["id"], []) or [])
    except Exception:  # noqa: BLE001 — fall through to raw extraction
        text = ""
    if not text.strip():
        source = "extracted"
        try:
            from app.core.doc_index import extract_document_text
            text = extract_document_text(
                doc.get("file_path") or "", doc.get("original_name") or ""
            ) or ""
        except Exception as exc:  # noqa: BLE001
            return None, doc, f"could not read document content: {exc}"
    if not text.strip():
        return None, doc, (
            "document has no extractable text (it may be a scanned/raster file "
            "that is not yet OCR-indexed)"
        )

    truncated = len(text) > _FETCH_DOCUMENT_MAX_CHARS
    if truncated:
        text = text[:_FETCH_DOCUMENT_MAX_CHARS]
    return {"text": text, "truncated": truncated, "source": source}, doc, None


def _build_attached_documents_note(attached: list[dict[str, Any]]) -> str:
    """System note pinning this turn's attached document(s) for the model.

    ``attached`` items carry ``id`` / ``original_name`` (and optionally
    ``file_path`` / ``doc_type``) as resolved by the chat router from the
    composer's ``[attached: X]`` marker.
    """
    entries = []
    for d in attached or []:
        name = (d.get("original_name") or d.get("filename") or "").strip()
        did = (d.get("id") or d.get("document_id") or "").strip()
        if not name and not did:
            continue
        entries.append(f"- {name or '(unnamed)'} (document_id: {did or 'unknown'})")
    if not entries:
        return ""
    return (
        "ATTACHED DOCUMENT(S) THIS TURN:\n" + "\n".join(entries) + "\n"
        "When the user says 'the attached file' / 'this document' (or similar), "
        "they mean the document(s) above. Read it with the fetch_document tool "
        "(pass the document_id), or pass its filename to a file-consuming block. "
        "Do NOT ask which file they mean, and do NOT answer about it from memory "
        "without reading it."
    )


def _resolve_block_file_input(project_id: str, payload: Any) -> Any:
    """Apply :func:`_resolve_file_path` to any ``file_path`` / bare-string
    inputs in a block's ``input`` or ``params`` payload.

    The block-input contract is loose — sometimes it's a string (just the
    filename), sometimes a dict with ``file_path``, sometimes nested under
    other keys. We walk one shallow level and fix any field that looks like
    a file reference, leaving everything else untouched.
    """
    if payload is None:
        return payload
    if isinstance(payload, str):
        return _resolve_file_path(project_id, payload)
    if isinstance(payload, dict):
        out = dict(payload)
        for key in ("file_path", "filepath", "path", "input", "file"):
            if key in out and isinstance(out[key], str):
                out[key] = _resolve_file_path(project_id, out[key])
        return out
    return payload


# ── Anti-hallucination: scrub prior assistant turns that contain WBS/BOQ
# markdown tables. When conversation history carries a previously-emitted
# table (often hallucinated — Float=0 / Critical=Y on every row, fabricated
# file paths), the model pattern-matches to "produce another table" instead
# of calling generate_wbs / boq_processor. Replacing the table with a
# placeholder removes the pattern while preserving turn position so the
# conversation stays coherent.
_HALLUC_TABLE_RE = re.compile(
    r"^\s*\|[^\n]*?(?:Activity\s+ID|Float|Early\s+Start|Late\s+Start|Duration|"
    r"Quantity|Unit\s+Rate|BOQ|WBS|Critical\?)[^\n]*\|\s*\n"
    r"(?:\s*\|[-:\s|]+\|\s*\n)"
    r"(?:\s*\|[^\n]*\|\s*\n){5,}",
    re.IGNORECASE | re.MULTILINE,
)


def _scrub_history(turns: list[dict[str, str]]) -> list[dict[str, str]]:
    """Replace assistant turns that contain a WBS/BOQ-shaped table with a
    placeholder so the model can't pattern-match to a prior (often
    hallucinated) table when it should be calling the tool.

    Also strips assistant turns that are raw tool-call JSON (UI-PHYS A5) so a
    previously leaked envelope cannot re-seed the model or the UI history.

    Heuristic only: markdown tables with WBS/BOQ-like headers and >=5 rows.
    Legitimate operator-pasted tables in user turns are NOT touched.
    Returns a new list; the caller's history is left intact.
    """
    out: list[dict[str, str]] = []
    for t in turns:
        content = t.get("content") or ""
        if t.get("role") == "assistant" and _looks_like_internal_tool_json(content):
            out.append({
                **t,
                "content": (
                    "[Previous turn emitted internal tool JSON; ignored. "
                    "Answer in plain text from project documents.]"
                ),
            })
        elif t.get("role") == "assistant" and _HALLUC_TABLE_RE.search(content):
            out.append({**t, "content": "[Previous schedule/BOQ output omitted — call the tool to re-derive.]"})
        else:
            out.append(t)
    return out


# ── Anti-hallucination: force tool_choice="required" when the user's
# message names a deliverable type that maps 1:1 to a tool. Keeps Q&A on
# "auto" so explanation questions ("what is a WBS?") still get prose.
_DELIVERABLE_PHRASES = (
    "construction schedule", "wbs", "activity list", "gantt",
    "schedule with", "schedule of", "n-activity", "n activities",
    "critical path", "programme", "program of works", "project schedule",
    "manpower histogram", "labour histogram", "labor histogram",
    "resource histogram",
    "boq", "bill of quantities", "quantity takeoff", "extract quantities",
    "cost estimate", "budget breakdown", "cost breakdown",
    "compare boq to drawings", "discrepancy report",
    "generate recommendations", "recommend action",
    # Commissioning deliverables → force the commissioning_checklist tool so the
    # authoritative ASHRAE/AHRI/IEEE/BS 7671/NFPA tables are used, not an LLM
    # guess. Deliverable phrasings only — a bare "commissioning" question (e.g.
    # "when does commissioning start?") is left on tool_choice=auto.
    "commissioning checklist", "commissioning plan", "commissioning schedule",
    "testing and commissioning", "t&c checklist",
)


def _user_intent_requires_tool(messages: list[dict[str, Any]]) -> bool:
    """True iff the latest turn is the operator's first request AND it
    names a deliverable that should be produced by a tool call.

    Two gates:
    1. The tail of ``messages`` must be a ``role=user`` turn — i.e. we're
       on iteration 0 of the agent loop, before any tool call has run.
       On iteration N+1 the runtime has appended assistant + tool result
       turns; we must NOT force ``tool_choice="required"`` then or the
       model gets trapped in a forever-tool-call loop (it keeps being
       forced to call another tool instead of writing the final answer).
    2. The user message content matches a deliverable phrase.

    Returns False on no user message OR when the tail isn't user-role
    (so the runtime keeps ``tool_choice="auto"`` and the model is free
    to summarise the tool result into the user-visible final answer).
    """
    if not messages:
        return False
    tail = messages[-1]
    if tail.get("role") != "user":
        # Iter > 0: assistant + tool turns have been appended; let the
        # model decide how to proceed (will be summary, not another tool).
        return False
    raw = tail.get("content") or ""
    if message_is_contract_data_lookup(raw):
        return False
    text = raw.lower()
    return any(p in text for p in _DELIVERABLE_PHRASES)


# Deliverable phrase -> the SPECIFIC tool that must produce it. tool_choice=
# "required" alone let the model satisfy the requirement with the wrong tool
# (e.g. search_project_documents) and then generate the answer from memory,
# bypassing the tool's authoritative data. When one of these matches we force
# THAT tool by name so the real data path runs.
_INTENT_TOOL_MAP = (
    (("commissioning checklist", "commissioning plan", "commissioning schedule",
      "testing and commissioning", "t&c checklist"), "commissioning_checklist"),
    (("primavera", "xer", "p6", "baseline programme", "baseline program",
      "programme milestones", "program milestones", "project milestones",
      "schedule milestones", "milestones from"), "primavera_parser"),
    # Engineering CALCULATIONS: force the deterministic calculator so the model
    # runs the exact maths instead of computing the formula in prose (observed
    # gpt-4o-mini doing wrong prose-math + claiming it "cannot run the tool").
    # Only unambiguous calc phrases — cost build-ups stay on tool_choice=auto so
    # the cost-grounding path isn't forced.
    (("dewatering", "uplift check", "mix design", "formwork striking",
      "modulus of rupture", "well point spacing", "well-point spacing",
      "diaphragm wall volume", "crane capacity", "crane planning",
      "bearing pressure", "beam deflection",
      # reporting / commercial / procurement / risk calculators
      "earned value", "evm", "cost performance index", "schedule performance index",
      "estimate at completion", "interim payment", "net payment due",
      "tender evaluation", "evaluate tenders", "score the tenders", "bid evaluation",
      "risk score", "probability and impact"), "construction_calc"),
)


_IPC_ISSUE_RE = re.compile(
    r"\b(issue|generate|produce|create|prepare)\b.{0,60}\b"
    r"(interim\s+payment|payment\s+certificate|\bipc\b)",
    re.IGNORECASE,
)
_NAMED_CALC_ASK_RE = re.compile(
    r"\b(?:calculator|construction_calc|registered)\b|\bgross\s+\d",
    re.IGNORECASE,
)


def _message_wants_named_calculator(text: str) -> bool:
    """True when the turn is a registered ``construction_calc``, not a deliverable.

    Live F5: "interim payment certificate gross 750000 with 5 percent
    retention" was classified as ``payment_certificate``, rerouted to
    heavy-reasoning, then predefined intercept ran the container action with
    empty params. Named calculators stay on the requested agent so
    ``construction_calc`` remains the path.

    Negative: "issue an interim payment certificate from the contract"
    with no figures is a predefined IPC deliverable — do not steal it.
    """
    raw = text or ""
    if _looks_like_self_contained_calculation(raw):
        return True
    low = raw.lower()
    wants = any(
        p in low
        for phrases, tool in _INTENT_TOOL_MAP
        if tool == "construction_calc"
        for p in phrases
    )
    if not wants:
        return False
    if _NAMED_CALC_ASK_RE.search(raw):
        return True
    if _IPC_ISSUE_RE.search(raw) and not _carries_ipc_figures(raw):
        return False
    return True


def _carries_ipc_figures(text: str) -> bool:
    r"""True when the message actually supplies figures an IPC could use.

    This used to be ``re.search(r"\d", raw)`` -- ANY digit anywhere. The intent
    was "no figures supplied", but a project code or a package number is not a
    figure, so "issue the interim payment certificate ... on the DG2
    Infrastructure Package 1 contract" tripped it on the 2 in DG2 and the 1 in
    Package 1. The calculator branch then cleared ``action`` and the predefined
    IPC deliverable never dispatched: instead of the clean missing-figures
    error, the turn fell through to RAG and answered a payment question out of
    procurement-programme excerpts. Dates, revision numbers and drawing
    references all defeat it the same way.

    ``_payment_figures_from_message`` is the parser ``payment_certificate``
    itself uses to pull gross valuation / retention / advance out of a chat
    message, so it is the authority on whether figures are present. Falling
    back to the old digit test keeps behaviour unchanged if the container
    cannot be imported.
    """
    raw = text or ""
    try:
        from app.containers.construction.boq import (
            _payment_figures_from_message,
        )
        return bool(_payment_figures_from_message(raw))
    except Exception:
        _LOG.debug(
            "IPC figure parser unavailable; falling back to the digit test",
            exc_info=True,
        )
        return bool(re.search(r"\d", raw))


# A question that carries its OWN numbers is arithmetic, not a document lookup.
#
# Live 2026-08-03, project-assistant on the master corpus:
#   "Calculate the concrete volume for a raft 25m x 18m x 1.2m thick and the
#    number of 6m3 truck loads"
#   -> route None (below_routing_gate), construction_calc never invoked, and
#      the model answered:
#        "I cannot find that specific information in the provided reference
#         context ... they do not contain a raft measuring 25 m x 18 m x 1.2 m"
#
# 25 x 18 x 1.2 = 540 m3; 540 / 6 = 90 loads. Nothing needed to be retrieved.
# The grounding discipline overrode arithmetic and refused a question whose
# every input was in the question. `guardrail height` failed the same way.
#
# Both HAVE calculators (concrete_volume, guardrail_top_rail_height — 76 are
# registered), but _INTENT_TOOL_MAP's ~22 phrases only reach about a dozen of
# them. Rather than chase 76 keyword spellings, detect the SHAPE that is
# unambiguously a calculation: an explicit calc verb plus at least two
# measurement-bearing numbers.
#
# Deliberately narrow. A document lookup ("what is the concrete volume in the
# BOQ") has no calc verb AND no self-supplied dimensions, so it is unaffected
# and keeps the cost-grounding path.
#
# Kill switch: FORCE_CALC_ON_DIMENSIONS=0.
_CALC_VERB_RE = re.compile(
    r"\b(calculate|compute|work out|how many|how much|volume|excavation)\b",
    re.IGNORECASE,
)
# A number attached to a unit: 25m, 1.2 m, 6m3, 900,000 kg, 45 days
_DIMENSION_RE = re.compile(
    r"\b\d[\d,\.]*\s*"
    r"(mm|cm|m|km|m2|m3|sqm|cum|kg|tonne|tons?|t|kn|mpa|days?|weeks?|months?|%)\b",
    re.IGNORECASE,
)
# Engineering shorthand writes one dimension chain with ONE trailing unit:
# "30x20x1.5 m", "25 x 18 x 1.2 m", "1200x600". _DIMENSION_RE cannot see it --
# it needs a word boundary before each number, and "x" is a word character, so
# only the last number in the chain can ever match.
#
# THE MEASUREMENT (F-PHRASE-1, gate_battery_13b2bf7_2026-08-31.md). Battery
# question E4, verbatim from the instrument:
#
#     "Concrete volume for a raft 30x20x1.5 m including your documented
#      waste factor."               -> 1 dimension -> refusal
#
# A conversational paraphrase of the SAME question, same session, same corpus:
#
#     "I'm pouring a raft 30 m long, 20 m wide and 1.5 m thick, how much
#      concrete do I need ...?"     -> 3 dimensions -> 900 -> 945 m3, 119 trucks
#
# The user wrote the dimensions the way an engineer writes them and the
# platform decided that was a document lookup. Nothing about the question was
# ambiguous; the notation was.
#
# Narrow on purpose: at least TWO numbers joined by x / × / *, which is not a
# shape prose produces by accident. A document lookup ("what is the rate for
# 600x600 floor tiles") still has no calc verb and is unaffected.
_COMPACT_DIMENSION_RE = re.compile(
    r"(?<![A-Za-z0-9])\d[\d,\.]*\s*[x×*]\s*\d[\d,\.]*(?:\s*[x×*]\s*\d[\d,\.]*)*"
    r"(?:\s*(?:mm|cm|m|km|m2|m3|sqm|cum)\b)?",
    re.IGNORECASE,
)


def _count_dimensions(text: str) -> int:
    """Measurement-bearing numbers in ``text``, counting compact chains.

    A chain contributes each of its numbers: "30x20x1.5 m" is three, exactly
    as "30 m long, 20 m wide and 1.5 m thick" is three. Overlap is avoided by
    removing the chains before the plain pattern runs, so the trailing number
    of a chain is not counted twice.
    """
    body = text or ""
    count = 0
    for chain in _COMPACT_DIMENSION_RE.finditer(body):
        count += len(re.findall(r"\d[\d,\.]*", chain.group(0)))
    body = _COMPACT_DIMENSION_RE.sub(" ", body)
    return count + len(_DIMENSION_RE.findall(body))


_MIN_DIMENSIONS_FOR_CALC = 2
# Leftover L7: Compute (18.4-16)*2850 — numbers without unit suffixes.
_ARITH_EXPR_RE = re.compile(
    r"\(\s*\d[\d.]*\s*[-+*/]\s*\d[\d.]*\s*\)(?:\s*[-+*/]\s*\d[\d.]*)?",
)


def _looks_like_self_contained_calculation(text: str) -> bool:
    """True when the question supplies its own numbers AND asks to compute."""
    if (os.getenv("FORCE_CALC_ON_DIMENSIONS", "1") or "1").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return False
    if not text or not _CALC_VERB_RE.search(text):
        return False
    if _ARITH_EXPR_RE.search(text):
        return True
    return _count_dimensions(text) >= _MIN_DIMENSIONS_FOR_CALC


def _forced_specific_tool(messages: list[dict[str, Any]], available: set) -> str | None:
    """Return a tool name to force via named tool_choice for this turn, or None.
    Gated to the user-tail (iter 0) and to tools actually available, mirroring
    ``_user_intent_requires_tool``."""
    if not messages:
        return None
    tail = messages[-1]
    if tail.get("role") != "user":
        return None
    text = tail.get("content") or ""
    low = text.lower()
    # Contract Data TfC / milestone Q&A must not force primavera_parser or
    # generate_wbs — those questions belong to RAG.
    if message_is_contract_data_lookup(text):
        try:
            from app.lib.wbs_duration_overrides import message_wants_wbs_duration_rerun
            user_msg, history = _messages_user_and_history(messages)
            if not message_wants_wbs_duration_rerun(user_msg or text, history):
                return None
        except Exception:  # noqa: BLE001
            return None
    # Duration override + re-run must force generate_wbs, not a calculator.
    if "generate_wbs" in available:
        try:
            from app.lib.wbs_duration_overrides import message_wants_wbs_duration_rerun
            user_msg, history = _messages_user_and_history(messages)
            if message_wants_wbs_duration_rerun(user_msg or text, history):
                return "generate_wbs"
        except Exception:  # noqa: BLE001
            _LOG.debug(
                "duration-override tool force skipped; falling through",
                exc_info=True,
            )
    if "resource_histogram" in available and _message_wants_resource_histogram(text):
        return "resource_histogram"
    for phrases, tool in _INTENT_TOOL_MAP:
        if tool in available and any(p in low for p in phrases):
            return tool
    # Keyword phrases reach ~a dozen of the 76 registered calculators. Catch
    # the rest by SHAPE: a question that supplies its own dimensions and asks
    # to compute is arithmetic, whatever the domain noun happens to be.
    if "construction_calc" in available and _looks_like_self_contained_calculation(text):
        return "construction_calc"
    return None


# Bracketed forms:
#   [source: file.pdf, chunk 65]
#   【source: file.pdf, chunk 65】   (gpt-oss Chinese-bracket variant)
# Capture the entire bracket contents; _parse_source_tail extracts the
# filename and optional chunk suffix. This lets filenames contain commas
# without the regex engine swallowing the chunk suffix into group 1.
_CITATION_RE = re.compile(
    r"(?:\[source:|【source:)\s*(.+?)\s*(?:\]|】)",
    re.IGNORECASE,
)

# Bracketless line form gpt-oss-style models also emit:
#   Source: PRC-406_HSE.pdf, chunk 65.
#   Sources: PRC-406_HSE.pdf, chunks 16, 34, 55.
#   Source: “Diff BOQ Qty Vs Modified Qty.xlsx”, which lists ...
# Anchor on start-of-line or newline + "Source[s]:" prefix; capture the
# rest of the line up to a sentence terminator. The post-capture parsing
# strips quotes and extracts an optional ", chunk(s) ..." suffix.
_CITATION_LINE_RE = re.compile(
    r"(?:^|\n)\s*Sources?:\s*(.+?)(?=\.\s*(?:\n|$)|\n|$)",
    re.IGNORECASE,
)

# Quoted inline source mention (smart or straight quotes):
#   Source: “File Name.xlsx”, ...
#   Source: "File Name.xlsx", ...
#   Source: 'File Name.xlsx', ...
_CITATION_QUOTED_RE = re.compile(
    r"(?:^|\n|\s)Sources?:\s*([\"'“""])"""  # opening quote
    r"([^\"'”'\"\n]+?)"""
    r"\1""",
    re.IGNORECASE,
)

# doc_id form gpt-oss also emits when it wants to be technically precise:
#   [doc_id=3496d239, chunk 65, score 0.697]
#   [doc_id=3496d239 chunk=65 score=0.697]    (the RAG-injection header style)
# Match either separator style; capture (doc_id, chunk_index). The chunk
# is REQUIRED here — a bare [doc_id=...] would be ambiguous.
_CITATION_DOCID_RE = re.compile(
    r"\[\s*doc_id\s*=\s*([0-9a-f]{4,})\s*[,;\s]+\s*chunk\s*=?\s*(\d+)",
    re.IGNORECASE,
)

# gpt-oss-120b (the pilot model) inline form — the filename is INSIDE the
# brackets and the chunk number comes AFTER, mid-sentence:
#   ...protected (Source: [DD-2022-175 - Site Demolition … Part 3], chunk 941).
#   ...schedule (Source: [DD-2022-175 … Part 2], chunks 1988-1990).
# Distinct from _CITATION_RE ("[source: file, chunk N]" — source INSIDE the
# bracket). Group 1 = filename (often truncated with an ellipsis); group 2 =
# the chunk-number blob (digits, commas, and en-/em-dash ranges).
_CITATION_INLINE_BRACKET_RE = re.compile(
    r"sources?:\s*[\[【]\s*(.+?)\s*[\]】]\s*,?\s*chunks?\s+(\d[\d,\s‐-―−-]*)",
    re.IGNORECASE,
)

# Injection-marker copy: the model repeats ``src=filename`` from the RAG
# header. Filenames may contain spaces; the value ends at the closing
# bracket or end of line.
_CITATION_SRC_RE = re.compile(
    r"\bsrc\s*=\s*(.+?)(?:\s*\]|$)",
    re.IGNORECASE,
)


def _normalise_filename(s: str) -> str:
    """Normalize source filenames for cite-vs-chunk matching.

    Handles Unicode dashes, typographic quotes, surrounding whitespace,
    and trailing punctuation so the model's rewritten filenames still
    resolve to the stored original_name."""
    return (
        (s or "")
        .replace("‐", "-")  # hyphen
        .replace("‑", "-")  # non-breaking hyphen
        .replace("‒", "-")  # figure dash
        .replace("–", "-")  # en dash
        .replace("—", "-")  # em dash
        .replace("−", "-")  # minus
        .replace("“", "\"")
        .replace("”", "\"")
        .replace("‘", "'")
        .replace("’", "'")
        .strip(" \"'\n\t.")
        .lower()
    )


def _strip_source_quotes(s: str) -> str:
    """Remove matching surrounding quotes (smart/straight)."""
    s = s.strip()
    for open_q, close_q in (("“", "”"), ('"', '"'), ("'", "'"), ("‘", "'")):
        if s.startswith(open_q) and s.endswith(close_q):
            return s[1:-1].strip()
    return s


def _clean_path_label(label: str) -> str:
    r"""Return a user-facing source label stripped of Windows/Unix path gunk.

    Preserves the basename (or last non-empty path segment) so sources are
    still grounded to a real document, but never leaks ``G:\My Drive\...``
    or ``/home/user/...`` machine paths in the UI.
    """
    if not label:
        return label
    # Normalize Windows backslashes and URL separators to a single slash,
    # then return the last non-empty segment (the basename).
    normalized = label.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p]
    if parts:
        return parts[-1].strip()
    return label.strip()


# Generic machine-path sanitiser: catches stray Windows/Unix paths that
# appear outside formal citation markers (e.g. markdown table cells).
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\(?:[^\\]*\\)*[^\\\n]+"
    r"|\\\\[^\\]+(?:\\[^\\]+)+"
    r"|(?:/home/|/Users/)[^\n]+)",
    re.IGNORECASE,
)


def _sanitize_inline_paths(text: str) -> str:
    """Rewrite stray Windows/Unix file paths anywhere in the text to basenames."""
    if not text:
        return text
    return _PATH_RE.sub(lambda m: _clean_path_label(m.group(0)), text)


def _answer_is_caveat(text: str) -> bool:
    """True when the assistant answer is explicitly refusing/declining to answer."""
    if not text:
        return False
    lowered = text.lower()
    phrases = (
        "could not locate",
        "couldn't locate",
        "unable to locate",
        "cannot confirm",
        "can't confirm",
        "unable to generate",
        "was unable to",
        "wasn't able to",
        "not found",
        "i'm not able to",
        "i am not able to",
        "i cannot",
        "no record",
        "could not find",
        "i could not find",
        "none of the indexed files contain",
        "none of the indexed files",
        "i searched the project's document repository",
    )
    return any(p in lowered for p in phrases)


def _sanitize_citation_labels(text: str) -> str:
    """Rewrite inline citation labels so they show basenames, not raw paths.

    Handles the bracketed forms recognised by ``_CITATION_RE`` plus the
    bracketless ``Source:`` line form.  Only the label/path inside the
    citation is rewritten; chunk numbers and surrounding prose are left
    untouched.
    """
    if not text:
        return text

    def _repl_bracket(m: re.Match) -> str:
        inner = m.group(1)
        # inner still contains the label and optional chunk tail.
        fname, tail = _parse_source_tail(inner)
        clean = _clean_path_label(fname)
        # Reconstruct using the same bracket style as the original match.
        open_br = m.group(0)[0]
        close_br = {"[": "]", "【": "】"}.get(open_br, "]")
        if tail:
            return f"{open_br}source: {clean}, chunk {tail}{close_br}"
        return f"{open_br}source: {clean}{close_br}"

    text = _CITATION_RE.sub(_repl_bracket, text)

    # Bracketless "Source: ..." / "Sources: ..." lines.
    def _repl_line(m: re.Match) -> str:
        inner = m.group(1)
        fname, tail = _parse_source_tail(inner)
        clean = _clean_path_label(fname)
        prefix = m.group(0).split(":", 1)[0]  # "Source" or "Sources"
        if tail:
            return f"\n{prefix}: {clean}, chunk {tail}"
        return f"\n{prefix}: {clean}"

    text = _CITATION_LINE_RE.sub(_repl_line, text)
    return text


# ── Cost-grounding gate ─────────────────────────────────────────────────────
# A cost/rate figure in a chat answer must trace to a RATE-SEMANTIC retrieved
# chunk (currency/price context) or a computed tool result — otherwise the
# answer is refused. The unit of grounding is figure + rate-semantics, NOT the
# bare number: on 2026-07-14 a live cost query answered "450 SAR/m³" by lifting
# "450" out of a the client project drawing dimension-table chunk. A naive "the number appears
# somewhere in retrieval" gate would PASS that fabrication because 450 is in the
# soup; this gate does not, because that chunk is not rate-semantic and 450 does
# not sit next to a currency/rate-unit. Flag-controlled (COST_GROUNDING_GATE,
# default on); never raises (a gate must never break an answer).
_CG_CURRENCY = r"(?:SAR|SR|USD|US\$|AED|EUR|GBP|QAR|KWD|OMR|BHD|﷼|\$|€|£)"
# Pricing DENOMINATORS only (per-unit rate units), never bare dimensions like
# mm/cm — a "450 mm" dimension must never read as a rate.
_CG_RATE_UNIT = (
    r"(?:/|per\s+)\s*"
    r"(?:day|month|week|year|hour|hr|man-?hour|m3|m²|m2|m³|sqm|sq\s?m|cum|"
    r"kg|tonne|ton|lm|l\.?m|no\.?|nr|unit|each|ea|item|m)\b"
)
_CG_NUM = r"\d[\d,]*(?:\.\d+)?"
_CG_MONEY_RE = re.compile(
    rf"{_CG_CURRENCY}\s*({_CG_NUM})"       # SAR 300 / $1,200
    rf"|({_CG_NUM})\s*{_CG_CURRENCY}"      # 300 SAR
    rf"|({_CG_NUM})\s*{_CG_RATE_UNIT}",    # 55 /m³ / 1,200 per day
    re.IGNORECASE,
)
# A chunk is rate-semantic if it carries currency or explicit rate/price
# vocabulary. Drawing dimension-tables (pure numbers + BEND/PIPE/mm labels)
# match none of these, so their numbers never ground a cost claim.
_CG_RATE_SEMANTIC_RE = re.compile(
    rf"{_CG_CURRENCY}|\b(?:unit\s*rate|rate\b|priced?|pricing|tender\s*price|"
    r"bill\s+of\s+quantit|boq|cost\s*/|/\s*m[23³²]|per\s+m[23³²]|per\s+tonne|"
    r"per\s+kg|sar/|usd/)\b",
    re.IGNORECASE,
)
_CG_NUM_RE = re.compile(_CG_NUM)
# Magnitude suffixes: a tool returns 1250000 while the model naturally writes
# "SAR 1.25 million". Fold the suffix so the figure compares to the raw grounded
# number instead of being false-refused (Codex #223). Applied to CURRENCY money
# forms only — a rate unit ("1.25 /m3") never carries a magnitude word, and a
# bare "1.25 m" without a currency could be metres.
_CG_MAGNITUDE = {
    "thousand": 1e3, "k": 1e3,
    "million": 1e6, "mn": 1e6, "mln": 1e6, "m": 1e6,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
}
_CG_SUFFIX_RE = re.compile(r"\s*(thousand|million|billion|mln|mn|bn|[kmb])\b", re.IGNORECASE)
_CG_REFUSAL = (
    "I don't have a rate on file for that in the project documents or the "
    "knowledge base. Please upload your priced BOQ / rate schedule, or get a "
    "supplier quote for a firm figure — I won't quote a price I can't ground."
)


def _cost_grounding_enabled() -> bool:
    return os.getenv("COST_GROUNDING_GATE", "1") not in ("0", "false", "False", "")


# A cost/rate-shaped user question. Token streaming yields the answer before the
# cost gate can act, so a fabricated price would reach the client (Codex
# #223/#224). For these queries only, streaming is disabled so the full answer is
# gated before emission. Non-cost queries (the common case) stream normally.
_CG_COST_QUERY_RE = re.compile(
    r"\b(rate|rates|unit\s*rate|cost|costs|price|priced|pricing|how\s+much|"
    r"quote|quotation|sar|usd|aed|qar|/\s*m[23³²]|per\s+m[23³²]|per\s+tonne|"
    r"per\s+kg|bill\s+of\s+quantit|boq)\b",
    re.IGNORECASE,
)


def _is_cost_shaped_query(user_message: str | None) -> bool:
    return bool(user_message and _CG_COST_QUERY_RE.search(user_message))


# Document deliverables (RFP, WIR, O&M, …) often mention a currency or a
# contract sum. The cost gate used to replace the *entire* document with
# `_CG_REFUSAL` ("I don't have a rate on file") — live M14 on theshovel.ai.
# Skip that full-answer wipe unless the user is actually asking for a unit
# rate / price-per. Fabricated unit rates on a rate-quote still refuse.
_DOCUMENT_DELIVERABLE_RE = re.compile(
    r"\b("
    r"rfp|request for proposal|invitation to tender|"
    r"job requisition|prequalification shortlist|"
    r"design directive|"
    r"commissioning checklist|"
    r"inspection request|work inspection request|\bwir\b|"
    r"delay claim|claim notice|eot claim|"
    r"as-built deviation|as built deviation|"
    r"o\s*&\s*m(?:\s+manual)?|\bom manual\b|"
    r"\bncr\b|non[-\s]?conformance|"
    r"punch list|qc inspection|"
    r"\bwbs\b|work breakdown|"
    r"payment certificate|interim payment|\bipc\b|"
    r"cash[- ]?flow|"
    r"safety briefing|resource histogram|"
    r"follow-on rfi|request for information"
    r")\b",
    re.IGNORECASE,
)
_RATE_QUOTE_RE = re.compile(
    r"\b(unit\s*rate|how much per|price per|rate per|quote (?:a |the )?rate)\b",
    re.IGNORECASE,
)


def _latest_user_text(messages: list[dict[str, Any]] | None) -> str:
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        content = str(m.get("content") or "")
        if content.lstrip().startswith("PLATFORM PRE-DISPATCH:"):
            continue
        return content
    return ""


def _is_document_deliverable_request(user_message: str | None) -> bool:
    if not user_message:
        return False
    if _RATE_QUOTE_RE.search(user_message):
        return False
    return bool(_DOCUMENT_DELIVERABLE_RE.search(user_message))


def _infer_commissioning_systems(text: str | None) -> list[str] | None:
    """Map the user's wording onto commissioning system keys.

    Leftover torch-SBS / membrane / before-backfill → waterproofing
    (holiday/spark belongs there). Infra Pack M3 PWPS-02 reservoir /
    first wet test / C21-OPC blinding must NEVER take that path.
    """
    low = (text or "").lower()
    reservoir = any(
        k in low
        for k in (
            "reservoir", "pwps", "wet test", "wet-test", "first wet",
            "c21-opc", "c21 opc",
        )
    )
    waterproof = any(
        k in low
        for k in (
            "waterproof", "torch-applied", "torch applied", "sbs",
            "membrane", "before backfill",
        )
    )
    if reservoir and not waterproof:
        return ["reservoir"]
    if waterproof and not reservoir:
        return ["waterproofing"]
    if reservoir and waterproof:
        if any(k in low for k in ("wet test", "wet-test", "first wet")):
            return ["reservoir"]
        return ["waterproofing"]
    return None


def _format_commissioning_tool(payload: dict[str, Any]) -> str:
    """User-facing checklist from a successful commissioning_checklist tool."""
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    by_sys = payload.get("checklists_by_system") or {}
    lines = [
        "Commissioning checklist (from the commissioning_checklist tool).",
        "",
        f"Systems covered: {summary.get('systems_covered', len(by_sys))}. "
        f"Tests: {summary.get('total_tests', 0)} "
        f"({summary.get('pending', 0)} pending).",
        "",
    ]
    if not isinstance(by_sys, dict):
        return "\n".join(lines).strip()
    for system, tests in by_sys.items():
        lines.append(f"## {str(system).replace('_', ' ').title()}")
        if not isinstance(tests, list):
            continue
        for t in tests:
            if not isinstance(t, dict):
                continue
            name = t.get("test") or "Test"
            std = t.get("standard") or ""
            acc = t.get("acceptance_criteria") or ""
            wit = "witness" if t.get("witness_required") else "review"
            hold = "hold" if t.get("hold_point") else wit
            lines.append(f"- **{name}** ({std}) — {acc} [{hold}]")
        lines.append("")
    return "\n".join(lines).strip()


def _format_wir_form(payload: dict[str, Any]) -> str:
    """User-facing WIR from a successful ``wir_form`` tool / predispatch."""
    lines = [
        f"**Work Inspection Request — {payload.get('wir_number') or 'DRAFT-WIR'}**",
        f"**Location:** {payload.get('location') or '—'}",
        f"**Activity:** {payload.get('activity') or '—'}",
        "",
        f"### 1. Scope",
        str(payload.get("scope") or "As stated by the operator."),
        "",
    ]
    mix = payload.get("mix") or ""
    vol = payload.get("volume_m3")
    week = payload.get("week")
    supplier = payload.get("supplier") or ""
    facts = []
    if week:
        facts.append(f"Week {week}")
    if mix:
        facts.append(mix)
    if vol not in (None, ""):
        facts.append(f"{vol} m³")
    if payload.get("manhole_range"):
        facts.append(str(payload["manhole_range"]))
    elif payload.get("manhole_count"):
        facts.append(f"{payload['manhole_count']} manholes")
    if supplier:
        facts.append(f"supplier {supplier}")
    if facts:
        lines.append("**Stated facts:** " + "; ".join(facts))
        lines.append("")
    tmpl = payload.get("template") or ""
    if tmpl:
        lines.append(f"**Template:** {tmpl}")
        lines.append("")
    lines.append("### 2. Pre-pour checklist")
    for row in payload.get("checklist") or []:
        if isinstance(row, dict):
            lines.append(f"- [ ] {row.get('item') or row}")
        else:
            lines.append(f"- [ ] {row}")
    lines.append("")
    lines.append("### 3. Hold points")
    for h in payload.get("hold_points") or []:
        lines.append(f"- {h}")
    lines.append("")
    lines.append("### 4. Witness points")
    for w in payload.get("witness_points") or []:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("### 5. Sign-off")
    lines.append("| Party | Status |")
    lines.append("|-------|--------|")
    for s in payload.get("signatories") or []:
        if isinstance(s, dict):
            lines.append(
                f"| {s.get('party') or '—'} | {s.get('status') or 'pending'} |"
            )
    lines.append("")
    if payload.get("notice"):
        lines.append(str(payload["notice"]))
        lines.append("")
    if payload.get("note"):
        lines.append(str(payload["note"]))
    return "\n".join(lines).strip()


def _text_needs_tool_recovery(text: str) -> bool:
    """True when the LLM hop died and a tool/predispatch draft should speak."""
    t = (text or "").strip()
    if not t:
        return True
    if t == _EMPTY_RESPONSE_FALLBACK:
        return True
    if "unable to generate" in t.lower():
        return True
    if re.search(r"HTTP 413|tokens per minute|\bTPM\b", t):
        return True
    return False


def _format_payment_certificate(payload: dict[str, Any]) -> str:
    cert = payload.get("certificate") if isinstance(payload.get("certificate"), dict) else {}
    val = payload.get("valuation") if isinstance(payload.get("valuation"), dict) else {}
    ded = payload.get("deductions") if isinstance(payload.get("deductions"), dict) else {}
    pay = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
    lines = [
        f"**Interim Payment Certificate — {cert.get('period') or 'Current Period'}**",
        f"**Contractor:** {cert.get('contractor') or 'Contractor'}",
        "",
        f"- Contract value: {val.get('contract_value')}",
        f"- Gross valuation: {val.get('gross_valuation')}",
        f"- Retention ({ded.get('retention_percent')}%): {ded.get('retention_held')}",
        f"- Advance recovery: {ded.get('advance_recovery')}",
        f"- Net due this period: {pay.get('net_due_this_period')}",
        f"- Cumulative certified: {pay.get('cumulative_certified')}",
        "",
        str(payload.get("certificate_summary") or ""),
    ]
    return "\n".join(lines).strip()


def _format_as_built_note(payload: dict[str, Any]) -> str:
    if payload.get("note"):
        return str(payload["note"])
    planned = payload.get("planned_m3")
    poured = payload.get("poured_m3")
    short = payload.get("shortfall_m3")
    pct = payload.get("shortfall_percent")
    loc = payload.get("location") or ""
    return (
        f"As-built deviation note{f' — {loc}' if loc else ''}: "
        f"planned {planned} m³ versus poured {poured} m³ — "
        f"shortfall {short} m³ ({pct}%)."
    )


def _format_claim_notice(payload: dict[str, Any]) -> str:
    narrative = payload.get("claim_narrative") if isinstance(payload.get("claim_narrative"), dict) else {}
    body = narrative.get("full_narrative") or narrative.get("executive_summary")
    if body:
        extra = []
        if payload.get("quantum_formula"):
            extra.append(f"Quantum: {payload['quantum_formula']}")
        comms = payload.get("communication")
        if comms:
            extra.append(f"Communication: {comms}")
        if payload.get("clause"):
            extra.append(f"Clause: {payload['clause']}")
        return "\n\n".join([str(body)] + extra)
    return json.dumps(payload, default=str)[:4000]


def _graft_operator_claim_facts(text: str, user_message: str) -> str:
    """Keep Aconex / 8.8.1 on the published claim when synthesis drops them."""
    if not text or not _message_wants_delay_claim(user_message):
        return text
    out = text
    if re.search(r"\baconex\b", user_message, re.I) and not re.search(r"\baconex\b", out, re.I):
        out = out.rstrip() + "\n\nThis notice is issued via Aconex."
    if re.search(r"8\.8\.1", user_message) and "8.8.1" not in out:
        out = re.sub(r"Clause\s+4\.5", "Clause 8.8.1", out, count=1)
        if "8.8.1" not in out:
            out = out.rstrip() + "\n\nClause 8.8.1."
    return out


def _format_rfi(payload: dict[str, Any]) -> str:
    rfis = payload.get("rfis") if isinstance(payload.get("rfis"), list) else []
    first = next((row for row in rfis if isinstance(row, dict)), None)
    number = (
        payload.get("rfi_number")
        or (first.get("rfi_number") if first else None)
        or "DRAFT-RFI"
    )
    lines = [
        f"**Request for Information — {number}**",
        "",
    ]
    if payload.get("question"):
        lines.append(str(payload["question"]))
    for row in rfis:
        if not isinstance(row, dict):
            continue
        lines.append(f"**{row.get('rfi_number') or 'RFI'} — {row.get('subject') or ''}**")
        lines.append(str(row.get("question") or ""))
        lines.append("")
    if payload.get("note"):
        lines.append(str(payload["note"]))
    return "\n".join(lines).strip() or json.dumps(payload, default=str)[:4000]


def _format_wbs_result(payload: dict[str, Any]) -> str:
    lines = ["**Work Breakdown Structure**", ""]
    # The scaffold declaration goes FIRST, before any number the reader might
    # take for a project fact (owner's ruling R3, 2026-09-01). generate_wbs is
    # a deterministic template scheduler: it never reads the BOQ, the drawings
    # or the contract. On the 13b2bf7 gate battery its output was read as the
    # project's own structure, so the answer now says what it is at the top
    # rather than leaving it to be inferred from an assumptions list nobody
    # reaches. Rendered from the tool's own `scaffold` record -- the model does
    # not write this line.
    scaffold = payload.get("scaffold") if isinstance(payload.get("scaffold"), dict) else {}
    if scaffold.get("declaration"):
        lines.append(f"_{scaffold['declaration']}_")
        lines.append("")
    op = payload.get("operator_milestones") if isinstance(payload.get("operator_milestones"), dict) else {}
    if op.get("time_for_completion_days"):
        lines.append(
            f"Time for Completion of the whole of the Works: "
            f"{op['time_for_completion_days']} calendar days."
        )
    for m in op.get("milestones") or []:
        if not isinstance(m, dict):
            continue
        lines.append(
            f"- Milestone {m.get('id')}: {m.get('name')} — "
            f"{m.get('days')} calendar days"
        )
    if not op.get("milestones"):
        for m in payload.get("target_milestones") or []:
            if not isinstance(m, dict):
                continue
            lines.append(
                f"- {m.get('name') or m.get('id')}: "
                f"{m.get('duration_days') or m.get('days')} days"
            )
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if summary:
        lines.append("")
        lines.append(
            f"Template activities: {payload.get('actual_count') or summary.get('activity_count') or 0}. "
            f"Project type: {payload.get('project_type') or '—'}."
        )
    if payload.get("assumptions"):
        lines.append("")
        for a in payload.get("assumptions") or []:
            lines.append(f"- {a}")
    out: list[str] = []
    for ln in lines:
        if not ln.strip() and out and not out[-1].strip():
            continue  # the declaration block and the summary each add a blank
        out.append(ln)
    return "\n".join(out).strip()


def _format_job_requisition(payload: dict[str, Any]) -> str:
    lines = [
        f"**Job Requisition — {payload.get('jr_number') or 'DRAFT-JR'}**",
        f"**Title:** {payload.get('title') or 'Job requisition'}",
        "",
        "### Scope",
        str(payload.get("scope") or ""),
        "",
    ]
    if payload.get("noc"):
        lines.append(f"**NOC:** {payload['noc']}")
    if payload.get("noc_expiry"):
        lines.append(f"**NOC expiry:** {payload['noc_expiry']}")
    lines.append("")
    lines.append("### Prequalification")
    for row in payload.get("prequalification") or []:
        lines.append(f"- {row}")
    lines.append("")
    lines.append("### Shortlist rubric")
    for row in payload.get("shortlist_rubric") or []:
        if isinstance(row, dict):
            lines.append(f"- {row.get('criterion')} ({row.get('weight')})")
        else:
            lines.append(f"- {row}")
    if payload.get("note"):
        lines.extend(["", str(payload["note"])])
    return "\n".join(lines).strip()


def _format_cash_flow(payload: dict[str, Any]) -> str:
    rows = (
        payload.get("monthly_forecast")
        or payload.get("s_curve_data")
        or []
    )
    params = payload.get("project_parameters") if isinstance(payload.get("project_parameters"), dict) else {}
    contract = params.get("contract_value") or payload.get("contract_value") or ""
    lines = [
        "**Cash-flow S-curve**",
        f"Contract value: {contract}",
        "",
        "| Month | Progress % | Monthly | Cumulative | Advance recovery | Retention | Net |",
        "|---|---|---|---|---|---|---|",
    ]
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {row.get('month') or row.get('period')} | "
                f"{row.get('planned_progress_percent')} | "
                f"{row.get('monthly_value')} | "
                f"{row.get('cumulative_value')} | "
                f"{row.get('advance_recovery')} | "
                f"{row.get('retention_deduction')} | "
                f"{row.get('net_cash_in')} |"
            )
    return "\n".join(lines).strip()


def _format_om_outline(payload: dict[str, Any]) -> str:
    if payload.get("note") and payload.get("sections"):
        lines = [
            f"**{payload.get('title') or 'Operations and Maintenance manual outline'}**",
            "",
        ]
        for sec in payload.get("sections") or []:
            if isinstance(sec, dict):
                lines.append(f"### {sec.get('section') or sec.get('title') or 'Section'}")
                lines.append(str(sec.get("content") or sec.get("body") or ""))
                lines.append("")
            else:
                lines.append(f"- {sec}")
        lines.append(str(payload.get("note") or ""))
        return "\n".join(lines).strip()
    return json.dumps(payload, default=str)[:4000]


def _format_safety_briefing(payload: dict[str, Any]) -> str:
    if payload.get("briefing"):
        return str(payload["briefing"])
    lines = [
        f"**{payload.get('title') or 'Live-haul-road safety briefing'}**",
        "",
        str(payload.get("note") or ""),
        "",
        "### Signage",
        str(payload.get("signage") or "As shown on the diversion / haul-road drawings."),
        "",
        "### Speed control",
        str(payload.get("speed") or "Enforce the posted site speed and the diversion speed limit."),
        "",
        "### Pedestrian crossing",
        str(payload.get("pedestrian") or "Keep public crossings signed and marshalled at the interface."),
    ]
    return "\n".join(lines).strip()


def _format_rfp_draft(payload: dict[str, Any]) -> str:
    lines = [
        f"**{payload.get('title') or 'Request for Proposal'} — {payload.get('rfp_number') or 'DRAFT-RFP'}**",
        "",
        str(payload.get("invitation") or ""),
        "",
        "### Scope of works",
        str(payload.get("scope_of_works") or ""),
        "",
        "### Prequalification",
    ]
    for row in payload.get("prequalification") or []:
        lines.append(f"- {row}")
    lines.append("")
    lines.append("### Evaluation method")
    lines.append(str(payload.get("evaluation_method") or ""))
    lines.append("")
    lines.append("### Key dates")
    lines.append(str(payload.get("key_dates_note") or ""))
    refs = payload.get("references") or []
    if refs:
        lines.append("")
        lines.append("**References:** " + ", ".join(str(r) for r in refs))
    return "\n".join(lines).strip()


def _extract_predispatch_draft(raw_user: str) -> str:
    for marker in (
        "Authoritative draft:",
        "Authoritative result:",
        "Authoritative result (",
    ):
        if marker in raw_user:
            body = raw_user.split(marker, 1)[1]
            for stop in (
                "\nPresent this WIR",
                "\nReport the period buckets",
                "\nPresent this ",
                "\nDo not search again",
                "\nDo not invent",
                "\nDo not re-call",
            ):
                body = body.split(stop, 1)[0]
            body = body.strip()
            if body:
                return body
    return ""


def _recover_answer_from_tool_messages(
    text: str,
    messages: list[dict[str, Any]] | None,
) -> str:
    """If the model hop died after a successful deliverable tool / predispatch,
    surface that draft instead of an empty bubble or a 413 error."""
    if not _text_needs_tool_recovery(text):
        return text
    for m in reversed(messages or []):
        if m.get("role") == "user":
            raw_user = str(m.get("content") or "")
            if "PLATFORM PRE-DISPATCH:" in raw_user[:80]:
                body = _extract_predispatch_draft(raw_user)
                if body:
                    return body
            continue
        if m.get("role") != "tool":
            continue
        raw = m.get("content") or ""
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            # Truncation envelope: {"truncated": true, "preview": "..."}
            continue
        inner = payload
        if payload.get("truncated") and payload.get("preview"):
            try:
                inner = json.loads(payload["preview"])
            except (TypeError, json.JSONDecodeError):
                inner = payload
        if not isinstance(inner, dict):
            continue
        action = inner.get("action")
        if action == "commissioning_checklist_generated":
            return _format_commissioning_tool(inner)
        if action == "wir_form":
            return _format_wir_form(inner)
        if action == "payment_certificate":
            return _format_payment_certificate(inner)
        if action == "as_built_deviation_report":
            return _format_as_built_note(inner)
        if action in {"claim_generated", "claims_builder"}:
            return _format_claim_notice(inner)
        if action == "job_requisition":
            return _format_job_requisition(inner)
        if action in {"rfp_draft", "rfp_management"}:
            return _format_rfp_draft(inner)
        if action == "rfi_generator" or inner.get("rfis"):
            return _format_rfi(inner)
        if action == "cash_flow_forecast" or inner.get("monthly_forecast"):
            return _format_cash_flow(inner)
        if action in {"om_manual_generated", "om_manual_generator"}:
            return _format_om_outline(inner)
        if action == "safety_briefing" or inner.get("briefing"):
            return _format_safety_briefing(inner)
        if action == "resource_histogram_generated":
            kind = inner.get("histogram_kind") or "labor"
            periods = inner.get("periods") or []
            note = inner.get("note") or ""
            lines = [
                f"Resource / activity histogram ({kind}).",
                note,
                "",
            ]
            for p in periods:
                if not isinstance(p, dict):
                    continue
                lines.append(
                    f"- {p.get('label', '?')}: {p.get('total', 0)} "
                    f"{'activities' if kind == 'activity_count' else 'units'}"
                )
            return "\n".join(lines).strip()
        if inner.get("wbs_id") or inner.get("operator_milestones") or action == "generate_wbs":
            return _format_wbs_result(inner)
        if inner.get("planned_m3") is not None and inner.get("poured_m3") is not None:
            return _format_as_built_note(inner)
    return text


def _construction_calc_tool_schema() -> dict[str, Any]:
    """Schema for the `construction_calc` deterministic-calculator tool. The
    calculation enum is built from the live registry so it never drifts from the
    library. For cost build-ups the model is told to pass real rates from RAG;
    the library's defaults are indicative fallbacks only (keeps cost grounded)."""
    from app.lib import construction_formulas as _cf
    return {
        "type": "function",
        "function": {
            "name": "construction_calc",
            "description": (
                "Run a DETERMINISTIC construction engineering or cost calculation "
                "(deep foundations, concrete technology, structural, crane planning, "
                "cost build-up, MEP, QC). Returns the computed result. For COST "
                "build-ups, pass the project's real unit rates in `params` (from the "
                "priced BOQ / rate schedule) when available — the built-in defaults "
                "are indicative GCC fallbacks only. Deterministic: call once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "calculation": {
                        "type": "string",
                        "enum": _cf.available_calculations(),
                        "description": "Which calculator to run.",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Keyword arguments for the chosen calculation (e.g. "
                            "cost_buildup_rebar needs quantity_kg and optionally "
                            "material_price_sar_t / labour_rate_sar_hr / ...). On a bad "
                            "call the tool returns the exact required signature."
                        ),
                    },
                },
                "required": ["calculation"],
            },
        },
    }


def _cg_to_number(s: Any) -> float | None:
    try:
        return round(float(str(s).replace(",", "").strip()), 2)
    except (ValueError, TypeError):
        return None


def _cg_money_values(text: str) -> list[tuple[str, float]]:
    """Every money/rate-shaped figure in ``text`` as (fragment, value).

    Folds a trailing magnitude word (million/billion/k) for CURRENCY money forms
    so "SAR 1.25 million" -> 1_250_000 and matches a tool's raw 1250000 instead
    of being false-refused. Group 3 is the rate-unit form ("1.25 /m3"), which
    never carries a magnitude word, so it is left as-is."""
    text = text or ""
    out: list[tuple[str, float]] = []
    for m in _CG_MONEY_RE.finditer(text):
        raw = next((g for g in m.groups() if g), None)
        v = _cg_to_number(raw)
        if v is None or v == 0:
            continue
        frag = m.group(0).strip()
        if m.group(3) is None:  # currency money form (not a rate unit)
            suf = _CG_SUFFIX_RE.match(text, m.end())
            if suf:
                v = round(v * _CG_MAGNITUDE[suf.group(1).lower()], 2)
                frag = text[m.start():suf.end()].strip()
        out.append((frag, v))
    return out


_CG_SMALL = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_CG_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_CG_NUMBER_WORDS = set(_CG_SMALL) | set(_CG_SCALES) | {"and"}


def _cg_words_to_int(words: list[str]) -> float | None:
    """Convert a run of English number words to an int (four million → 4e6)."""
    if not words:
        return None
    total = 0
    current = 0
    saw = False
    for w in words:
        if w == "and":
            continue
        if w in _CG_SMALL:
            current += _CG_SMALL[w]
            saw = True
        elif w == "hundred":
            current = (current or 1) * 100
            saw = True
        elif w in _CG_SCALES:
            current = (current or 1) * _CG_SCALES[w]
            total += current
            current = 0
            saw = True
        else:
            return None
    if not saw:
        return None
    return float(total + current)


def _cg_english_and_percent_values(text: str) -> list[float]:
    """Number-words and 'N percent of X' from a user message.

    Live leftover-hat L3: 'ten percent of four million eight hundred thousand'
    produced a 480,000 LD cap the cost gate refused because the user never
    typed digits. Word phrases and percent-of products are the user's own
    figures — same authority as SAR 4.2M in the prompt.
    """
    if not text:
        return []
    tokens = re.findall(r"[a-z]+|%|\d[\d,]*(?:\.\d+)?", text.lower())
    out: list[float] = []
    i = 0
    while i < len(tokens):
        run: list[str] = []
        j = i
        while j < len(tokens) and tokens[j] in _CG_NUMBER_WORDS:
            run.append(tokens[j])
            j += 1
        parsed = _cg_words_to_int(run) if run else None
        if parsed is not None:
            out.append(parsed)
            i = j
            continue
        i += 1
    # "ten percent of four million…" / "10% of 4800000". Restrict both
    # sides to digits or number-words so "LD cap is ten percent of …"
    # does not swallow "ld cap is" into the percent (leftover-hat L3).
    _word = (
        r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
        r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
        r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
        r"hundred|thousand|million|billion|and)"
    )
    for m in re.finditer(
        rf"(?P<pct>{_CG_NUM}|{_word}(?:\s+{_word}){{0,8}})\s*"
        r"(?:percent|per\s+cent|%)\s+of\s+"
        rf"(?P<base>{_CG_NUM}|{_word}(?:\s+{_word}){{0,10}})",
        text.lower(),
    ):
        pct_raw, base_raw = m.group("pct"), m.group("base")
        pct = _cg_to_number(pct_raw)
        if pct is None:
            pct = _cg_words_to_int(re.findall(r"[a-z]+", pct_raw))
        base = _cg_to_number(base_raw)
        if base is None:
            base = _cg_words_to_int(re.findall(r"[a-z]+", base_raw))
        if pct is not None and base is not None:
            out.append(round(base * (pct / 100.0), 4))
    return out


def _cg_grounded_numbers(rag_context: str, messages: list[dict[str, Any]]) -> set:
    """Numbers a cost claim may be grounded against:

    * RAG context, classified PER CHUNK — in a rate-semantic chunk every number
      grounds (rate tables split figure/unit across columns); in a non-rate
      chunk only figures adjacent to currency/rate-unit ground. Per-chunk is
      essential: a rate keyword in one chunk must NOT ground a bare number in a
      drawing chunk.
    * Tool-result messages this turn — a computed deliverable (e.g. a real
      cost_estimate) is authoritative, so all of its numbers ground.
    """
    grounded: set = set()

    def _add_numbers(text: str, all_numbers: bool) -> None:
        # Bare numbers (only in rate-semantic / authoritative text)...
        if all_numbers:
            for tok in _CG_NUM_RE.findall(text):
                v = _cg_to_number(tok)
                if v is not None:
                    grounded.add(v)
        # ...plus magnitude-FOLDED money values, so a chunk/tool that says
        # "SAR 1.25 million" grounds an answer that repeats it (Codex #226 —
        # symmetric with the answer-side fold in _cg_money_values).
        for _frag, v in _cg_money_values(text):
            grounded.add(v)

    # RAG context, per injected chunk (marker from format_chunks_as_system_message).
    for block in re.split(r"\[doc_id=", rag_context or ""):
        if not block.strip():
            continue
        _add_numbers(block, all_numbers=bool(_CG_RATE_SEMANTIC_RE.search(block)))
    # Tool results this turn (authoritative computed numbers) AND the user's
    # own message. A figure the USER supplied ("variance: planned SAR 4.2M vs
    # actual SAR 5.1M") is definitionally traceable — the gate must not refuse
    # to echo the user's own numbers back (the sympy/variance mis-fire). Only
    # numbers actually present in the user's text ground; a fabricated rate the
    # user never mentioned still fails the gate.
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role in ("tool", "user") and isinstance(content, str):
            _add_numbers(content, all_numbers=True)
            for v in _cg_english_and_percent_values(content):
                grounded.add(v)
    # Simple arithmetic derivations of grounded figures also ground: a
    # variance/overrun answer legitimately computes the SUM or DIFFERENCE of
    # two grounded totals (SAR 5.1M - SAR 4.2M = SAR 0.9M), and a bill line
    # legitimately computes the PRODUCT of quantity and rate -- the extension
    # qty x rate is the QS's most basic operation, and unit-rate back-outs
    # (amount / qty) are its inverse. Live find 2026-08-15 (F25): "extend 100
    # square metres at the operator-instructed 250 riyals" was refused because
    # 25,000 traced to nothing -- the closure knew sums and differences but
    # not products. Bounded to pairwise combinations of the (small) grounded
    # set; the 0.5% tolerance in _cg_is_grounded keeps this from grounding an
    # unrelated fabricated rate.
    base = list(grounded)
    for i in range(len(base)):
        for j in range(i, len(base)):
            grounded.add(round(base[i] + base[j], 4))
            grounded.add(round(abs(base[i] - base[j]), 4))
            grounded.add(round(base[i] * base[j], 4))
            if base[j]:
                grounded.add(round(base[i] / base[j], 4))
            if base[i]:
                grounded.add(round(base[j] / base[i], 4))
    return grounded


def _cg_is_grounded(value: float, grounded: set) -> bool:
    tol = max(0.5, abs(value) * 0.005)
    return any(abs(value - g) <= tol for g in grounded)


def _cost_grounding_gate(
    text: str,
    rag_sys_msg: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> str:
    """Refuse a cost/rate answer whose figures don't trace to a rate-semantic
    retrieved chunk or a computed tool result. Non-cost answers pass untouched.
    Never raises."""
    try:
        if not _cost_grounding_enabled():
            return text
        figs = _cg_money_values(text)
        if not figs:
            return text  # not a cost/rate answer — leave it alone
        if _is_document_deliverable_request(_latest_user_text(messages)):
            return text
        rag_context = (rag_sys_msg or {}).get("content", "") if rag_sys_msg else ""
        grounded = _cg_grounded_numbers(rag_context, messages)
        if all(_cg_is_grounded(v, grounded) for _, v in figs):
            return text
        _LOG.warning(
            "cost_grounding_gate: refused ungrounded cost figure(s) %s",
            [f for f, _ in figs],
        )
        return _CG_REFUSAL
    except Exception:
        _LOG.exception("cost_grounding_gate failed; passing answer through")
        return text


def format_excerpts_as_rag_context(
    excerpts: list[dict[str, Any]] | None,
) -> str:
    """Render project-document excerpts into the SAME per-chunk marker form the
    agent path's ``format_chunks_as_system_message`` emits, so the cost-grounding
    gate classifies each excerpt for rate-semantics INDEPENDENTLY.

    Per-chunk markers are load-bearing: a single flat block that mixed a rate
    table with a drawing dimension-table chunk would read as rate-semantic as a
    whole and wrongly ground the drawing's bare numbers (the 2026-07-14 "450
    SAR/m³" incident). Emitting one ``[doc_id=... chunk=N ...]`` block per
    excerpt keeps grounding on the non-agent paths identical to the agent path.

    Excerpt shape matches ``search_project_documents`` /
    ``_with_doc_search`` output (``snippet`` / ``filename`` / ``document_id`` /
    ``score``). Returns "" when there are no usable excerpts."""
    blocks: list[str] = []
    for i, e in enumerate(excerpts or []):
        if not isinstance(e, dict):
            continue
        text = (e.get("snippet") or "").strip()
        if not text:
            continue
        did = e.get("document_id") or e.get("filename") or f"doc{i}"
        score = e.get("score")
        score_s = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
        blocks.append(f"[doc_id={did} chunk={i}{score_s}] {text}")
    return "\n\n".join(blocks)


def gate_cost_answer(
    text: str,
    *,
    rag_context: str = "",
    authoritative_texts: list[str] | None = None,
    user_message: str | None = None,
) -> str:
    """Run the tested cost-grounding gate over an answer produced by a NON-agent
    answer path (``/chat``, ``/v1/project/ask``) that doesn't itself build the
    agent's ``(rag_sys_msg, messages)`` pair.

    * ``rag_context`` — retrieved evidence in the per-chunk ``[doc_id=...]``
      marker form (see ``format_excerpts_as_rag_context``); classified per chunk
      for rate-semantics, exactly like the agent path.
    * ``authoritative_texts`` — computed/tool results produced this turn (e.g.
      the project reasoner's executed step outputs). Every number in them
      grounds — the same authority the agent path grants tool-result messages.
    * ``user_message`` — the caller's own question; figures the user supplied are
      grounded, so the gate never refuses to echo the user's own numbers back.

    Delegates to the existing ``_cost_grounding_gate`` — NO gating logic is
    reimplemented here. Never raises: a gate must never break a working turn."""
    try:
        rag_sys_msg = (
            {"role": "system", "content": rag_context} if rag_context else None
        )
        messages: list[dict[str, Any]] = []
        if user_message:
            messages.append({"role": "user", "content": user_message})
        for t in authoritative_texts or []:
            if t:
                messages.append({"role": "tool", "content": str(t)})
        return _cost_grounding_gate(text, rag_sys_msg, messages)
    except Exception:  # noqa: BLE001 — a gate must never break an answer
        _LOG.exception("gate_cost_answer failed; passing answer through")
        return text


# ── Standards advisory (ADVISORY, never blocks) ─────────────────────────────
# Highlights a deviation from a critical construction standard (e.g. 'APPROVED'
# used on a design document, PRC-501) by APPENDING a note — it never rejects,
# edits, or halts the answer. Operators bend rules deliberately in the field; the
# platform's job is to flag the deviation so the choice is informed, not to
# enforce a stop. Flag STANDARDS_ADVISORY (default on); never raises. This is the
# advisory wiring for the previously-dormant construction_knowledge enforcement.
def _standards_advisory_enabled() -> bool:
    return os.getenv("STANDARDS_ADVISORY", "1") not in ("0", "false", "False", "")


def _standards_advisory(text: str) -> str:
    try:
        if not _standards_advisory_enabled() or not text or not text.strip():
            return text
        from app.core.construction_knowledge import enforce_critical_rules
        violations = enforce_critical_rules(text)
        if not violations:
            return text
        seen: set = set()
        lines: list[str] = []
        for v in violations:
            rid = v.get("rule_id")
            if rid in seen:
                continue
            seen.add(rid)
            proc = v.get("procedure", "")
            msg = v.get("violation_message") or v.get("rule", "")
            lines.append(f"> - **{proc}** — {msg}" if proc else f"> - {msg}")
        if not lines:
            return text
        return text + (
            "\n\n> **Standards note (advisory — flagging, not blocking):**\n"
            + "\n".join(lines)
        )
    except Exception:
        _LOG.exception("standards advisory failed; passing answer through")
        return text


# STEP 0b — visible disclosure prepended to any answer that fell back to the
# Master Corpus because the active project has no usable documents of its own.
# The sources panel is separately tagged (see _build_sources_from_audit); this
# is the in-answer half of "fallback VISIBLE in response + sources panel."
_MASTER_CORPUS_FALLBACK_NOTE = (
    "_This project has no documents of its own for this question — "
    "answering from the Master Corpus._\n\n"
)


def _postprocess_answer(
    text: str,
    rag_sys_msg: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    fallback_used: bool = False,
    agent_name: str | None = None,
) -> str:
    """Final-answer post-processing: cost-grounding gate (may refuse an
    ungrounded cost claim) then the standards advisory (appends a non-blocking
    deviation note). Order matters — don't annotate a refusal for cost figures
    it no longer contains.

    When ``fallback_used`` is set (the retriever answered from the Master Corpus
    because the project is empty/thin), a one-line disclosure banner is
    prepended so the fallback is visible in the answer itself."""
    text = _recover_answer_from_tool_messages(text, messages)
    text = _graft_operator_claim_facts(text, _operator_user_text(messages))
    text = _cost_grounding_gate(text, rag_sys_msg, messages)
    # Citation provenance: an attribution no evidence record backs is removed
    # and the answer flagged. Sibling of the cost gate above -- that one
    # grounds the FIGURES, this one grounds the claim about where they came
    # from (gate battery 13b2bf7 F2: "BOQ context: ... (DD-2022-175)" from a
    # template scheduler that has no BOQ input at all).
    from app.agents.citation_provenance import gate as _citation_provenance_gate
    text = _citation_provenance_gate(text, rag_sys_msg, messages)
    text = _standards_advisory(text)
    text = _ensure_ingestion_handoff(text, messages, agent_name)
    # Confidentiality stopgap: scrub known project/client names from the final
    # answer so one client's project identity can't leak via general-knowledge
    # retrieval. Runs LAST so it catches names in any appended note too.
    from app.core.identifier_scrub import scrub_identifiers
    text = scrub_identifiers(text)
    # Disclosure banner goes on AFTER the scrub so it's never mangled, and only
    # when the banner isn't already present (idempotent across retries).
    if fallback_used and _MASTER_CORPUS_FALLBACK_NOTE.strip() not in text:
        text = _MASTER_CORPUS_FALLBACK_NOTE + text
    return text


_INGEST_NEXT_RE = re.compile(r"(?im)^Next:\s*\S+")
_EXT_TO_NEXT_AGENT = (
    (".ifc", "bim-analyst"),
    (".xer", "construction-pm"),
    (".xlsx", "quantity-surveyor"),
    (".csv", "quantity-surveyor"),
    (".docx", "contracts-manager"),
    (".doc", "contracts-manager"),
    (".pdf", "quantity-surveyor"),
)


def _next_agent_from_turn(messages: list[dict[str, Any]] | None) -> str:
    """Pick the leftover L5 handoff hat from the user's named file/intent."""
    text = ""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            text = str(m.get("content") or "")
            break
    low = text.lower()
    for ext, agent in _EXT_TO_NEXT_AGENT:
        if ext in low:
            return agent
    if "bim" in low or "ifc" in low:
        return "bim-analyst"
    if "boq" in low:
        return "quantity-surveyor"
    return "smart-orchestrator"


def _ensure_ingestion_handoff(
    text: str,
    messages: list[dict[str, Any]] | None,
    agent_name: str | None,
) -> str:
    """Leftover L5: document-ingestion must end with ``Next: <hat>``."""
    if (agent_name or "") != "document-ingestion":
        return text
    if not (text or "").strip():
        return text
    if _INGEST_NEXT_RE.search(text):
        return text
    return text.rstrip() + f"\n\nNext: {_next_agent_from_turn(messages)}"


def _parse_source_tail(tail: str) -> tuple[str, str]:
    """Split a source mention into filename and optional chunk-number blob.

    Examples:
      "File.xlsx, chunk 8"        -> ("File.xlsx", "8")
      "File.xlsx, chunks 1, 2"    -> ("File.xlsx", "1, 2")
      "File.xlsx, which says..."  -> ("File.xlsx", "")
      "File.xlsx"                 -> ("File.xlsx", "")
    """
    tail = tail.strip()
    # Try to peel a trailing ", chunk(s) ..." suffix.
    chunk_suffix = re.search(r",\s*chunks?\s+([\d,\s]+)$", tail, re.IGNORECASE)
    if chunk_suffix:
        fname = tail[: chunk_suffix.start()].strip()
        return _strip_source_quotes(fname), chunk_suffix.group(1)
    # No chunk suffix. Trim any trailing prose clause after a comma.
    if "," in tail:
        # Heuristic: the first comma followed by a lowercase/prose word marks
        # the start of a clause (", which lists..."). Keep only the filename.
        idx = tail.find(",")
        rest = tail[idx + 1 :].lstrip()
        if rest and (rest[0].islower() or rest.startswith("which") or rest.startswith("found") or rest.startswith("see")):
            tail = tail[:idx].strip()
    return _strip_source_quotes(tail), ""


def _extract_cited_chunk_indexes(text: str) -> list[tuple[str, int]]:
    """Pull (filename, chunk_index) pairs from the agent's final text.

    Recognised patterns (case-insensitive):
      [source: filename.pdf, chunk 65]
      [source: filename.pdf, chunks 16, 34, 55]
      Source: filename.pdf, chunk 65.
      Source: “filename.pdf”, which ...
      [doc_id=abc123, chunk 4]

    Returns a list of pairs; chunk_index is -1 when the cite didn't
    include a chunk number.
    """
    if not text:
        return []
    out: list[tuple[str, int]] = []

    for m in _CITATION_RE.finditer(text):
        fname, nums_blob = _parse_source_tail(m.group(1))
        if not nums_blob.strip():
            out.append((fname, -1))
        else:
            for piece in nums_blob.split(","):
                piece = piece.strip()
                if piece.isdigit():
                    out.append((fname, int(piece)))

    for m in _CITATION_LINE_RE.finditer(text):
        fname, nums_blob = _parse_source_tail(m.group(1))
        if not nums_blob.strip():
            out.append((fname, -1))
        else:
            for piece in nums_blob.split(","):
                piece = piece.strip()
                if piece.isdigit():
                    out.append((fname, int(piece)))

    for m in _CITATION_QUOTED_RE.finditer(text):
        fname = m.group(2).strip()
        if fname:
            out.append((fname, -1))

    for m in _CITATION_DOCID_RE.finditer(text):
        doc_id = m.group(1).strip()
        chunk_idx = int(m.group(2))
        out.append((doc_id, chunk_idx))

    # gpt-oss inline-bracketed form: "Source: [filename], chunk(s) N[-M]".
    # The chunk number is the reliable signal (the model can only cite indexes
    # it saw in the injected context); the filename is often ellipsis-truncated
    # and is matched leniently downstream.
    for m in _CITATION_INLINE_BRACKET_RE.finditer(text):
        fname = _strip_source_quotes(m.group(1).strip())
        for num in re.findall(r"\d+", m.group(2)):
            out.append((fname, int(num)))

    for m in _CITATION_SRC_RE.finditer(text):
        fname = _strip_source_quotes(m.group(1).strip().rstrip(".,;)]"))
        if fname:
            out.append((fname, -1))

    return out


def _build_sources_from_audit(
    audit_rec: dict[str, Any],
    final_text: str = "",
) -> list[dict[str, Any]]:
    """Build the SSE end-event sources list.

    Behaviour:
      1. If ``final_text`` contains ``[source: ...]`` citations AND those
         citations match chunks present in the audit record's injected
         set, return ONLY those — they are what the agent actually
         cited. The right-panel Sources tab then shows the operator
         exactly the chunks behind the answer.
      2. Otherwise (no citations parsed, or none match the injected
         chunks), fall back to the top-3 retrieved chunks by score —
         the pre-PR-110 behaviour. Preserves the old contract for the
         qwen-style agents that don't emit ``[source: ...]`` markers.

    Empty list when ``audit_rec`` has no chunks (fallback turn).
    """
    chunks = (audit_rec or {}).get("chunks") or []
    if not chunks:
        return []

    # If the retrieval trust gate fired (identifier miss or confidence
    # threshold), don't fabricate a sources panel from fallback chunks.
    if audit_rec.get("identifier_miss") or audit_rec.get("threshold_fired"):
        return []

    # If the assistant explicitly declined to answer, don't fabricate a
    # sources panel from low-score fallback chunks.
    if _answer_is_caveat(final_text):
        return []

    try:
        from app.core import projects as _projects
    except Exception:
        _projects = None

    def _doc_name(doc_id: str) -> str:
        if not _projects:
            return ""
        try:
            d = _projects.get_document(doc_id) or {}
            return d.get("original_name") or ""
        except Exception:
            return ""

    # A3: a named contract/doc id must not appear beside another year's
    # DD contract in Sources. Fail closed when nothing remaining matches.
    from app.core.rag.retriever import (
        extract_contract_doc_ids,
        filename_matches_named_contracts,
    )
    named_contracts = list((audit_rec or {}).get("extracted_contract_ids") or [])
    if not named_contracts:
        named_contracts = extract_contract_doc_ids(
            (audit_rec or {}).get("user_message_preview") or ""
        )
    # Question-named ids stay authoritative (A3 / #443). When the question
    # did not name a contract but the answer did, scope Sources to that id
    # so the panel cannot list DD-2022 next to prose that cites DD-2023.
    if not named_contracts:
        named_contracts = extract_contract_doc_ids(final_text or "")
    if named_contracts:
        scoped = [
            c for c in chunks
            if filename_matches_named_contracts(
                _doc_name(c.get("doc_id") or ""),
                named_contracts,
            )
        ]
        if not scoped:
            return []
        chunks = scoped

    def _sources_one_contract(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Unnamed contract answers may cite one PREFIX-YEAR-SEQ, not two years."""
        if named_contracts or not sources:
            return sources
        winning: str | None = None
        out: list[dict[str, Any]] = []
        for s in sources:
            cids = extract_contract_doc_ids(s.get("doc_name") or "")
            if not cids:
                out.append(s)
                continue
            if winning is None:
                winning = cids[0]
                out.append(s)
            elif winning in cids:
                out.append(s)
        return out

    # STEP 0b — human-readable layer labels for the sources panel so a
    # Master-Corpus fallback (and disclosed general-knowledge context) is
    # visible per-source, not just in the answer banner.
    _LAYER_LABELS = {
        "own": "Project document",
        "master_corpus": "Master Corpus (fallback)",
        "general_knowledge": "Knowledge base",
    }

    def _format(chunk_meta: dict[str, Any], doc_name: str) -> dict[str, Any]:
        score = chunk_meta.get("score") or 0.0
        conf = "High" if score >= 0.75 else "Medium" if score >= 0.5 else "Low"
        layer = chunk_meta.get("layer") or "own"
        # Layered RAG (Stage 4): a user upload is 'own'-tagged for isolation but
        # should read as "Your upload" so the user sees it came from their own
        # layer, not the authoritative corpus. knowledge_layer is None unless
        # RAG_LAYERED is on, so this label never appears in today's behaviour.
        label = _LAYER_LABELS.get(layer, "Knowledge base")
        if chunk_meta.get("knowledge_layer") == "user_session":
            label = "Your upload"
        # Confidentiality: the answer TEXT is scrubbed in _finalize, but this
        # panel used to ship the original filename verbatim -- a Master-Corpus
        # or knowledge-base chunk named after another client leaked the
        # identity through the Sources tab. The user's own project documents
        # keep their real names.
        display_name = _clean_path_label(doc_name)
        if layer != "own":
            from app.core.identifier_scrub import scrub_identifiers_filename
            display_name = scrub_identifiers_filename(display_name)
        return {
            "doc_id": chunk_meta["doc_id"],
            "doc_name": display_name,
            "page_or_section": f"chunk #{chunk_meta['chunk_index']}",
            "chunk_index": chunk_meta.get("chunk_index"),
            "chunk_id": chunk_meta.get("chunk_id"),
            # Owning project of the cited file (Master Corpus / GK), not
            # only the workspace the chat ran in — preview uses this.
            "project_id": (
                chunk_meta.get("project_id")
                or (audit_rec or {}).get("project_id")
            ),
            "score": float(score),
            "confidence": conf,
            "layer": layer,
            "layer_label": label,
        }

    # 1) Try to extract citations from the agent's text first.
    cites = _extract_cited_chunk_indexes(final_text)
    if cites:
        matched: list[dict[str, Any]] = []
        seen: set = set()
        # Set of injected doc_ids for the doc-id-keyed citation branch.
        injected_doc_ids = {c.get("doc_id") for c in chunks}

        for cited_token, cited_idx in cites:
            cited_token_n = _normalise_filename(cited_token)
            # Branch A: doc-id-keyed cite. ``cited_token`` matches one of
            # the audit's doc_ids directly (gpt-oss [doc_id=X chunk=N]
            # form). Use that as the primary match.
            doc_id_match = cited_token if cited_token in injected_doc_ids else None

            for c in chunks:
                cidx = c.get("chunk_index")
                doc_id = c.get("doc_id")
                # chunk-index match (when provided) is the primary key.
                if cited_idx != -1 and cidx != cited_idx:
                    continue
                # If the cite was doc-id-keyed, require doc_id match.
                if doc_id_match is not None:
                    if doc_id != doc_id_match:
                        continue
                elif cited_idx == -1:
                    # Filename-keyed cite with NO chunk number to disambiguate
                    # — require a filename suffix-match (normalized) so a model
                    # that rewrote the dash style still resolves. A PREFIX-
                    # YEAR-SEQ in the cite also matches that contract's file.
                    name = _doc_name(doc_id)
                    name_n = _normalise_filename(name)
                    token_cids = extract_contract_doc_ids(cited_token)
                    if token_cids and filename_matches_named_contracts(
                        name, token_cids,
                    ):
                        pass
                    elif (
                        cited_token_n
                        and name_n
                        and cited_token_n not in name_n
                        and name_n not in cited_token_n
                    ):
                        continue
                # else: the cite carried a chunk number that already matched
                # above (cited_idx != -1, cidx == cited_idx). That index
                # uniquely identifies the injected chunk, so trust it even when
                # the model truncated the filename (gpt-oss "Site … Part 3").
                key = (doc_id, cidx)
                if key in seen:
                    continue
                seen.add(key)
                matched.append(_format(c, _doc_name(doc_id)))
        if matched:
            return _sources_one_contract(matched)

    # 2) Filename-mention fallback: the model may have named a source in
    #    prose without a formal citation marker. If any injected filename
    #    appears in the answer, surface the highest-scoring chunk of that
    #    document.
    if final_text:
        text_n = _normalise_filename(final_text)
        mention_hits: list[dict[str, Any]] = []
        seen_mentions: set = set()
        for c in sorted(chunks, key=lambda x: -(x.get("score") or 0)):
            doc_id = c.get("doc_id")
            name = _doc_name(doc_id)
            name_n = _normalise_filename(name)
            name_cids = extract_contract_doc_ids(name)
            mentioned = bool(name_n and name_n in text_n)
            if not mentioned and name_cids:
                mentioned = any(cid in text_n for cid in name_cids)
            if mentioned and doc_id not in seen_mentions:
                seen_mentions.add(doc_id)
                mention_hits.append(_format(c, name))
        if mention_hits:
            return _sources_one_contract(mention_hits[:3])

    # 3) Final fallback: top-3 retrieved chunks by score.
    by_score = sorted(chunks, key=lambda c: -(c.get("score") or 0))[:3]
    return _sources_one_contract(
        [_format(c, _doc_name(c["doc_id"])) for c in by_score]
    )


# File extensions whose BOQs are safe to turn into a cost workbook on click.
# Digital grids only: a scanned PDF BOQ won't parse and OOMs the 2 GB box
# (memory the-fork-boq-always-xlsx), so PDFs are deliberately excluded here.
_BOQ_COST_EXPORT_EXTS = (".xlsx", ".csv")
_BOQ_NAME_HINTS = ("boq", "bill of quantities", "priced", "tender")


def _is_cost_boq_source(doc: dict[str, Any]) -> bool:
    """Cheap, metadata-only test for 'this cited doc can back a cost-BOQ
    export'. NEVER runs boq_processor — gating on name/type/extension keeps
    the SSE end path off the OOM-prone parse. The real parse happens on click,
    server-side, where it's bounded (and 422s honestly if the grid is empty)."""
    name = (doc.get("original_name") or "").lower()
    if not name.endswith(_BOQ_COST_EXPORT_EXTS):
        return False
    doc_type = (doc.get("doc_type") or "").lower()
    return doc_type == "boq" or any(h in name for h in _BOQ_NAME_HINTS)


# RFP/BOD/spec documents document_engine can mine for lead times + milestones.
# Text formats only (docx/pdf/txt) — spreadsheets carry no procurement
# narrative, and a scanned-BOQ pdf is the OOM path we avoid elsewhere.
_SPEC_EXPORT_EXTS = (".pdf", ".docx", ".doc", ".txt", ".md")
_SPEC_NAME_HINTS = ("rfp", "request for proposal", "bod", "basis of design",
                    "spec", "requirement", "scope of work", "tender",
                    "employer requirement", "design brief")


def _is_spec_or_rfp_source(doc: dict[str, Any]) -> bool:
    """Metadata-only test for 'this cited doc is an RFP/BOD/spec that
    document_engine can extract equipment lead times + target milestones from',
    to drive a document-aware schedule offer. The extract runs on click,
    server-side, bounded — this only gates the offer."""
    name = (doc.get("original_name") or "").lower()
    if not name.endswith(_SPEC_EXPORT_EXTS):
        return False
    doc_type = (doc.get("doc_type") or "").lower()
    if doc_type in ("spec", "rfp", "bod", "design", "requirements"):
        return True
    return any(h in name for h in _SPEC_NAME_HINTS)


def _build_exports_from_audit(
    audit_rec: dict[str, Any],
    final_text: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """SSE end-event `exports` list — data-backed download offers for the bubble.

    Each descriptor is something the platform can actually produce (not from
    routing intent — an offer that 422s on click is worse than no offer):
      - Cost BOQ: from a document the answer CITED (endpoint self-derives from
        the document_id).
      - Schedule: when the turn ran `generate_wbs`, offer the cost-loaded
        workbook. The full activity list is stripped from the tool result to
        keep the SSE payload small, so the offer carries the brief params and
        the endpoint re-derives them (generate_wbs is deterministic).

    Returns [] when the turn produced nothing exportable. Deduplicated.
    """
    project_id = (audit_rec or {}).get("project_id")
    if not project_id:
        return []
    try:
        from app.core import projects as _projects
    except Exception:
        _projects = None

    exports: list[dict[str, Any]] = []
    sources = _build_sources_from_audit(audit_rec, final_text)

    # ── Cost BOQ offers (from cited documents) ──────────────────────────────
    if sources and _projects is not None:
        seen: set = set()
        for s in sources:
            doc_id = s.get("doc_id")
            if not doc_id or doc_id in seen:
                continue
            try:
                doc = _projects.get_document(doc_id) or {}
            except Exception:
                continue
            if not _is_cost_boq_source(doc):
                continue
            seen.add(doc_id)
            exports.append({
                "label": "Cost BOQ (Excel)",
                "format": "xlsx",
                "method": "POST",
                "endpoint": f"/v1/projects/{project_id}/export/cost-boq",
                "payload": {"document_id": doc_id, "currency": "SAR"},
            })

    # RFP/BOD/spec docs cited this turn → drive the schedule from THEIR real
    # extracted lead times (document_engine) rather than a generic template.
    # Only spec-type sources qualify, capped, and only digital formats — running
    # document_engine on a scanned BOQ is exactly the OOM path we avoid.
    rfp_doc_ids: list[str] = []
    if sources and _projects is not None:
        seen_rfp: set = set()
        for s in sources:
            did = s.get("doc_id")
            if not did or did in seen_rfp:
                continue
            try:
                doc = _projects.get_document(did) or {}
            except Exception:
                continue
            if _is_spec_or_rfp_source(doc):
                seen_rfp.add(did)
                rfp_doc_ids.append(did)
        rfp_doc_ids = rfp_doc_ids[:3]

    # ── Schedule offer (from a generate_wbs tool call this turn) ─────────────
    for tc in reversed(tool_calls or []):
        if not isinstance(tc, dict) or tc.get("name") != "generate_wbs":
            continue
        res = tc.get("result")
        if not (isinstance(res, dict) and res.get("status") == "success"):
            continue
        total = res.get("activities_total") or res.get("actual_count") or 0
        payload: dict[str, Any] = {
            "brief": res.get("brief") or "",
            "target_count": res.get("target_count") or 200,
            "currency": "SAR",
        }
        if res.get("project_type"):
            payload["project_type"] = res["project_type"]
        if res.get("start_date"):
            payload["start_date"] = res["start_date"]
        if rfp_doc_ids:
            # Document-driven: real lead times + milestones from the cited RFP.
            payload["document_ids"] = rfp_doc_ids
            label = (f"Schedule from documents — {total} activities (Excel)"
                     if total else "Schedule from documents (Excel)")
            endpoint = f"/v1/projects/{project_id}/export/schedule-from-document"
        else:
            label = (f"Schedule — {total} activities (Excel)"
                     if total else "Schedule (Excel)")
            endpoint = f"/v1/projects/{project_id}/export/schedule-from-brief"
        exports.append({
            "label": label, "format": "xlsx", "method": "POST",
            "endpoint": endpoint, "payload": payload,
        })
        break  # one schedule offer per turn (latest WBS)
    return exports



# Groq provides an OpenAI-compatible chat-completions endpoint, so the only
# things that differ from DeepSeek are the base URL, the env-var name, and the
# default model id. Tool-calling payload shape is identical.
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile / llama-3.1-8b-instant on 2026-08-16
# (free/developer tier). Live M9/M10/M13 then 404'd every Groq hop.
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_RETIRED_MODELS = {
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
    "llama-3.1-8b-instant": "openai/gpt-oss-20b",
}

# Kimi / Moonshot native API — OpenAI-compatible chat-completions. Same payload
# shape as Groq/DeepSeek (verified: tool-calling + streaming accepted). K2 models
# (kimi-k2.6 default) are reasoning models: the response carries a separate
# `reasoning_content` chain-of-thought and the real answer in `content`; we read
# `content` and the outbound sanitiser strips `reasoning_content` on replay.
KIMI_API_URL = "https://api.moonshot.ai/v1/chat/completions"
KIMI_DEFAULT_MODEL = "kimi-k2.6"


def _llm_http_timeout() -> float:
    """Per-call HTTP timeout for an LLM request (seconds).

    Kimi k2.6 is a REASONING model: on a large grounded turn (~12k-token
    context) its first token can land well past 120s. The old hardcoded
    120s httpx timeout gave up on Kimi prematurely and fell back to Groq —
    whose free tier then 413'd the same large payload, so the turn produced
    NOTHING (the "Kimi hiccup"). Give the primary room to finish, capped
    below the CHAT_STREAM deadline so the stream still ends cleanly.
    Default 200s (stream deadline default is 240s). Override with
    LLM_HTTP_TIMEOUT_SECONDS.
    """
    try:
        return float(os.getenv("LLM_HTTP_TIMEOUT_SECONDS", "200"))
    except ValueError:
        return 200.0


# Floor for a single LLM attempt. Below this there is no point starting one:
# it cannot return before the turn's wall clock cuts the stream, and the
# attempt only burns the budget that a clean fallback answer needs.
_MIN_LLM_ATTEMPT_SECONDS = 5.0

# Head-room reserved between the LAST LLM attempt's deadline and the turn's
# wall clock, so a call that gives up still leaves the producer time to
# sanitize, persist and stream the fallback text. Without it the LLM deadline
# and the stream deadline are the same instant and the fallback races the
# cancellation it was meant to replace.
_STREAM_DELIVERY_MARGIN_SECONDS = 10.0


def _forced_retry_min_seconds() -> float:
    """Wall clock the forced no-tools retry needs before it is worth starting.

    The retry is a full LLM call on a reasoning model; observed primary calls
    on this service run 100-150s. Starting one with less than this left means
    the turn dies mid-await and the user gets a timeout banner instead of the
    fallback text we could have delivered immediately. Override with
    FORCED_RETRY_MIN_SECONDS.
    """
    try:
        return float(os.getenv("FORCED_RETRY_MIN_SECONDS", "45"))
    except ValueError:
        return 45.0

# OpenAI native API — standard chat-completions endpoint. Tool-calling and
# streaming are first-class; we keep the same payload shape as Groq/DeepSeek.

# Ollama exposes an OpenAI-compatible endpoint at /v1/chat/completions
# (v0.1.31+). Self-hosted on the operator's PC or a VPS. No auth, no token
# cost, no TPM rate limits — bounded by local hardware. Used when the
# operator wants to escape cloud rate limits entirely.
OLLAMA_DEFAULT_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_DEFAULT_MODEL = "qwen2.5:7b-instruct"


def _llm_config() -> dict[str, Any]:
    """Pick the active LLM provider's URL + env-key + default model.

    Precedence:
      1. Explicit ``LLM_PROVIDER`` env var (``kimi`` | ``groq`` | ``ollama``)
         wins.
      2. Otherwise: Kimi when a KIMI_API_KEY is set, else Groq when a
         GROQ_API_KEY is set, else Kimi (the documented primary).
    OpenAI and DeepSeek were removed 2026-07-25 — the cloud ladder is Kimi
    primary + Groq fallback; Ollama is the on-prem provider.

    Per-provider override envs let the operator pin a specific model
    without code changes:
      - ``GROQ_MODEL`` / ``KIMI_MODEL`` / ``OLLAMA_MODEL``
      - ``OLLAMA_URL`` overrides the localhost default — set this to your
        Cloudflare Tunnel / Tailscale / VPS URL so the Render deploy can
        reach your self-hosted Ollama.

    Ollama can run in two modes:
      * Self-hosted (localhost / your tunnel) — no auth required.
        ``env_key`` is empty so the caller skips the Bearer header.
      * Ollama Cloud (https://ollama.com) — Bearer-token auth via
        ``OLLAMA_API_KEY``. When that env var is set we expose env_key
        so the downstream auth path adds the header just like Groq
        or DeepSeek.
    """
    # The ladder is Kimi (primary) + Groq (the one cloud fallback) + Ollama
    # (on-prem only). OpenAI and DeepSeek were removed 2026-07-25. An unset or
    # unrecognized LLM_PROVIDER resolves to Kimi (the documented primary), with
    # Groq as the auto-pick only when a Kimi key is absent but a Groq key exists.
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "kimi" if os.getenv("KIMI_API_KEY") else (
            "groq" if os.getenv("GROQ_API_KEY") else "kimi")
    if provider == "ollama":
        url = os.getenv("OLLAMA_URL", OLLAMA_DEFAULT_URL).rstrip("/")
        # Native Ollama protocol (/api/chat) is explicit — leave it alone.
        # Otherwise accept both the bare host (http://host:11434) and the
        # full OAI-shape path; append the canonical suffix when missing.
        if not url.endswith("/api/chat"):
            if not url.endswith("/v1/chat/completions"):
                if url.endswith("/v1"):
                    url = url + "/chat/completions"
                elif "/v1/" not in url:
                    url = url + "/v1/chat/completions"
        return {
            "provider": "ollama",
            "url": url,
            "env_key": "OLLAMA_API_KEY" if os.getenv("OLLAMA_API_KEY") else "",
            "default_model": os.getenv("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL),
        }
    if provider == "groq":
        return {
            "provider": "groq",
            "url": GROQ_API_URL,
            "env_key": "GROQ_API_KEY",
            "default_model": _resolve_groq_model(os.getenv("GROQ_MODEL")),
        }
    if provider == "kimi":
        return {
            "provider": "kimi",
            "url": KIMI_API_URL,
            "env_key": "KIMI_API_KEY",
            "default_model": os.getenv("KIMI_MODEL", KIMI_DEFAULT_MODEL),
            # Moonshot's K2 reasoning models reject any temperature but 1
            # ("invalid temperature: only 1 is allowed for this model"). Declare
            # that constraint HERE so the outbound payload is shaped per-provider
            # (see _provider_temperature) rather than hardcoding 1 globally — a
            # global constant would just become the next provider's 400.
            "fixed_temperature": 1,
            # K2 spends the SHARED max_tokens budget on chain-of-thought before
            # any content. Measured with the validation agent's own prompt:
            # max_tokens=1024 -> finish=length, content=0c, reasoning=3940c.
            # Any cap below the reasoning burn makes content structurally
            # impossible — the empty-final retry then fails the same way and
            # the user sees the canned "unable to generate" message. Floor it
            # here (see _provider_max_tokens), like fixed_temperature.
            "reasoning_min_tokens": int(os.getenv("KIMI_REASONING_MIN_TOKENS", "4096")),
        }
    # Default / fallthrough = Kimi (primary). OpenAI and DeepSeek are gone.
    return {
        "provider": "kimi",
        "url": KIMI_API_URL,
        "env_key": "KIMI_API_KEY",
        "default_model": os.getenv("KIMI_MODEL", KIMI_DEFAULT_MODEL),
        "fixed_temperature": 1,
        "reasoning_min_tokens": int(os.getenv("KIMI_REASONING_MIN_TOKENS", "4096")),
    }


def _kimi_model_fallback(primary: dict[str, Any]) -> dict[str, Any] | None:
    """Same-Moonshot-key fast model, or None. No ``fixed_temperature`` so
    moonshot-v1 accepts the caller's temperature."""
    if primary.get("provider") != "kimi":
        return None
    fb_model = (os.getenv("KIMI_FALLBACK_MODEL") or "").strip()
    if not fb_model or fb_model == primary.get("default_model"):
        return None
    return {
        "provider": "kimi",
        "url": KIMI_API_URL,
        "env_key": "KIMI_API_KEY",
        "default_model": fb_model,
    }


def _cross_provider_fallback(primary: dict[str, Any]) -> dict[str, Any] | None:
    """``LLM_FALLBACK_PROVIDER`` config, or None when unset/unusable."""
    name = (os.getenv("LLM_FALLBACK_PROVIDER") or "").strip().lower()
    if not name or name == primary.get("provider"):
        return None
    prev = os.environ.get("LLM_PROVIDER")
    os.environ["LLM_PROVIDER"] = name
    try:
        cfg = _llm_config()
    finally:
        if prev is None:
            os.environ.pop("LLM_PROVIDER", None)
        else:
            os.environ["LLM_PROVIDER"] = prev
    if cfg.get("provider") == primary.get("provider"):
        return None
    if cfg.get("env_key") and not os.getenv(cfg["env_key"]):
        return None
    return cfg


def _llm_fallback_ladder(primary: dict[str, Any]) -> list[dict[str, Any]]:
    """Every usable fallback, same-provider model first, then cross-provider.

    A Kimi conversation-shape 400 (orphaned ``tool_call_id``s, tokenization)
    cannot be fixed by another Moonshot model — Groq must still be on the
    ladder even when ``KIMI_FALLBACK_MODEL`` is set.
    """
    out: list[dict[str, Any]] = []
    same = _kimi_model_fallback(primary)
    if same:
        out.append(same)
    cross = _cross_provider_fallback(primary)
    if cross:
        out.append(cross)
    return out


def _llm_fallback_config(primary: dict[str, Any]) -> dict[str, Any] | None:
    """First fallback only — callers that want the full ladder use
    ``_llm_fallback_ladder``.

    Two fallback shapes, checked in order:

    1. SAME-PROVIDER MODEL fallback (``KIMI_FALLBACK_MODEL``): when the Kimi
       primary (kimi-k2.6, a slow reasoning model that forces temperature=1)
       times out or errors on a large grounded turn, retry the SAME Moonshot
       key/endpoint with a fast, temperature-flexible, big-context model
       (moonshot-v1-128k). This is the RECOMMENDED fallback — it survives the
       payloads Groq's free tier 413s on (verified: 19.5k tokens OK), needs no
       new key, and has no fixed-temperature constraint. Set
       KIMI_FALLBACK_MODEL=moonshot-v1-128k.

    2. CROSS-PROVIDER fallback (``LLM_FALLBACK_PROVIDER`` = ``groq`` |
       ``ollama``): degrade to another provider on a retryable failure
       (413/429/5xx/network). Returns ``None`` when unset, names the primary
       provider, or its API-key env is missing.

    Reuses ``_llm_config`` for URL/suffix normalisation by pinning the provider
    through the env for the duration of one synchronous call.
    """
    ladder = _llm_fallback_ladder(primary)
    return ladder[0] if ladder else None


def _provider_temperature(cfg: dict[str, Any], default: float) -> float:
    """The temperature this provider will actually accept.

    Some providers pin temperature. Moonshot's K2 reasoning models 400 on any
    value but 1. Rather than hardcode 1 globally (which would become the NEXT
    provider's 400), each constrained provider declares ``fixed_temperature`` in
    ``_llm_config``; every other provider keeps the agent's own temperature.
    Applied at the same outbound chokepoint as message sanitisation so every
    caller (_call_llm and _stream_synthesis) shapes the payload identically.
    """
    fixed = cfg.get("fixed_temperature")
    return default if fixed is None else float(fixed)


def _provider_max_tokens(cfg: dict[str, Any], configured: int) -> int:
    """The max_tokens this provider needs to produce any content at all.

    Reasoning providers declare ``reasoning_min_tokens``: the shared budget is
    spent on chain-of-thought first, so a configured cap below the model's
    reasoning burn yields finish_reason=length with EMPTY content every time
    (measured: 1024 -> 0 chars content, 3,940 chars reasoning). The floor only
    LIFTS a starving budget; a generous agent budget passes through, and
    providers without the field keep the agent's own value.
    """
    floor = cfg.get("reasoning_min_tokens")
    if floor is None:
        return configured
    return max(int(configured), int(floor))


def _is_native_ollama(cfg: dict[str, Any]) -> bool:
    """True when the configured Ollama endpoint uses the native /api/chat
    protocol rather than the OpenAI-compatible /v1/chat/completions path."""
    return cfg.get("provider") == "ollama" and str(cfg.get("url", "")).endswith("/api/chat")


def _sanitize_messages_for_ollama_native(
    messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Adapt OpenAI-shaped messages for Ollama's native /api/chat protocol.

    Native Ollama does not accept the ``tool`` role; tool responses must be
    sent as ``user`` messages. It also expects ``function.arguments`` inside
    assistant ``tool_calls`` to be a parsed dict, not a JSON string.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "user",
                "content": f"Tool result: {m.get('content', '')}",
            })
            continue
        if role == "assistant":
            cm = {"role": "assistant", "content": m.get("content", "")}
            tcs = m.get("tool_calls")
            if isinstance(tcs, list):
                clean_tcs: list[dict[str, Any]] = []
                for tc in tcs:
                    if not isinstance(tc, dict):
                        clean_tcs.append(tc)
                        continue
                    fn = tc.get("function") or {}
                    raw_args = fn.get("arguments") or "{}"
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = {}
                    else:
                        args = raw_args
                    clean_tcs.append({
                        "id": tc.get("id") or "",
                        "type": tc.get("type") or "function",
                        "function": {
                            "name": fn.get("name") or "",
                            "arguments": args,
                        },
                    })
                if clean_tcs:
                    cm["tool_calls"] = clean_tcs
            out.append(cm)
            continue
        out.append(m)
    return out


def _native_ollama_payload(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    """Build an Ollama-native /api/chat payload from our OpenAI-shaped inputs.

    Ollama's native protocol expects ``options.temperature`` and
    ``options.num_predict`` (token limit), and ``tools`` in OpenAI format.
    ``tool_choice`` is not supported natively, so the caller omits it.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if tools:
        payload["tools"] = tools
    return payload


def _ollama_message_to_openai(message: dict[str, Any]) -> dict[str, Any]:
    """Convert an Ollama-native ``message`` dict into an OpenAI-style message.

    Native tool_calls carry ``function.arguments`` as a parsed dict; we
    serialise it back to JSON so the rest of the runtime stays unchanged.
    """
    out: dict[str, Any] = {
        "role": message.get("role", "assistant"),
        "content": message.get("content", ""),
    }
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        clean: list[dict[str, Any]] = []
        for i, tc in enumerate(tool_calls):
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            name = fn.get("name") or ""
            args = fn.get("arguments")
            if isinstance(args, dict):
                args = json.dumps(args)
            elif not isinstance(args, str):
                args = "{}"
            clean.append({
                "id": tc.get("id") or f"ollama_{i+1}",
                "type": "function",
                "function": {"name": name, "arguments": args},
            })
        if clean:
            out["tool_calls"] = clean
    return out


# Outbound message sanitiser — the single chokepoint that protects every
# provider from non-standard fields our own (reasoning-capable) models add.
# Reasoning models (gpt-oss / GLM) attach a `reasoning` field to the assistant
# message that carries a tool call; runtime appends that message wholesale and
# it is persisted to agent memory. When the array is later POSTed to Groq it
# 400s: "messages[N].reasoning: reasoning is not supported with this model" and
# the whole (tool-calling) turn errors. We WHITELIST allowed keys rather than
# blacklist `reasoning`, so whatever field the next provider invents is dropped
# too. Applied only in _call_llm so already-contaminated history is neutralised
# on replay — no persistence migration.
_ALLOWED_MSG_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")
_ALLOWED_TOOLCALL_KEYS = ("id", "type")
_ALLOWED_FUNCTION_KEYS = ("name", "arguments")


class _SynthStreamError(Exception):
    """Raised by ``_stream_synthesis`` when the streaming synthesis call cannot
    be served (bad config, over daily cap, non-200, transport error). The
    streaming chat loop catches it and — if nothing was streamed yet — falls
    back to the non-streaming ``_call_llm`` path, so streaming is never a
    one-way door away from the working behaviour."""


_GROQ_TPM_CHAR_BUDGET = 16000  # ~4k tokens; leave headroom under 8000 TPM


def _approx_message_chars(messages: list[dict[str, Any]] | None) -> int:
    total = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        total += len(str(m.get("content") or ""))
    return total


#: Smallest useful preview of a compacted tool result. Below this the
#: model has nothing to reason from and answers about the note instead.
_TOOL_PREVIEW_FLOOR = 1000

#: What the LAST tool result keeps. It is the one the answer is about --
#: the BOQ that was just parsed, the schedule that was just built -- while
#: the older ones are usually context the model has already used.
_LAST_TOOL_PREVIEW = 6000


def _compacted_tool_payload(content: str, keep: int) -> str:
    """A truncated tool result that says exactly what it lost.

    Live on 554e0b9: asked how many cubic metres of demolition are in the
    BOQ, the model answered from the preview and reported "pages 1-6 of 16",
    concluding no m3 item existed. The 945 m3 line is past the cut. It had
    only been told "Compacted for Groq TPM 8000" -- not how much was gone --
    so it inferred the extent from the content and inferred it wrong.

    Two things fix that, and neither is a bigger budget:

    * state the loss as a NUMBER, so the model does not have to guess it;
    * forbid the absence claim outright. inject.py already applies exactly
      this rule to retrieval excerpts (SCOPE OF ABSENCE) because a top-K
      sample is evidence of what IS present, never of what is absent. A
      truncated tool result is the same shape of evidence and never got the
      same rule. On that turn the model happened to hedge correctly. The
      same cut with different phrasing produces "the BOQ contains no
      demolition in cubic metres" -- a false statement about the customer's
      own contract, stated with the tool's authority behind it.
    """
    kept = max(0, int(keep))
    dropped = max(0, len(content) - kept)
    return json.dumps(
        {
            "truncated": True,
            "note": "Compacted for Groq TPM 8000.",
            "chars_shown": min(kept, len(content)),
            "chars_dropped": dropped,
            "chars_total": len(content),
            "scope_of_absence": (
                f"You are seeing the FIRST {min(kept, len(content))} of "
                f"{len(content)} characters of this tool result; "
                f"{dropped} characters are not shown. Anything the user "
                "asked about may be in the part you cannot see. Say what "
                "you found and that the result was truncated. NEVER state "
                "or imply that a value, line item, or section is absent "
                "from the document or the data -- you cannot see far "
                "enough to know that. Offer to re-run narrowed to the "
                "section, page, or code the user wants."
            ),
            "preview": content[:kept],
        },
        default=str,
    )


#: The retrieval brief's first line. A system message carrying it is the
#: EVIDENCE for the turn, not boilerplate, and is compacted differently.
_RETRIEVAL_BRIEF_MARKER = "AUTHORITATIVE REFERENCE CONTEXT"

#: One retrieved excerpt starts here. inject.py::_marker builds these.
_EXCERPT_SPLIT_RE = re.compile(r"(?=\[doc_id=[^\]\s]+\s+chunk=\d+\s+score=)")


def _compact_retrieval_message(content: str, budget: int) -> str:
    """Drop whole excerpts from the tail, and say how many.

    Measured on the real thing: a 5-chunk Contract Data brief is 8072
    characters -- 1704 of instructions and 6368 of evidence. The flat
    ``content[:2500]`` this replaces kept every word of the instructions and
    796 characters of evidence: ONE excerpt of five, cut mid-sentence, with
    no indication that four were gone.

    That is the worst possible trade. The header tells the model it is
    holding authoritative context and must answer from it; the compactor
    then removes the context and leaves the instruction. It is also why two
    live answers reported a fact "missing from what was returned" when
    retrieval had in fact returned it:

        D1  "the specific Contract Data entry ... that names the individual
             Engineer's Representative is missing from what was returned"
        E1  "The retrieved excerpts do not contain the Contract Price
             expressed in SAR"

    So: never slice an excerpt. Drop whole ones from the tail -- they are
    ordered by score, so the tail is the weakest evidence -- and state the
    count, because the header's SCOPE OF ABSENCE rule is only honest if the
    model knows the sample got smaller.
    """
    parts = _EXCERPT_SPLIT_RE.split(content)
    header, excerpts = parts[0], [p for p in parts[1:] if p.strip()]
    if not excerpts:
        # Not the shape we expected. Slice, but keep the rule intact rather
        # than leaving instructions with no caveat.
        return content[:budget] + "\n[truncated for TPM]"

    def _note(kept: int) -> str:
        if kept == len(excerpts):
            return ""
        return (
            f"\nBUDGET NOTE: {len(excerpts) - kept} of {len(excerpts)} "
            "retrieved excerpts were dropped to fit this request, lowest "
            "relevance first. The sample you are reading is SMALLER than "
            "what search found, so the SCOPE OF ABSENCE rule above applies "
            "with more force, not less: say what you found and offer to "
            "search again by document name or section. Never report that "
            "something is absent.\n"
        )

    for kept in range(len(excerpts), 0, -1):
        out = header + _note(kept) + "".join(excerpts[:kept])
        if len(out) <= budget:
            return out
    # Zero excerpts, returned regardless of whether the header fits. Two
    # things are load-bearing here and neither is the size.
    #
    # The rules: slicing the header is the one outcome worse than being a
    # few hundred characters long, because it removes SCOPE OF ABSENCE and
    # the never-claim-absence line, and those are what keep the model honest
    # precisely when the evidence is thinnest. Bounded cost -- the header is
    # a fixed ~1700 characters, not a document -- and the caller's
    # drop-middle-history pass finds the room elsewhere.
    #
    # The note: zero is a real outcome, not an edge case. Without it the
    # model reasons about a sample it no longer has.
    return header + _note(0)


def _compact_messages_for_tpm(
    messages: list[dict[str, Any]],
    budget: int = _GROQ_TPM_CHAR_BUDGET,
) -> list[dict[str, Any]]:
    """Shrink a Groq hop so on-demand TPM 8000 is not blown by tool dumps.

    Live M1/M3/M7/M9/M12/M14: Kimi empty/filter → Groq 413 on 9–12k tokens.
    Keep roles and tool-call pairing; truncate contents only.

    Tool results are compacted OLDEST FIRST and only while the hop is still
    over budget. The previous pass cut every tool result over 1200 chars to
    the same 1000-char preview whether or not that was needed -- so a turn
    that was 200 chars over budget lost 39k characters of a just-parsed BOQ,
    and the result the question was actually about was cut exactly as hard
    as a stale one from iteration 0.
    """
    if _approx_message_chars(messages) <= budget:
        return messages

    # Oldest first, last excluded: the last tool result is the one the
    # answer is about, so it is compacted last and keeps the most.
    # Every tool message, not only the big ones: the size test belongs with
    # the keep size, below. Filtering here on the floor would also mean a
    # small trailing result stopped counting as "the last one", handing the
    # larger share to an older result the question is not about.
    tool_idx = [
        i for i, m in enumerate(messages)
        if isinstance(m, dict) and m.get("role") == "tool"
        and isinstance(m.get("content"), str)
    ]
    keep_for = {i: _TOOL_PREVIEW_FLOOR for i in tool_idx}
    if tool_idx:
        keep_for[tool_idx[-1]] = _LAST_TOOL_PREVIEW

    running = _approx_message_chars(messages)
    compact_tool: dict[int, int] = {}
    for i in tool_idx:
        if running <= budget:
            break
        keep = keep_for[i]
        if len(messages[i]["content"]) <= keep:
            continue
        compact_tool[i] = keep
        running -= len(messages[i]["content"]) - keep
    # Still over with the last one at its larger share: cut it to the floor
    # too rather than blowing the hop.
    if running > budget and tool_idx:
        last = tool_idx[-1]
        if compact_tool.get(last, _LAST_TOOL_PREVIEW) > _TOOL_PREVIEW_FLOOR:
            running -= (
                compact_tool.get(last, len(messages[last]["content"]))
                - _TOOL_PREVIEW_FLOOR
            )
            compact_tool[last] = _TOOL_PREVIEW_FLOOR

    out: list[dict[str, Any]] = []
    for idx, m in enumerate(messages):
        if not isinstance(m, dict):
            out.append(m)
            continue
        cm = dict(m)
        role = cm.get("role")
        content = cm.get("content")
        if not isinstance(content, str):
            out.append(cm)
            continue
        if role == "tool" and idx in compact_tool:
            cm["content"] = _compacted_tool_payload(content, compact_tool[idx])
        elif role == "system" and len(content) > 2500:
            # The retrieval brief is handled after this loop: it is the
            # turn's evidence and must be the LAST thing sacrificed, not cut
            # to 2500 alongside boilerplate. Everything else is prompt text
            # the model can do without.
            if _RETRIEVAL_BRIEF_MARKER not in content:
                cm["content"] = content[:2500] + "\n[truncated for TPM]"
        elif role == "user" and len(content) > 6000:
            if "PLATFORM PRE-DISPATCH:" not in content[:80]:
                cm["content"] = content[:6000] + "\n[truncated for TPM]"
        out.append(cm)

    # Evidence last -- and that means LAST. Before the brief gives up an
    # excerpt, the tool results give up the rest of their allowance down to
    # the floor. Excerpts are dropped whole, so a 170-character overage would
    # otherwise cost a 1300-character excerpt while a 6000-character tool
    # preview sat untouched: the coarse unit must come out of the cheap
    # budget, not the expensive one.
    over = _approx_message_chars(out) - budget
    if over > 0 and tool_idx:
        for i in reversed(tool_idx):
            if over <= 0:
                break
            cm = out[i]
            if not isinstance(cm, dict) or not isinstance(cm.get("content"), str):
                continue
            have = compact_tool.get(i)
            if have is None or have <= _TOOL_PREVIEW_FLOOR:
                continue
            new_keep = max(_TOOL_PREVIEW_FLOOR, have - over)
            shrunk = _compacted_tool_payload(messages[i]["content"], new_keep)
            over -= len(cm["content"]) - len(shrunk)
            cm["content"] = shrunk
            compact_tool[i] = new_keep
        over = _approx_message_chars(out) - budget

    if over > 0:
        for cm in out:
            if not isinstance(cm, dict) or cm.get("role") != "system":
                continue
            content = cm.get("content")
            if not isinstance(content, str) or _RETRIEVAL_BRIEF_MARKER not in content:
                continue
            # Never below the header: see _compact_retrieval_message.
            allowance = max(0, len(content) - over)
            shrunk = _compact_retrieval_message(content, allowance)
            over -= len(content) - len(shrunk)
            cm["content"] = shrunk
            if over <= 0:
                break
    if _approx_message_chars(out) <= budget:
        return out
    # Drop middle history; keep first system + last 8 messages.
    # Do NOT re-run pairing sanitise here — an unpaired tool dump is
    # exactly what we need to keep (truncated) for a Groq 413 retry.
    kept: list[dict[str, Any]] = []
    systems = [m for m in out if isinstance(m, dict) and m.get("role") == "system"]
    rest = [m for m in out if not (isinstance(m, dict) and m.get("role") == "system")]
    kept.extend(systems[:2])
    kept.extend(rest[-8:])
    return kept or out


def _resolve_groq_model(name: str | None) -> str:
    """Map retired Groq ids so a stale ``GROQ_MODEL`` env still serves.

    Render still pins ``GROQ_MODEL=llama-3.3-70b-versatile``. Changing only
    ``GROQ_DEFAULT_MODEL`` would leave that override 404ing.
    """
    raw = (name or "").strip() or GROQ_DEFAULT_MODEL
    return GROQ_RETIRED_MODELS.get(raw, raw)


def _http_400_is_retryable(body: str) -> bool:
    """True for provider 400s another model CAN serve.

    Generic 400s (bad key, unknown model, malformed JSON we sent) stay
    non-retryable. Kimi conversation-shape / tokenizer 400s are the same
    class as Groq ``tool_use_failed``-as-prose: the next provider on the
    ladder is the recovery, not a quota burn on a broken request.
    """
    text = (body or "").lower()
    if "tokenization failed" in text:
        return True
    if "tool_call_id" in text and "followed" in text:
        return True
    if "must be followed by tool messages" in text:
        return True
    if "invalid request" in text and "tool_call" in text:
        return True
    # Live M12 on 817f224: Kimi HTTP 400 + type=content_filter / "high risk"
    # (not HTTP 200 + finish_reason). Same class as a filtered 200.
    if "content_filter" in text or "considered high risk" in text:
        return True
    return False


def _llm_choice_is_empty_or_filtered(choice: Any) -> bool:
    """True when the provider accepted the request but produced no usable turn.

    Live M12: Kimi HTTP 200 + ``finish_reason=content_filter`` + empty
    content on construction-pm. Live M9: contracts-manager HTTP 200 with
    empty content and no tool_calls. Both used to be treated as a
    successful empty answer. Another provider on the ladder can still
    serve the turn.
    """
    if not isinstance(choice, dict):
        return True
    reason = str(choice.get("finish_reason") or "").lower().replace("-", "_")
    if reason in {"content_filter", "safety"}:
        return True
    msg = choice.get("message") or {}
    if msg.get("tool_calls"):
        return False
    return not (msg.get("content") or "").strip()


def _normalize_tool_call_ids(tool_calls: list[Any]) -> list[Any]:
    """Give every tool_call a stable id. Kimi infers ``name:index`` when ``id``
    is missing; our tool results must use the same string or the next call 400s.
    """
    out: list[Any] = []
    for i, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            out.append(tc)
            continue
        ctc = dict(tc)
        fn = ctc.get("function") if isinstance(ctc.get("function"), dict) else {}
        name = fn.get("name") or ctc.get("name") or "tool"
        if not ctc.get("id"):
            ctc["id"] = f"{name}:{i}"
        out.append(ctc)
    return out


def _repair_tool_call_pairing(
    messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Legal OpenAI tool history: every assistant ``tool_calls[].id`` is
    answered by the immediately-following ``role=tool`` messages.

    Live 2026-08-20: a user-role empty-router/error nudge inserted AFTER the
    second of four ``fetch_document`` results made Kimi 400
    (``Missing: fetch_document:3, fetch_document:4``) and the turn died
    because HTTP 400 was non-retryable. Move interleaved non-tool messages
    to after the batch and stub any still-missing ids so Groq (and a
    retried Kimi) can accept the payload.
    """
    out: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if not (isinstance(m, dict) and m.get("role") == "assistant"
                and isinstance(m.get("tool_calls"), list) and m["tool_calls"]):
            if isinstance(m, dict) and m.get("role") == "tool":
                out.append({
                    "role": "user",
                    "content": (
                        f"Tool result ({m.get('name') or 'unknown'}): "
                        f"{m.get('content') or ''}"
                    ),
                })
                i += 1
                continue
            out.append(m)
            i += 1
            continue

        assistant = dict(m)
        tcs = _normalize_tool_call_ids(list(assistant.get("tool_calls") or []))
        assistant["tool_calls"] = tcs
        out.append(assistant)
        needed_ids = [
            tc.get("id") for tc in tcs
            if isinstance(tc, dict) and tc.get("id")
        ]
        needed_set = set(needed_ids)
        found: dict[str, dict[str, Any]] = {}
        deferred: list[Any] = []
        i += 1
        while i < n:
            nxt = messages[i]
            if not isinstance(nxt, dict):
                deferred.append(nxt)
                i += 1
                continue
            role = nxt.get("role")
            if role == "assistant":
                break
            if role == "tool":
                tid = nxt.get("tool_call_id")
                if tid in needed_set and tid not in found:
                    tm = dict(nxt)
                    tm["tool_call_id"] = tid
                    found[tid] = tm
                elif not tid:
                    for nid in needed_ids:
                        if nid not in found:
                            tm = dict(nxt)
                            tm["tool_call_id"] = nid
                            found[nid] = tm
                            break
                    else:
                        deferred.append(nxt)
                else:
                    deferred.append(nxt)
                i += 1
                continue
            deferred.append(nxt)
            i += 1

        for tid in needed_ids:
            if tid in found:
                out.append(found[tid])
            else:
                out.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "name": "unknown",
                    "content": json.dumps({
                        "status": "error",
                        "error": "tool result missing from history",
                    }),
                })
        for d in deferred:
            if isinstance(d, dict) and d.get("role") == "tool":
                out.append({
                    "role": "user",
                    "content": (
                        f"Tool result ({d.get('name') or 'unknown'}): "
                        f"{d.get('content') or ''}"
                    ),
                })
            else:
                out.append(d)
    return out


def _sanitize_messages_for_provider(
    messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return a copy of ``messages`` with only OpenAI-standard keys per message
    (and per tool_call). Never mutates the input list."""
    clean: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            clean.append(m)
            continue
        cm = {k: m[k] for k in _ALLOWED_MSG_KEYS if k in m}
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            clean_tcs = []
            for tc in tcs:
                if not isinstance(tc, dict):
                    clean_tcs.append(tc)
                    continue
                ctc = {k: tc[k] for k in _ALLOWED_TOOLCALL_KEYS if k in tc}
                fn = tc.get("function")
                if isinstance(fn, dict):
                    ctc["function"] = {
                        k: fn[k] for k in _ALLOWED_FUNCTION_KEYS if k in fn
                    }
                clean_tcs.append(ctc)
            cm["tool_calls"] = clean_tcs
        clean.append(cm)
    return _repair_tool_call_pairing(clean)


# ── DSML inline tool-call markup handling ─────────────────────────────────
# Some models emit a tool call as inline text markup inside the
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

# Llama 3.x native tool-call markup — Groq's strict tool-use validator rejects
# these shapes with HTTP 400 `tool_use_failed` and emits the raw markup in
# `failed_generation`. We recover it into proper OpenAI-style tool_calls so the
# agent loop can dispatch and continue. Observed variants (all live on Groq):
#   <function=name{json}>              inline close
#   <function=name{json}</function>    closing tag, no `>` (2026-07-24 probe)
#   <function=name>{json}</function>   canonical Llama 3 tag pair
_LLAMA_FUNC_RE = re.compile(
    r"<\s*function\s*=\s*([A-Za-z_][\w]*)\s*>?\s*(\{.*?\})\s*(?:>|</\s*function\s*>)",
    re.DOTALL,
)


def _parse_llama_native_tool_calls(text: str) -> list[dict]:
    """Extract Llama-native `<function=name{json}>` markup into OpenAI-shaped
    tool_calls dicts. Returns [] if no markup found or every match fails to
    parse as JSON.
    """
    if not text or "<function" not in text:
        return []
    out: list[dict] = []
    counter = 0
    for m in _LLAMA_FUNC_RE.finditer(text):
        name = m.group(1).strip()
        if not name:
            continue
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            continue
        counter += 1
        out.append({
            "id": f"llama_{counter}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args),
            },
        })
    return out


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


_CM_INJECT_AGENTS = frozenset({"heavy-reasoning", "project-assistant"})


def _cm_prompt_fragment_for_turn(user_message: str) -> str:
    """Compact CrossDomainReasoner inject for construction agent paths."""
    try:
        from app.core.cross_domain_reasoner import CrossDomainReasoner

        reasoner = CrossDomainReasoner()
        injected = reasoner.inject_prompt("", user_message or "")
        text = (injected or "").strip()
        return text[:800] if text else ""
    except Exception:  # noqa: BLE001
        return ""


# Blocks that are pure infrastructure -- their presence alone does not make an
# agent functional (see Agent.unavailable_reason).
_INFRA_ONLY_BLOCKS = frozenset({"cache_manager"})


# File-consuming blocks need an explicit `file_path` schema so the LLM can't
# emit the call with empty args. Without this, the agent called e.g.
# boq_processor with {} after search_project_documents, got "No file_path
# provided", and deflected to the user. The runtime's
# _resolve_block_file_input then maps the bare filename to the encrypted
# stored path, so the agent only needs to pass the original_name string it
# saw from search_project_documents.
#
# Module scope rather than a local: it is a constant, it was rebuilt on every
# tool_definitions() call, and a schema the model depends on should be
# reachable by a test without standing up a block registry.
_FILE_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "boq_processor": {
        "description": (
            "Extract structured Bill of Quantities from an uploaded "
            "xlsx/csv/pdf file. Returns line items with quantities, "
            "rates, amounts, and totals. The file_path must be the "
            "exact original_name of a document returned by "
            "search_project_documents — never guess paths."
        ),
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The document's original_name (e.g. "
                    "'Infra-1 - Demolition BOQ.pdf'). MUST come "
                    "from a prior search_project_documents call."
                ),
            },
            "char_offset": {
                "type": "integer",
                "description": (
                    "Optional. Read a LATER window of a result that "
                    "came back truncated: pass the next_char_offset "
                    "the previous call reported, and repeat until "
                    "chars_remaining is 0. Never tell the user a "
                    "value is absent from this document while "
                    "chars_remaining is above 0 -- read the rest "
                    "first."
                ),
            },
        },
        "required": ["file_path"],
    },
    "drawing_qto": {
        "description": (
            "Extract quantity takeoff from a drawing file "
            "(DXF/DWG/PDF). Returns measured areas, lengths, counts. "
            "The file_path must be the exact original_name returned by "
            "search_project_documents — never guess paths."
        ),
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The drawing's original_name (e.g. "
                    "'tower_b_floor_plan.dxf'). MUST come from a prior "
                    "search_project_documents call."
                ),
            },
            "char_offset": {
                "type": "integer",
                "description": (
                    "Optional. Read a LATER window of a result that "
                    "came back truncated: pass the next_char_offset "
                    "the previous call reported, and repeat until "
                    "chars_remaining is 0. Never tell the user a "
                    "value is absent from this document while "
                    "chars_remaining is above 0 -- read the rest "
                    "first."
                ),
            },
        },
        "required": ["file_path"],
    },
    "spec_analyzer": {
        "description": (
            "Extract specifications, grades, standards, and methods "
            "from a specification document. file_path must be the "
            "original_name from search_project_documents — never guess."
        ),
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The spec doc's original_name. MUST come from a "
                    "prior search_project_documents call."
                ),
            },
            "char_offset": {
                "type": "integer",
                "description": (
                    "Optional. Read a LATER window of a result that "
                    "came back truncated: pass the next_char_offset "
                    "the previous call reported, and repeat until "
                    "chars_remaining is 0. Never tell the user a "
                    "value is absent from this document while "
                    "chars_remaining is above 0 -- read the rest "
                    "first."
                ),
            },
        },
        "required": ["file_path"],
    },
    "primavera_parser": {
        "description": (
            "Parse a Primavera P6 .xer schedule file from the project. "
            "Returns activities, milestones, WBS, schedule_data, and CPM "
            "output. file_path must be the original_name from "
            "search_project_documents — never guess paths."
        ),
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "The XER file's original_name (e.g. "
                    "'Annexure 2 - Baseline Program XER.xer'). MUST come "
                    "from a prior search_project_documents call."
                ),
            },
            "char_offset": {
                "type": "integer",
                "description": (
                    "Optional. Read a LATER window of a result that "
                    "came back truncated: pass the next_char_offset "
                    "the previous call reported, and repeat until "
                    "chars_remaining is 0. Never tell the user a "
                    "value is absent from this document while "
                    "chars_remaining is above 0 -- read the rest "
                    "first."
                ),
            },
        },
        "required": ["file_path"],
    },
}


_MISSING_INPUT_NUDGE = (
    "The excerpts above were retrieved specifically because your draft "
    "answer said it did not have %r. Read them and answer the original "
    "question in full, using the figure(s) they contain and citing the "
    "clause or document they came from.\n"
    "If they still do not contain it, say so plainly and do NOT estimate, "
    "infer, or substitute a typical value -- a wrong figure is worse than "
    "the gap you already reported."
)


@dataclass
class Agent:
    """Declarative agent definition."""

    name: str
    description: str
    system_prompt: str
    allowed_blocks: list[str] = field(default_factory=list)
    model: str = KIMI_DEFAULT_MODEL
    temperature: float = 0.3
    max_tokens: int = 2048
    icon: str = ""
    can_delegate: bool = False
    # Discipline-hat kernels bound at load time (manifest ids); the guidance
    # is already merged into system_prompt by _apply_hat_kernels.
    hats: list[str] = field(default_factory=list)
    # F34: this agent consumes figures the user supplies (corrections, actuals)
    # -- RAG injection must not strict-ground it against the corpus.
    user_data_authoritative: bool = False

    def unavailable_reason(self) -> str | None:
        """F35: an agent whose every functional block failed to load is a GHOST
        -- it lists in /v1/agents, accepts a chat, and then truthfully tells
        the user it has no tools (observed live: external-mcp on a virgin
        deployment, where mcp_consumer only loads with CEREBRUM_VIRGIN=false).
        Blocks absent from BLOCK_REGISTRY are silently skipped when building
        tool definitions, so this is the one place the gap is visible.
        Infra-only blocks (cache_manager) don't count as functional; agents
        that declare no blocks at all are prose agents and stay available."""
        functional = [b for b in self.allowed_blocks if b not in _INFRA_ONLY_BLOCKS]
        if functional and not any(b in BLOCK_REGISTRY for b in functional):
            return (
                "none of this agent's functional blocks ("
                + ", ".join(functional)
                + ") are loaded on this deployment (extended platform blocks "
                "require CEREBRUM_VIRGIN=false)"
            )
        return None

    def tool_definitions(self, project_id: str | None = None) -> list[dict[str, Any]]:
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
            # File-consuming blocks get a typed schema with required file_path.
            override = _FILE_TOOL_SCHEMAS.get(block_name)
            if override:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": block_name,
                        "description": override["description"],
                        "parameters": {
                            "type": "object",
                            "properties": override["properties"],
                            "required": override["required"],
                        },
                    },
                })
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

        # ── synthetic tool: list_project_documents (project-scoped) ──────────
        # The document REGISTER, deterministically from the project store.
        # Without this, "list the documents in this project / what do we have"
        # can only be answered by hoping a semantic search surfaces a register
        # that does not exist as a chunk — verified failing on the golden set
        # (pilot_document_metadata, 2026-08-02): the model honestly reported
        # the RAG context held no document list.
        if project_id:
            tools.append({
                "type": "function",
                "function": {
                    "name": "list_project_documents",
                    "description": (
                        "List this project's document register: each document's "
                        "filename, type, and size. Use this when the user asks "
                        "WHAT documents/files the project has. To search INSIDE "
                        "document content use search_project_documents."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": ["integer", "string"], "description": "Max documents to return (default 100, newest first)."},
                        },
                        "required": [],
                    },
                },
            })

        # ── synthetic tool: fetch_document (project-scoped) ──────────────────
        # Complements search_project_documents: fetches ONE specific document's
        # content by document_id or filename. This is what makes "work on the
        # attached file" actionable — the attachment note gives the agent a
        # concrete document_id, and this tool reads that document directly
        # instead of hoping a semantic query happens to surface it.
        if project_id:
            tools.append({
                "type": "function",
                "function": {
                    "name": "fetch_document",
                    "description": (
                        "Fetch the content of ONE specific project document by "
                        "document_id or filename. Use this when the user refers to a "
                        "specific or attached file. For open-ended questions across "
                        "the corpus use search_project_documents instead."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "document_id": {"type": "string", "description": "The document id (preferred when known, e.g. from an attachment note or a prior search result)."},
                            "filename": {"type": "string", "description": "The document's original filename (used when the id is not known)."},
                        },
                        "required": [],
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
                                # Some providers (Groq/llama-3.x/llama-4-scout) emit integer
                                # tool args as strings. Declaring ["integer","string"] keeps
                                # the strict tool-use validator happy; we coerce in
                                # _run_tool_call before passing to ConstructionContainer.
                                "type": ["integer", "string"],
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
                            "duration_overrides": {
                                "type": "object",
                                "additionalProperties": {"type": ["integer", "string"]},
                                "description": (
                                    "Activity-name substring → working days "
                                    "(e.g. {\"slab\": 6}). Parsed from the user "
                                    "message when omitted — 'use N days per slab "
                                    "and re-run' is applied even if this is empty."
                                ),
                            },
                        },
                        "required": ["brief"],
                    },
                },
            })
            # ── synthetic tool: commissioning_checklist ──────────────────────
            tools.append({
                "type": "function",
                "function": {
                    "name": "commissioning_checklist",
                    "description": (
                        "Generate a systems commissioning / testing & commissioning "
                        "checklist with the REAL test standards and acceptance "
                        "criteria per system (HVAC: ASHRAE/AHRI; electrical: "
                        "IEEE/BS 7671; fire: NFPA; plumbing/lift/facade/BMS too). "
                        "Call this whenever the user asks to produce/generate a "
                        "commissioning or T&C checklist — do NOT invent test "
                        "standards yourself; this tool returns the authoritative ones."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "systems": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Systems to commission. Map the user's wording to "
                                    "these keys: waterproofing, hvac, electrical, fire, "
                                    "plumbing, elevator, facade, bms. For torch-applied "
                                    "membrane / pre-backfill waterproofing use "
                                    "waterproofing — do not default to HVAC."
                                ),
                            },
                        },
                        "required": ["systems"],
                    },
                },
            })
            # ── synthetic tool: construction_calc (deterministic formulas) ───
            tools.append(_construction_calc_tool_schema())
            # ── synthetic tool: cash_flow_forecast (S-curve) ────────────────
            # Same reason as generate_wbs: the generic `construction` tool's
            # input/params shape lets the model narrate an S-curve instead of
            # calling cash_flow_forecast (pinned construction-pm, 12-month
            # cash flow). A typed top-level tool is the deterministic lever;
            # K2 rejects forced tool_choice.
            tools.append({
                "type": "function",
                "function": {
                    "name": "cash_flow_forecast",
                    "description": (
                        "Build a monthly S-curve / cash-flow forecast from a "
                        "contract value and duration. CALL THIS immediately "
                        "when the user asks for an S-curve, cash flow, spend "
                        "curve, or monthly drawdown. Do not invent monthly "
                        "percents in prose — this tool is deterministic."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "contract_value": {
                                "type": ["number", "string"],
                                "description": "Contract / project value (numeric).",
                            },
                            "duration_months": {
                                "type": ["integer", "string"],
                                "description": "Forecast horizon in months (e.g. 12).",
                            },
                            "message": {
                                "type": "string",
                                "description": (
                                    "Original user request. Used to parse "
                                    "figures when contract_value / duration "
                                    "are omitted."
                                ),
                            },
                        },
                        "required": [],
                    },
                },
            })
            # ── synthetic tool: resource_histogram ───────────────────────────
            # Same reason as cash_flow_forecast: pinned construction-pm will
            # otherwise call primavera_parser (the `xer` intent map) and
            # invent W1–W4 crew rows. A typed top-level tool + predispatch
            # is the deterministic lever.
            tools.append({
                "type": "function",
                "function": {
                    "name": "resource_histogram",
                    "description": (
                        "Build a time-phased manpower / activity histogram "
                        "from a Primavera P6 .xer. CALL THIS when the user "
                        "asks for a resource, manpower, labor, or crew "
                        "histogram. Uses real TASKRSRC labor hours when "
                        "present; otherwise returns CPM activity-count week "
                        "buckets — never invent man-hours. Prefer the .xer "
                        "the user named. Do NOT call primavera_parser for a "
                        "histogram."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "schedule_file": {
                                "type": "string",
                                "description": (
                                    "The .xer original_name "
                                    "(e.g. resource_loaded.xer). Must be a "
                                    "project document name."
                                ),
                            },
                            "period_unit": {
                                "type": "string",
                                "enum": ["week", "month"],
                                "description": "Bucket size. Default week.",
                            },
                        },
                        "required": [],
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
        history: list[dict[str, str]] | None = None,
        api_key: str | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        on_event: Callable[[str, dict[str, Any]], None | Awaitable[None]] | None = None,
        user_id: str | None = None,
        _depth: int = 0,
        _call_stack: list[str] | None = None,
    ) -> dict[str, Any]:
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
        async def _emit(name: str, payload: dict[str, Any]) -> None:
            if on_event is None:
                return
            try:
                res = on_event(name, payload)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                # Event handler must never break the agent loop.
                _LOG.warning(
                    "swallowed %s in _emit() — continuing",
                    "Exception", exc_info=True,
                )
        cfg = _llm_config()
        # Ollama (local / self-hosted) has no auth — skip the env-key
        # check entirely. The empty bearer token sent later is ignored
        # by Ollama's OAI-compatible endpoint.
        if cfg["env_key"]:
            api_key = api_key or os.getenv(cfg["env_key"])
            if not api_key:
                return {
                    "status": "error",
                    "error": f"No {cfg['env_key']} configured. Set it in .env or pass via env.",
                }
        else:
            api_key = api_key or ""

        _call_stack = _call_stack or [self.name]

        # Zero-chunk project guardrail: refuse before spending any LLM budget
        # unless the project has other (non-RAG) context such as facts.
        if (
            project_id
            and not project_is_rag_ready(project_id)
            and not _project_has_non_rag_context(project_id, user_message)
        ):
            if conversation_id:
                from app.core import agent_memory
                agent_memory.get_or_create_conversation(conversation_id, self.name, project_id)
                agent_memory.append_message(conversation_id, "user", user_message)
                agent_memory.append_message(conversation_id, "assistant", _UNINDEXED_PROJECT_MESSAGE)
            return {
                "status": "success",
                "answer": _UNINDEXED_PROJECT_MESSAGE,
                "tool_calls": [],
                "iterations": 0,
                "messages": [],
                "sources": [],
            }

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

        # Strip prior hallucinated WBS/BOQ tables from history before
        # sending. Prevents the model from pattern-matching to a prior
        # (often fabricated) table when it should be calling the tool.
        effective_history = _scrub_history(effective_history)

        messages = self._build_messages(user_message, effective_history, project_id=project_id)
        # Pre-iter-0 RAG injection. Runs for any project-scoped turn so that
        # routing to heavy-reasoning (or another agent) does not strip project
        # grounding. Adds a system message AFTER the prompt + project context
        # but BEFORE the latest user turn.
        _rag_sys_msg, _rag_audit = rag_inject(
            user_message=user_message,
            project_id=project_id,
            conversation_id=conversation_id,
            user_id=user_id,
            agent_name=self.name,
            history=effective_history,
        )
        if _rag_sys_msg and _rag_sys_msg.get("content"):
            _apply_rag_context(
                messages, _rag_sys_msg,
                user_data_authoritative=self.user_data_authoritative,
            )

        # Capability / self-introspection short-circuit: a question ABOUT this
        # agent (its tools, abilities, accessible documents) is answered from the
        # REAL tool roster + REAL document list, not from matched document text.
        if _is_capability_request(user_message):
            answer = _build_capability_answer(self, project_id)
            if conversation_id:
                from app.core import agent_memory
                agent_memory.append_message(conversation_id, "assistant", answer)
            await _emit("final", {"answer": answer})
            return {
                "status": "success",
                "answer": answer,
                "tool_calls": [],
                "iterations": 0,
                "messages": messages + [{"role": "assistant", "content": answer}],
                "sources": [],
            }

        # Deterministic file pre-dispatch BEFORE the RAG-miss short-circuit.
        # Leftover L1: a timestamped ``khor_waterproofing_spec_….docx`` looks
        # like an identifier, retrieval misses (file not indexed), and the
        # canned "could not confirm this reference" used to fire in ~1s
        # without ever fetching bytes from disk.
        tool_calls_made: list[dict[str, Any]] = []
        _locked = _message_wants_locked_deliverable(user_message)
        _pre = None
        if not _locked:
            _pre = await _predispatch_file_tool(self, messages, project_id)
        if _pre:
            tool_calls_made.append(_pre)
        _wbs_pre = await _predispatch_wbs_duration_override(self, messages)
        if _wbs_pre:
            tool_calls_made.append(_wbs_pre)
        _hist_pre = await _predispatch_resource_histogram(
            self, messages, project_id,
        )
        if _hist_pre:
            tool_calls_made.append(_hist_pre)
        _wir_pre = None
        if not _locked:
            _wir_pre = await _predispatch_wir_form(
                self, messages, project_id, operator_text=user_message,
            )
        if _wir_pre:
            tool_calls_made.append(_wir_pre)
        _more_pre = None
        if not _hist_pre:
            _more_pre = await _predispatch_remaining_deliverables(
                self, messages, project_id, operator_text=user_message,
            )
        if _more_pre:
            tool_calls_made.append(_more_pre)

        # Fast path: exact reference miss with no RAG context. Skip when a
        # named project file was already fetched/extracted from disk.
        if not _pre and not _wbs_pre and not _hist_pre and not _wir_pre and not _more_pre and _should_short_circuit_rag_miss(
            _rag_audit, _rag_sys_msg, user_message
        ):
            answer = _build_missing_reference_answer(project_id, user_id)
            if conversation_id:
                from app.core import agent_memory
                agent_memory.append_message(conversation_id, "assistant", answer)
            await _emit("final", {"answer": answer})
            return {
                "status": "success",
                "answer": answer,
                "tool_calls": [],
                "iterations": 0,
                "messages": messages + [{"role": "assistant", "content": answer}],
                "sources": [],
            }
        # Root fix for the tool-loop (mirrors chat_stream): cap explicit
        # search_project_documents calls, then stop offering the tool so the
        # model answers from injected context instead of grinding to the cap.
        SEARCH_TOOL_CAP = int(os.getenv("AGENT_SEARCH_TOOL_CAP", "2"))
        search_calls = 0
        excluded_tools: set = set()
        # Once a deliverable (non-search) tool returns, force a tool-free
        # synthesis call — see the streaming loop for the full rationale (stops
        # the tool-loop and the Groq large-context tool_use_failed/429 hang).
        # Live M1/M2/M6/M9/M13/M14: remaining already drafted the artifact,
        # but force_synthesis stayed False so payment_certificate /
        # drawing_qto / wir_form / commissioning_checklist overwrote it.
        force_synthesis = bool(_wbs_pre or _hist_pre or _wir_pre or _more_pre)
        for _hit in (_wbs_pre, _hist_pre, _wir_pre, _more_pre):
            if _hit:
                excluded_tools |= _conflicting_tools_after_predispatch(
                    str(_hit.get("name") or "")
                )
        error_nudges = 0
        _force_synth_enabled = os.getenv("AGENT_FORCE_SYNTHESIS", "1") != "0"

        for iteration in range(MAX_TOOL_ITERATIONS):
            await _emit("iteration", {"n": iteration + 1})
            resp = await self._call_llm(
                messages, api_key, project_id=project_id, user_id=user_id,
                exclude_tools=excluded_tools or None,
                with_tools=not force_synthesis,
            )
            if resp.get("status") == "error":
                err = resp.get("error") or _EMPTY_RESPONSE_FALLBACK
                recovered = _recover_answer_from_tool_messages(err, messages)
                if recovered != err and not _text_needs_tool_recovery(recovered):
                    final_text = _sanitize_inline_paths(
                        _sanitize_citation_labels(recovered)
                    )
                    final_text = _postprocess_answer(
                        final_text, _rag_sys_msg, messages,
                        fallback_used=bool(_rag_audit.get("fallback_used")),
                        agent_name=self.name,
                    )
                    messages.append({"role": "assistant", "content": final_text})
                    if conversation_id:
                        from app.core import agent_memory
                        agent_memory.append_message(
                            conversation_id, "assistant", final_text,
                        )
                    await _emit("final", {"answer": final_text})
                    return {
                        "status": "success",
                        "answer": final_text,
                        "tool_calls": tool_calls_made,
                        "iterations": iteration + 1,
                        "messages": messages,
                        "recovered_from_llm_error": True,
                        "sources": _build_sources_from_audit(_rag_audit, final_text),
                        "exports": _build_exports_from_audit(
                            _rag_audit, final_text, tool_calls_made,
                        ),
                    }
                return resp
            choice = resp["choice"]
            assistant_msg = choice.get("message") or {}

            tool_calls = assistant_msg.get("tool_calls") or []
            raw_content = assistant_msg.get("content") or ""

            # DeepSeek sometimes emits the tool call as inline DSML markup in
            # `content` with an empty structured `tool_calls` field. Groq
            # Llama-4-Scout sometimes leaks raw search args as JSON content.
            # Recover either shape before treating the turn as a final answer.
            if not tool_calls:
                cleaned_content, dsml_tool_calls = _parse_dsml_tool_calls(raw_content)
                recovered_tool_calls = (
                    _recover_tool_calls_from_content(raw_content)
                    if not dsml_tool_calls else []
                )
                # Leftover L4: after a deliverable, XML/DSML leaks must not
                # re-arm tools — graft the pipeline verdict instead.
                if force_synthesis and (dsml_tool_calls or recovered_tool_calls):
                    dsml_tool_calls = []
                    recovered_tool_calls = []
                # UI-PHYS A5: once search is capped, do not re-execute the same
                # leaked search envelope for 12 iterations — synthesize instead.
                if (
                    recovered_tool_calls
                    and _recovered_calls_are_search_only(recovered_tool_calls)
                    and (
                        "search_project_documents" in excluded_tools
                        or search_calls >= SEARCH_TOOL_CAP
                    )
                ):
                    recovered_tool_calls = []
                if dsml_tool_calls:
                    # Treat this turn as a tool-calling turn.
                    tool_calls = dsml_tool_calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": cleaned_content,
                        "tool_calls": dsml_tool_calls,
                    }
                elif recovered_tool_calls:
                    tool_calls = recovered_tool_calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": recovered_tool_calls,
                    }
                else:
                    # Genuine final answer — sanitize DSML markup, raw tool JSON,
                    # and empty content before it reaches the user.
                    final_text = _sanitize_final_text(
                        raw_content, messages=messages, tool_results=tool_calls_made,
                    )
                    # If sanitization left nothing usable (empty or raw tool JSON
                    # fallback), force one no-tools call so the model must produce
                    # a plain-text answer instead of an empty bubble or leak.
                    if _final_text_needs_forced_retry(final_text, user_message=user_message):
                        if final_text == _TOOL_FORMAT_FALLBACK:
                            messages.append({"role": "user", "content": _TOOL_FORMAT_RETRY_NUDGE})
                        elif _looks_like_search_preamble(final_text):
                            messages.append({"role": "user", "content": _SEARCH_PREAMBLE_RETRY_NUDGE})
                        forced_resp = await self._call_llm(messages, api_key, project_id=project_id, with_tools=False, user_id=user_id)
                        if forced_resp.get("status") == "error":
                            final_text = _EMPTY_RESPONSE_FALLBACK
                        else:
                            forced_msg = forced_resp["choice"].get("message") or {}
                            final_text = _sanitize_final_text(
                                forced_msg.get("content") or "",
                                messages=messages, tool_results=tool_calls_made,
                            )
                            if _final_text_needs_forced_retry(final_text, user_message=user_message):
                                final_text = _EMPTY_RESPONSE_FALLBACK
                    final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
                    # An answer that names its own missing input gets ONE
                    # bounded retrieval for it before it is final (F-E1-2).
                    final_text, _fetched_for = await self._fetch_named_missing_input(
                        final_text, messages, user_message=user_message,
                        project_id=project_id, api_key=api_key, user_id=user_id,
                    )
                    final_text = _postprocess_answer(final_text, _rag_sys_msg, messages, fallback_used=bool(_rag_audit.get("fallback_used")), agent_name=self.name)
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
                        "sources": _build_sources_from_audit(_rag_audit, final_text),
                        "exports": _build_exports_from_audit(_rag_audit, final_text, tool_calls_made),
                    }

            # Persist the assistant turn that contained the tool calls
            tool_calls = _normalize_tool_call_ids(list(tool_calls))
            assistant_msg = {**assistant_msg, "tool_calls": tool_calls}
            messages.append(assistant_msg)
            pending_nudges: list[dict[str, Any]] = []
            for tc in tool_calls:
                # Surface the tool call to the event stream BEFORE running it
                # so the UI can show "️ tool_name — running…" live.
                fn = tc.get("function") or {}
                tc_name = fn.get("name") or tc.get("name") or "unknown"
                if tc_name == "search_project_documents":
                    search_calls += 1
                    if search_calls >= SEARCH_TOOL_CAP:
                        excluded_tools.add("search_project_documents")
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
                    user_message=user_message,
                    history=effective_history,
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
                _tool_content = _tool_result_content(
                    {**(tool_result["result"] if isinstance(tool_result.get("result"), dict) else {"result": tool_result.get("result")}),
                     **({"validation": tool_result["validation"]} if "validation" in tool_result else {})},
                    offset=_requested_char_offset(tc),
                )
                # Half a document is not a deliverable: locking synthesis here
                # leaves the model holding an instruction to read the next
                # window with tools already disarmed.
                if (
                    _force_synth_enabled and ok
                    and _should_force_synthesis(tool_result)
                    and not _has_unread_windows(_tool_content)
                ):
                    force_synthesis = True
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
                    "content": _tool_content,
                })
                if _empty_router_verdict(tool_result):
                    pending_nudges.append({"role": "user", "content": _EMPTY_ROUTER_NUDGE})
                elif (_tool_result_errored(tool_result)
                      and error_nudges < _TOOL_ERROR_NUDGE_CAP):
                    error_nudges += 1
                    pending_nudges.append({"role": "user", "content": _nudge_for_failed_tool(
                        tool_result, self) + _file_tool_hint(
                            messages, project_id, self.allowed_blocks)})
            messages.extend(pending_nudges)

        # Hit the cap without a final answer — force one more call with tools disabled
        # so the model is required to emit a plain-text summary.
        forced_resp = await self._call_llm(messages, api_key, project_id=project_id, with_tools=False, user_id=user_id)
        if forced_resp.get("status") == "error":
            # Even the forced call failed; fall back to the original error shape.
            return {
                "status": "error",
                "error": f"Agent exceeded {MAX_TOOL_ITERATIONS} tool iterations without a final answer.",
                "tool_calls": tool_calls_made,
                "messages": messages,
            }
        forced_msg = forced_resp["choice"].get("message") or {}
        final_text = _sanitize_final_text(
            forced_msg.get("content") or "",
            messages=messages, tool_results=tool_calls_made,
        )
        if _final_text_needs_forced_retry(final_text, user_message=user_message):
            final_text = _EMPTY_RESPONSE_FALLBACK
        final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
        final_text, _fetched_for = await self._fetch_named_missing_input(
            final_text, messages, user_message=user_message,
            project_id=project_id, api_key=api_key, user_id=user_id,
        )
        final_text = _postprocess_answer(final_text, _rag_sys_msg, messages, fallback_used=bool(_rag_audit.get("fallback_used")), agent_name=self.name)
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
            "sources": _build_sources_from_audit(_rag_audit, final_text),
            "exports": _build_exports_from_audit(_rag_audit, final_text, tool_calls_made),
        }

    async def _fetch_named_missing_input(
        self,
        final_text: str,
        messages: list[dict[str, Any]],
        *,
        user_message: str,
        project_id: str | None,
        api_key: str | None,
        user_id: str | None,
    ) -> tuple[str, str | None]:
        """ONE retrieval for an input the answer named as missing (item 4).

        F-E1-2: E1 answered "do not state the specific monetary rate per
        calendar day" while E2, one question later in the same session and
        the same corpus, retrieved the Accepted Contract Amount to complete
        its arithmetic. E1 identified its missing input and did not go and
        get it.

        Bounded means bounded: one retrieval, one re-ask, and every failure
        path returns the ORIGINAL answer. A search always returns something,
        so the answer is only revisited when what came back actually carries
        the named terms -- otherwise a correct refusal would be talked out
        of itself by whatever the index happened to rank first.
        """
        from app.agents.missing_input import (
            enabled as _mi_enabled,
            fetch_query as _mi_query,
            fetched_context_supports as _mi_supports,
            names_missing_input as _mi_names,
        )

        if not _mi_enabled() or not project_id or not (final_text or "").strip():
            return final_text, None
        missing = _mi_names(final_text)
        if not missing:
            return final_text, None
        try:
            sys_msg, _ = rag_inject(
                user_message=_mi_query(missing, user_message),
                project_id=project_id,
                # No conversation_id: this is a targeted lookup, not a turn,
                # and it must not enter the follow-up context of the next one.
                conversation_id=None,
                user_id=user_id,
                agent_name=self.name,
                history=None,
            )
        except Exception:  # noqa: BLE001 - a failed extra fetch is not a failed turn
            _LOG.warning("missing-input fetch failed for %r", missing, exc_info=True)
            return final_text, None

        body = (sys_msg or {}).get("content") or ""
        if not _mi_supports(body, missing):
            _LOG.info(
                "missing-input fetch for %r returned nothing carrying it; "
                "the answer stands as written", missing,
            )
            return final_text, None

        follow = list(messages) + [
            {"role": "assistant", "content": final_text},
            sys_msg,
            {"role": "user", "content": _MISSING_INPUT_NUDGE % missing},
        ]
        resp = await self._call_llm(
            follow, api_key, project_id=project_id, with_tools=False, user_id=user_id,
        )
        if resp.get("status") == "error":
            return final_text, None
        retry = _sanitize_final_text(
            (resp["choice"].get("message") or {}).get("content") or "",
            messages=follow, tool_results=[],
        )
        if not retry.strip() or _final_text_needs_forced_retry(
            retry, user_message=user_message
        ):
            return final_text, None
        _LOG.info("missing-input fetch answered %r; answer revised", missing)
        return retry, missing

    async def chat_stream(
        self,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        api_key: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        rag_debug: bool = False,
        attached_documents: list[dict[str, Any]] | None = None,
        _depth: int = 0,
        _call_stack: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Generator: yields {type, ...} events. Types: start, tool_call, tool_result, token, end, error, heartbeat.

        Tool-calling is non-streamed (we collect the whole assistant turn before deciding),
        but the FINAL assistant answer streams token-by-token.

        **Emit guarantee (FOLLOW-UP #90):** every exit from this generator MUST
        emit either at least one ``token`` event OR a structured ``error`` event
        before a ``end`` event. Silent exits are bugs. The trailing safety net
        below converts any escaping exception or unhandled empty-content state
        into a synthetic ``error`` + ``end`` pair.

        **Wall-clock timeout + heartbeat (FOLLOW-UP #92):** a producer task
        runs ``_chat_stream_impl`` and pushes events into a queue; a heartbeat
        task injects ``{"type": "heartbeat"}`` after each ``CHAT_STREAM_HEARTBEAT_SECONDS``
        of silence; the consumer reads with an *absolute* wall-clock deadline
        of ``CHAT_STREAM_TIMEOUT_SECONDS`` (computed once, NOT reset by events),
        and emits a structured timeout error when the deadline expires before
        the producer finishes.
        """
        agent_name = self.name
        token_emitted = False
        terminal_emitted = False  # True once we yield an `end` or `error` event
        # Tool names seen on the way out, so the synthetic `end` below can
        # still report them when the inner generator dies before its own end.
        tools_seen: list[str] = []

        # Read knobs at call-time so tests can monkeypatch env. Bad values
        # fall back to safe defaults rather than crashing the stream.
        try:
            timeout_s = float(os.getenv("CHAT_STREAM_TIMEOUT_SECONDS") or "240")
        except ValueError:
            timeout_s = 240.0
        try:
            heartbeat_s = float(os.getenv("CHAT_STREAM_HEARTBEAT_SECONDS") or "15")
        except ValueError:
            heartbeat_s = 15.0

        _SENTINEL = object()

        async def _inner():
            nonlocal token_emitted, terminal_emitted
            _LOG.info(
                "chat_stream: start agent=%s conv=%s project=%s timeout=%.1fs heartbeat=%.1fs",
                agent_name, conversation_id, project_id, timeout_s, heartbeat_s,
            )

            queue: asyncio.Queue = asyncio.Queue()

            # Absolute deadline — computed ONCE and NOT reset by events.
            # Heartbeats keep the queue active even when the upstream LLM is
            # hung, so a per-get() timeout would never trigger. The fixed
            # deadline is the only correct semantic for a wall-clock cap.
            # Computed here, before the producer, because the producer now
            # passes it down so LLM calls cannot outlive the turn.
            deadline = time.monotonic() + timeout_s

            # Shared, mutable: the producer writes what it is waiting on, the
            # consumer reads it when the wall clock runs out.
            phase: dict[str, Any] = {"name": "starting", "since": time.monotonic()}

            async def producer() -> None:
                try:
                    async for event in self._chat_stream_impl(
                        user_message=user_message,
                        history=history,
                        api_key=api_key,
                        user_id=user_id,
                        project_id=project_id,
                        attached_documents=attached_documents,
                        conversation_id=conversation_id,
                        rag_debug=rag_debug,
                        _depth=_depth,
                        _call_stack=_call_stack,
                        _deadline=deadline,
                        _phase=phase,
                    ):
                        await queue.put(event)
                finally:
                    await queue.put(_SENTINEL)

            async def heartbeat() -> None:
                # Infinite loop — cancelled by the consumer's finally block.
                while True:
                    await asyncio.sleep(heartbeat_s)
                    await queue.put({"type": "heartbeat"})

            producer_task = asyncio.create_task(producer())
            heartbeat_task = asyncio.create_task(heartbeat())

            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # Wall-clock cap exceeded. Emit a structured timeout
                        # error so the frontend's friendlyErrorMessage maps
                        # it (substring "timeout") to a clean banner.
                        _stalled = str(phase.get("name") or "unknown")
                        _stalled_for = time.monotonic() - float(
                            phase.get("since") or deadline
                        )
                        _LOG.warning(
                            "chat_stream: wall-clock deadline exceeded after "
                            "%.1fs — stalled in phase=%r for %.1fs",
                            timeout_s, _stalled, _stalled_for,
                        )
                        if not token_emitted:
                            yield {"type": "token", "content": _EMPTY_RESPONSE_FALLBACK}
                            token_emitted = True
                        yield {
                            "type": "error",
                            "message": (
                                f"Response timeout — stream exceeded "
                                f"the wall-clock timeout ({timeout_s:.0f}s) "
                                f"while waiting on {_stalled} "
                                f"({_stalled_for:.0f}s)."
                            ),
                        }
                        terminal_emitted = True
                        return

                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=remaining)
                    except asyncio.TimeoutError:
                        # Loop top will see remaining <= 0 and emit the error.
                        continue

                    if item is _SENTINEL:
                        # Producer finished — re-raise any exception it caught
                        # so the outer safety net can convert it to error+end.
                        # (Necessary to keep test_inner_generator_exception_...
                        # passing under the producer-task indirection.)
                        await producer_task
                        return

                    event = item
                    if event.get("type") == "token":
                        token_emitted = True
                    if event.get("type") == "tool_call":
                        _tn = event.get("tool") or event.get("name")
                        if _tn and _tn not in tools_seen:
                            tools_seen.append(_tn)
                    if event.get("type") in ("end", "error"):
                        terminal_emitted = True
                    yield event
            finally:
                # Cancel-then-gather so both tasks fully unwind even when the
                # wall-clock branch fires mid-stream. The bare cancel() alone
                # left coroutine warnings; gather with return_exceptions=True
                # swallows the CancelledError and any producer leftovers.
                producer_task.cancel()
                heartbeat_task.cancel()
                await asyncio.gather(
                    producer_task, heartbeat_task, return_exceptions=True,
                )

        _leak_guard = _EmitLeakGuard()
        try:
            async for event in _inner():
                event = _leak_guard.check(event)
                if event is None:
                    continue
                yield event
        except Exception as exc:  # noqa: BLE001 - last-line safety net
            _LOG.exception("chat_stream: generator escaped with exception")
            if not token_emitted:
                # Make sure the UI does NOT render an empty bubble. A token
                # event populates the bubble with the friendly fallback; the
                # error event then triggers the styled error banner.
                yield {"type": "token", "content": _EMPTY_RESPONSE_FALLBACK}
                token_emitted = True
            yield {"type": "error", "message": f"chat_stream crashed: {exc}"}
            terminal_emitted = True
            return

        # Inner generator completed without exception but emitted no terminal
        # event — synthesise one so the SSE consumer sees a clean close.
        if not terminal_emitted:
            _LOG.warning(
                "chat_stream: inner generator returned with no terminal event "
                "(token_emitted=%s) — emitting synthetic end",
                token_emitted,
            )
            if not token_emitted:
                yield {"type": "token", "content": _EMPTY_RESPONSE_FALLBACK}
            yield {"type": "end", "iterations": 0, "sources": [],
                   "tools": list(tools_seen)}

    async def _chat_stream_impl(
        self,
        user_message: str,
        history: list[dict[str, str]] | None = None,
        api_key: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        rag_debug: bool = False,
        attached_documents: list[dict[str, Any]] | None = None,
        _depth: int = 0,
        _call_stack: list[str] | None = None,
        _deadline: float | None = None,
        _phase: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Internal implementation. ``chat_stream`` wraps this with an emit
        guarantee so silent / crashing exits become structured error events."""
        cfg = _llm_config()
        # Names of the tools this turn actually invoked, in call order.
        #
        # Every `end` event carried iterations/model/sources/exports but never
        # the tools, so a turn that ran formula_executor_v2 arrived at the UI
        # with the field ABSENT and rendered as tools=[] -- while the answer
        # prose said "Tool: code". The two disagreed because only the prose had
        # the information. The TIMING log knew the names all along; the SSE
        # contract just never carried them.
        tools_invoked: list[str] = []

        def _note_tool(name: str | None) -> None:
            if name and name not in tools_invoked:
                tools_invoked.append(name)
        # Ollama (local / self-hosted) has no auth — skip the env-key
        # check. The empty bearer is ignored by Ollama's OAI endpoint.
        if cfg["env_key"]:
            api_key = api_key or os.getenv(cfg["env_key"])
            if not api_key:
                _LOG.warning("chat_stream: missing %s — yielding error", cfg["env_key"])
                yield {"type": "error", "message": f"No {cfg['env_key']} configured."}
                return
        else:
            api_key = api_key or ""

        _call_stack = _call_stack or [self.name]

        yield {"type": "start", "agent": self.name}

        # Zero-chunk project guardrail: refuse before spending LLM budget
        # unless the project has other (non-RAG) context such as facts.
        if (
            project_id
            and not project_is_rag_ready(project_id)
            and not _project_has_non_rag_context(project_id, user_message)
        ):
            if conversation_id:
                from app.core import agent_memory
                agent_memory.get_or_create_conversation(conversation_id, self.name, project_id)
                agent_memory.append_message(conversation_id, "user", user_message)
                agent_memory.append_message(conversation_id, "assistant", _UNINDEXED_PROJECT_MESSAGE)
            for chunk in _chunks(_UNINDEXED_PROJECT_MESSAGE, 80):
                yield {"type": "token", "content": chunk}
            yield {"type": "end", "iterations": 0, "sources": [],
                   "tools": list(tools_invoked)}
            return

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

        # Strip prior hallucinated WBS/BOQ tables from history before
        # sending. Prevents the model from pattern-matching to a prior
        # (often fabricated) table when it should be calling the tool.
        effective_history = _scrub_history(effective_history)

        messages = self._build_messages(user_message, effective_history, project_id=project_id)
        # Attachment grounding (S4). The chat router resolves the composer's
        # `[attached: X]` marker to concrete documents and passes them here.
        # A system note pins the reference so "the attached file" is never a
        # guessing game: the model gets the exact document_id to feed into
        # fetch_document or a file-consuming block.
        if attached_documents:
            _att_note = _build_attached_documents_note(attached_documents)
            if _att_note:
                messages.append({"role": "system", "content": _att_note})
        # Pre-iter-0 RAG injection. Runs for any project-scoped turn so that
        # routing to heavy-reasoning (or another agent) does not strip project
        # grounding. Adds a system message AFTER the prompt + project context
        # but BEFORE the latest user turn.
        _rag_sys_msg, _rag_audit = rag_inject(
            user_message=user_message,
            project_id=project_id,
            conversation_id=conversation_id,
            user_id=user_id,
            agent_name=self.name,
            history=effective_history,
        )
        if _rag_sys_msg and _rag_sys_msg.get("content"):
            _apply_rag_context(
                messages, _rag_sys_msg,
                user_data_authoritative=self.user_data_authoritative,
            )

        # Capability / self-introspection short-circuit: a question ABOUT this
        # agent (its tools, abilities, accessible documents) is answered from the
        # REAL tool roster + REAL document list, bypassing the grounded-RAG clamp
        # that would otherwise answer it from matched document text (e.g. "what
        # tools do you have" -> construction power saws). See _is_capability_request.
        if _is_capability_request(user_message):
            answer = _build_capability_answer(self, project_id)
            if conversation_id:
                from app.core import agent_memory
                agent_memory.append_message(conversation_id, "assistant", answer)
            for chunk in _chunks(answer, 80):
                yield {"type": "token", "content": chunk}
            yield {"type": "end", "iterations": 0, "sources": [],
                   "tools": list(tools_invoked)}
            return

        # Tool results accumulated this turn — feeds the schedule download offer
        # (a generate_wbs call becomes a 'Schedule (Excel)' export descriptor).
        stream_tool_results: list[dict[str, Any]] = []
        # Deterministic file pre-dispatch BEFORE the RAG-miss short-circuit
        # (leftover L1 timestamped .docx looks like an identifier).
        _locked = _message_wants_locked_deliverable(user_message)
        _pre = None
        if not _locked:
            _pre = await _predispatch_file_tool(self, messages, project_id)
        if _pre:
            _note_tool(_pre["name"])
            # SSE contract: the browser reads "tool" + "args_preview" on a
            # tool_call and "summary" on a tool_result -- see the main tool
            # loop below, which emits "name" only as an alias. This
            # pre-dispatch pair emitted the REVERSE (name, no tool), so the
            # deterministic file fast path -- the IFC and named-document
            # route -- rendered as a blank, unnamed entry in the UI. The turn
            # then narrated work the user could not see it do, which reads as
            # the assistant wandering rather than as a tool that ran.
            _pre_file = ""
            if isinstance(_pre.get("result"), dict):
                _pre_file = str(_pre["result"].get("filename") or "")
            _pre_summary = _summarize_result(_pre["result"])
            yield {"type": "tool_call",
                   "tool": _pre["name"],
                   "name": _pre["name"],  # alias, see the main loop
                   "args_preview": (
                       json.dumps({"filename": _pre_file})[:200]
                       if _pre_file else ""
                   ),
                   "predispatched": True}
            yield {"type": "tool_result",
                   "tool": _pre["name"],
                   "name": _pre["name"],  # alias, see the main loop
                   "ok": True,
                   "summary": _pre_summary[:400],
                   "result": _pre_summary,
                   "predispatched": True}

        _wbs_pre = await _predispatch_wbs_duration_override(self, messages)
        if _wbs_pre:
            _note_tool(_wbs_pre["name"])
            stream_tool_results.append(_wbs_pre)
            _wbs_summary = _summarize_result(_wbs_pre.get("result"))
            yield {"type": "tool_call",
                   "tool": _wbs_pre["name"],
                   "name": _wbs_pre["name"],
                   "args_preview": json.dumps({
                       "duration_overrides": (
                           (_wbs_pre.get("result") or {}).get(
                               "duration_overrides_applied"
                           )
                           if isinstance(_wbs_pre.get("result"), dict)
                           else None
                       ),
                   }, default=str)[:200],
                   "predispatched": True}
            yield {"type": "tool_result",
                   "tool": _wbs_pre["name"],
                   "name": _wbs_pre["name"],
                   "ok": True,
                   "summary": _wbs_summary[:400],
                   "result": _wbs_summary,
                   "predispatched": True}

        _hist_pre = await _predispatch_resource_histogram(
            self, messages, project_id,
        )
        if _hist_pre:
            _note_tool(_hist_pre["name"])
            stream_tool_results.append(_hist_pre)
            _hist_summary = _summarize_result(_hist_pre.get("result"))
            _hist_file = ""
            if isinstance(_hist_pre.get("result"), dict):
                _hist_file = str(
                    (_hist_pre.get("result") or {}).get("source") or ""
                )
            yield {"type": "tool_call",
                   "tool": _hist_pre["name"],
                   "name": _hist_pre["name"],
                   "args_preview": json.dumps({
                       "source": _hist_file,
                       "histogram_kind": (
                           (_hist_pre.get("result") or {}).get("histogram_kind")
                           if isinstance(_hist_pre.get("result"), dict)
                           else None
                       ),
                   }, default=str)[:200],
                   "predispatched": True}
            yield {"type": "tool_result",
                   "tool": _hist_pre["name"],
                   "name": _hist_pre["name"],
                   "ok": True,
                   "summary": _hist_summary[:400],
                   "result": _hist_summary,
                   "predispatched": True}

        _wir_pre = None
        if not _locked:
            _wir_pre = await _predispatch_wir_form(
                self, messages, project_id, operator_text=user_message,
            )
        if _wir_pre:
            _note_tool(_wir_pre["name"])
            stream_tool_results.append(_wir_pre)
            _wir_summary = _summarize_result(_wir_pre.get("result"))
            yield {"type": "tool_call",
                   "tool": _wir_pre["name"],
                   "name": _wir_pre["name"],
                   "args_preview": json.dumps({
                       "action": "wir_form",
                       "wir_number": (
                           (_wir_pre.get("result") or {}).get("wir_number")
                           if isinstance(_wir_pre.get("result"), dict)
                           else None
                       ),
                   }, default=str)[:200],
                   "predispatched": True}
            yield {"type": "tool_result",
                   "tool": _wir_pre["name"],
                   "name": _wir_pre["name"],
                   "ok": True,
                   "summary": _wir_summary[:400],
                   "result": _wir_summary,
                   "predispatched": True}

        _more_pre = None
        if not _hist_pre:
            _more_pre = await _predispatch_remaining_deliverables(
                self, messages, project_id, operator_text=user_message,
            )
        if _more_pre:
            _note_tool(_more_pre["name"])
            stream_tool_results.append(_more_pre)
            _more_summary = _summarize_result(_more_pre.get("result"))
            yield {"type": "tool_call",
                   "tool": _more_pre["name"],
                   "name": _more_pre["name"],
                   "args_preview": json.dumps({
                       "action": _more_pre["name"],
                   }, default=str)[:200],
                   "predispatched": True}
            yield {"type": "tool_result",
                   "tool": _more_pre["name"],
                   "name": _more_pre["name"],
                   "ok": True,
                   "summary": _more_summary[:400],
                   "result": _more_summary,
                   "predispatched": True}

        # Fast path: exact reference miss with no RAG context. Skip when a
        # named project file was already fetched/extracted from disk.
        if not _pre and not _wbs_pre and not _hist_pre and not _wir_pre and not _more_pre and _should_short_circuit_rag_miss(
            _rag_audit, _rag_sys_msg, user_message
        ):
            answer = _build_missing_reference_answer(project_id, user_id)
            if conversation_id:
                from app.core import agent_memory
                agent_memory.append_message(conversation_id, "assistant", answer)
            for chunk in _chunks(answer, 80):
                yield {"type": "token", "content": chunk}
            yield {"type": "end", "iterations": 0, "sources": [],
                   "tools": list(tools_invoked)}
            return
        # Root fix for the tool-loop: RAG context is injected pre-loop, yet the
        # model keeps re-calling search_project_documents when an exact value
        # isn't found — grinding to the iteration cap. Allow a few explicit
        # searches, then STOP offering the tool so the model must answer from what
        # it has (or honestly say it doesn't) instead of looping. generate_wbs and
        # other tools stay available.
        SEARCH_TOOL_CAP = int(os.getenv("AGENT_SEARCH_TOOL_CAP", "2"))
        search_calls = 0
        excluded_tools: set = set()
        # Once a deliverable (non-search) tool has returned its result, the model
        # has what it needs — the next call is synthesis. Offering tools on that
        # synthesis call is pure downside: it lets the model loop, and on Groq a
        # large context makes Llama's prose synthesis fail the tool-use validator
        # (HTTP 400 tool_use_failed) or blow the free-tier TPM (429) — each then
        # falls back to slow gpt-oss and the turn appears to hang for minutes.
        # Force a tool-free synthesis instead. Env-disable: AGENT_FORCE_SYNTHESIS=0.
        # Live M1/M2/M6/M9/M13/M14: remaining already drafted the artifact,
        # but force_synthesis stayed False so a later steal tool overwrote it.
        force_synthesis = bool(_wbs_pre or _hist_pre or _wir_pre or _more_pre)
        for _hit in (_wbs_pre, _hist_pre, _wir_pre, _more_pre):
            if _hit:
                excluded_tools |= _conflicting_tools_after_predispatch(
                    str(_hit.get("name") or "")
                )
        error_nudges = 0
        _force_synth_enabled = os.getenv("AGENT_FORCE_SYNTHESIS", "1") != "0"
        # True token streaming for the FINAL synthesis call only. Gated to:
        #   * SYNTHESIS_STREAMING=1  (instant prod kill-switch; default off)
        #   * provider == groq       (the only verified streaming path)
        # When enabled, the force_synthesis iteration streams provider deltas as
        # token events instead of computing the whole answer then re-chunking it
        # (which made first_token ~= total). Any pre-first-token failure falls
        # back to the untouched non-streaming path below.
        _synth_stream_enabled = (
            os.getenv("SYNTHESIS_STREAMING") == "1"
            # Kimi is the production primary; gating on Groq alone made
            # SYNTHESIS_STREAMING=1 a dead switch in prod. Keep this list in
            # step with the allowlist in _stream_synthesis.
            and cfg["provider"] in ("groq", "kimi")
            # rag_debug needs the whole final text to run its with/without-RAG
            # A/B in the non-streaming branch; don't stream those turns.
            and not rag_debug
            # Token streaming yields the answer BEFORE _postprocess_answer can
            # act, so a fabricated price would already be on the client with
            # only the persisted copy corrected (Codex #223/#224). For
            # COST-SHAPED queries only, the gate wins: streaming disables itself
            # so the whole answer is price-gated before emission. Non-cost turns
            # stream normally. (The standards advisory is non-blocking, so under
            # streaming it remains best-effort on the persisted copy — an
            # accepted minor gap; streaming is off in prod regardless.)
            and not (_cost_grounding_enabled() and _is_cost_shaped_query(user_message))
        )
        # WARNING-level timing instrumentation: Render drops INFO app logs on this
        # service, so the deliverable-hang call-count/latency was invisible.
        # Gate behind AGENT_TIMING_LOG=1 so it's off by default.
        _timing = os.getenv("AGENT_TIMING_LOG") == "1"
        _turn_t0 = time.monotonic()

        # `_deadline` is the caller's wall-clock cap for the WHOLE turn (a
        # time.monotonic() instant), or None when nothing is capping us.
        # Every LLM call below gets it minus a delivery margin, so a call
        # that runs out of budget fails inside the turn and we still stream
        # something instead of being cancelled mid-await.
        def _remaining_budget() -> float | None:
            return None if _deadline is None else _deadline - time.monotonic()

        def _llm_deadline() -> float | None:
            if _deadline is None:
                return None
            # Reserve a slice of what is LEFT rather than a flat 10s, so a
            # deliberately short CHAT_STREAM_TIMEOUT_SECONDS (the test suite
            # runs 1-3s) does not hand every LLM call a deadline already in
            # the past. At the 240s production default this is the full 10s.
            left = max(0.0, _deadline - time.monotonic())
            return _deadline - min(_STREAM_DELIVERY_MARGIN_SECONDS, left * 0.1)

        # What the turn is currently waiting on, so the caller's wall-clock
        # branch can NAME the stalled component instead of reporting a bare
        # elapsed time. Diagnosing 43e40b3a-e8f needed the ABSENCE of a
        # "forced-retry call=" line to infer where it hung.
        def _set_phase(name: str) -> None:
            if _phase is not None:
                _phase["name"] = name
                _phase["since"] = time.monotonic()
        # Served model string of the LAST successful LLM call this turn, surfaced
        # in the `end` event so observability (fork_cli/smoke) can tell the
        # configured provider apart from a silent fallback (e.g. Kimi K2 vs an
        # Ollama fallback) per run. The model string alone is a sufficient
        # discriminator; None until the first successful call.
        served_model: str | None = None
        for iteration in range(MAX_TOOL_ITERATIONS):
            _LOG.info("chat_stream: iter=%d agent=%s force_synthesis=%s", iteration, self.name, force_synthesis)
            # ── True token streaming for the forced synthesis call ────────────
            # force_synthesis is set only AFTER a deliverable tool returns, so
            # this call is with_tools=False — no tool_calls can appear, honouring
            # "tool iterations stay non-streaming". Stream its provider deltas as
            # token events. On any pre-first-token failure we fall through to the
            # unchanged non-streaming path below (streaming is never a one-way
            # door). A mid-stream drop finishes with whatever streamed.
            if force_synthesis and _synth_stream_enabled:
                streamed_any = False
                fell_back = False
                tool_leak = False
                acc: list[str] = []
                pending = ""
                try:
                    async for _delta in self._stream_synthesis(
                        messages, api_key, project_id=project_id, user_id=user_id,
                    ):
                        streamed_any = True
                        acc.append(_delta)
                        pending += _delta
                        raw_so_far = "".join(acc)
                        # Leftover L4 / UI-PHYS A5: SYNTHESIS_STREAMING must not
                        # flush XML tool markup or raw tool-call JSON to the client
                        # before sanitization (frontend keeps accumulated tokens).
                        if (
                            _looks_like_xml_tool_leak(raw_so_far)
                            or _looks_like_internal_tool_json(raw_so_far)
                            or _looks_like_internal_context_leak(raw_so_far)
                        ):
                            tool_leak = True
                            pending = ""
                            continue
                        if len(raw_so_far) < 60 and "\n" not in raw_so_far:
                            continue
                        nl = pending.rfind("\n")
                        if nl >= 0:
                            seg, pending = pending[: nl + 1], pending[nl + 1:]
                            seg = _sanitize_inline_paths(_sanitize_citation_labels(seg))
                            if seg and not _looks_like_internal_tool_json(seg):
                                yield {"type": "token", "content": seg}
                except _SynthStreamError as _se:
                    if streamed_any:
                        _LOG.warning("chat_stream: synthesis stream dropped mid-way (%s)", _se)
                    else:
                        _LOG.info("chat_stream: synthesis stream unavailable (%s) — non-streaming fallback", _se)
                        fell_back = True
                if not fell_back:
                    raw = "".join(acc)
                    # No context-leak check here on purpose. The in-loop
                    # guard above runs on `raw_so_far` after EVERY delta,
                    # including the last, so a repeat at this point cannot
                    # fail on any input the loop would have passed -- no test
                    # can kill it, and a guard no test can kill is not a
                    # guard. The chokepoint in _sanitize_final_text, which
                    # `final_text` goes through two lines down, is the
                    # backstop.
                    if (
                        _looks_like_xml_tool_leak(raw)
                        or _looks_like_internal_tool_json(raw)
                    ):
                        tool_leak = True
                    # Fully-sanitised accumulated text: what we persist + feed
                    # sources/exports (must match the non-streaming path, not a
                    # concatenation of per-line flushes). Computed BEFORE the
                    # tail flush so a dangling promise can be held back rather
                    # than shown and then contradicted.
                    final_text = _sanitize_inline_paths(
                        _sanitize_citation_labels(_sanitize_final_text(
                            raw, messages=messages, tool_results=stream_tool_results,
                        ))
                    )
                    # #454 taught the NON-streamed branch that a first-person
                    # promise to search is not an answer. This branch never
                    # learned it: it retried only on an EMPTY stream, and a
                    # promise is not empty. SYNTHESIS_STREAMING is set in
                    # production, so this is the branch a live turn takes.
                    #
                    # Live on 1594b32, with #454 and #455 both deployed, a
                    # fresh thread asking for the Engineer's Representative
                    # ended on:
                    #
                    #   "I don't have the Engineer's Representative's name in
                    #    the retrieved excerpts. Let me search the Contract
                    #    Data and Schedules volume more specifically for that
                    #    appointment."
                    #
                    # _SEARCH_PROMISE_TAIL_RE matches that string. The
                    # non-streamed branch would have retried. This one shipped
                    # it as the answer.
                    #
                    # Held rather than flushed: the loop above releases a
                    # segment only once 60 chars AND a newline have
                    # accumulated, so a short end-of-turn promise is still
                    # entirely in `pending` here and the user has seen nothing.
                    # On a long answer that ends in a promise some prefix is
                    # already out and the retry appends to it -- uglier, and
                    # still better than a promise as the final word.
                    promise_hold = bool(final_text.strip()) and (
                        _final_text_needs_forced_retry(
                            final_text, user_message=user_message
                        )
                    )
                    if promise_hold:
                        _LOG.info(
                            "chat_stream: streamed synthesis ended on a search "
                            "promise -- holding it and forcing a no-tools retry"
                        )
                    # A context leak is not the same failure as an XML/JSON
                    # tool-call leak, and must not share its outcome. There,
                    # the model wanted a tool it no longer had, and the
                    # controlled fallback ("retry or narrow the question") is
                    # all that is left. Here the model was HANDED the answer
                    # and copied the packaging instead of reading it -- the
                    # Delay Damages rate the live leak buried was 0.1% of the
                    # Contract Price per calendar day, sitting in the very
                    # excerpts it echoed. Serving the dead-end fallback for
                    # that would be a second failure stacked on the first, so
                    # this takes the retry path #456 built instead.
                    leak_hold = bool(raw.strip()) and _looks_like_internal_context_leak(raw)
                    if leak_hold:
                        _LOG.warning(
                            "chat_stream: streamed synthesis returned the "
                            "platform's own context -- suppressing it and "
                            "forcing a no-tools retry"
                        )
                    # Do not flush a held tool-leak tail — leftover L4 / A5
                    # streaming used to emit tool JSON/XML here before sanitize.
                    if pending and not tool_leak and not promise_hold:
                        seg = _sanitize_inline_paths(_sanitize_citation_labels(pending))
                        if seg and not _looks_like_internal_tool_json(seg):
                            yield {"type": "token", "content": seg}
                    # Recover-from-tools lives in _postprocess_answer. Do not
                    # run it before the empty-stream check — a successful
                    # commissioning/WIR/IPC tool (or predispatch draft) would
                    # fill the blank and skip the forced non-streaming retry
                    # that test_empty_stream_triggers_forced_retry pins.
                    if tool_leak and not leak_hold and final_text.strip():
                        final_text = _postprocess_answer(
                            final_text, _rag_sys_msg, messages,
                            fallback_used=bool(_rag_audit.get("fallback_used")),
                            agent_name=self.name,
                        )
                        for chunk in _chunks(final_text, 80):
                            yield {"type": "token", "content": chunk}
                    elif not final_text.strip() or promise_hold or leak_hold:
                        # Nothing usable streamed -- empty, or a promise to
                        # search that #454 already ruled is not an answer. One
                        # path for both, so the two branches cannot disagree
                        # about what counts as an answer again.
                        # leak_hold first: it is the more specific diagnosis,
                        # and it is reached with promise_hold ALSO set, because
                        # _sanitize_final_text has by then replaced the leak
                        # with _TOOL_FORMAT_FALLBACK -- whose own "retry or
                        # narrow the question" wording trips the search-promise
                        # detector. Ordering the other way would send the model
                        # "stop promising to search" for a turn where it never
                        # promised anything.
                        if leak_hold:
                            messages.append(
                                {"role": "user", "content": _CONTEXT_LEAK_RETRY_NUDGE}
                            )
                        elif promise_hold:
                            messages.append(
                                {"role": "user", "content": _SEARCH_PREAMBLE_RETRY_NUDGE}
                            )
                        _set_phase("forced-retry (streamed-synth)")
                        forced_resp = await self._call_llm(
                            messages, api_key, project_id=project_id,
                            with_tools=False, user_id=user_id,
                            deadline=_llm_deadline(),
                        )
                        if forced_resp.get("status") == "error":
                            final_text = _EMPTY_RESPONSE_FALLBACK
                        else:
                            served_model = (forced_resp.get("raw") or {}).get("model") or served_model
                            _fm = forced_resp["choice"].get("message") or {}
                            final_text = _sanitize_final_text(
                                _fm.get("content") or "",
                                messages=messages, tool_results=stream_tool_results,
                            )
                            # No leak check on final_text here: the
                            # _sanitize_final_text call two lines up has
                            # already turned any leak into
                            # _TOOL_FORMAT_FALLBACK, which this condition
                            # catches. Repeating it reads like a second guard
                            # and is one no test can kill. Pinned instead by
                            # test_a_retry_that_leaks_again_is_refused_too,
                            # which asserts the OUTCOME rather than the line.
                            if not final_text.strip() or _final_text_needs_forced_retry(
                                final_text, user_message=user_message
                            ):
                                final_text = _EMPTY_RESPONSE_FALLBACK
                        final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
                        final_text = _postprocess_answer(final_text, _rag_sys_msg, messages, fallback_used=bool(_rag_audit.get("fallback_used")), agent_name=self.name)
                        for chunk in _chunks(final_text, 80):
                            yield {"type": "token", "content": chunk}
                    else:
                        final_text = _postprocess_answer(
                            final_text, _rag_sys_msg, messages,
                            fallback_used=bool(_rag_audit.get("fallback_used")),
                            agent_name=self.name,
                        )
                    if _timing:
                        _LOG.warning("TIMING chat_stream STREAMED-SYNTH iter=%d chars=%d cum=%.1fs",
                                     iteration, len(final_text), time.monotonic() - _turn_t0)
                    if conversation_id:
                        from app.core import agent_memory
                        agent_memory.append_message(conversation_id, "assistant", final_text)
                    yield {
                        "type": "end",
                        "content": final_text,
                        "iterations": iteration + 1,
                        "model": served_model,
                        "tools": list(tools_invoked),
                        "sources": _build_sources_from_audit(_rag_audit, final_text),
                        "exports": _build_exports_from_audit(_rag_audit, final_text, stream_tool_results),
                    }
                    return
            _call_t0 = time.monotonic()
            _set_phase(f"llm-call iter={iteration}")
            resp = await self._call_llm(
                messages, api_key, project_id=project_id, user_id=user_id,
                exclude_tools=excluded_tools or None,
                with_tools=not force_synthesis,
                deadline=_llm_deadline(),
            )
            if _timing:
                _tcs = [((tc.get("function") or {}).get("name")) for tc in (resp.get("choice", {}).get("message", {}).get("tool_calls") or [])]
                _LOG.warning("TIMING chat_stream iter=%d call=%.1fs status=%s tools=%s cum=%.1fs",
                             iteration, time.monotonic() - _call_t0, resp.get("status"),
                             _tcs or "final", time.monotonic() - _turn_t0)
            if resp.get("status") == "error":
                err = resp.get("error", "LLM call failed")
                recovered = _recover_answer_from_tool_messages(err, messages)
                if recovered != err and not _text_needs_tool_recovery(recovered):
                    final_text = _sanitize_inline_paths(
                        _sanitize_citation_labels(recovered)
                    )
                    final_text = _postprocess_answer(
                        final_text, _rag_sys_msg, messages,
                        fallback_used=bool(_rag_audit.get("fallback_used")),
                        agent_name=self.name,
                    )
                    _LOG.warning(
                        "chat_stream: iter=%d recovered deliverable after LLM error %s",
                        iteration, err[:80],
                    )
                    for chunk in _chunks(final_text, 80):
                        yield {"type": "token", "content": chunk}
                    if conversation_id:
                        from app.core import agent_memory
                        agent_memory.append_message(
                            conversation_id, "assistant", final_text,
                        )
                    yield {
                        "type": "end",
                        "content": final_text,
                        "iterations": iteration + 1,
                        "model": served_model,
                        "tools": list(tools_invoked),
                        "recovered_from_llm_error": True,
                        "sources": _build_sources_from_audit(_rag_audit, final_text),
                        "exports": _build_exports_from_audit(
                            _rag_audit, final_text, stream_tool_results,
                        ),
                    }
                    return
                _LOG.warning("chat_stream: iter=%d LLM error %s", iteration, err)
                _persist_failed_turn(conversation_id)
                yield {"type": "error", "message": err}
                return
            served_model = (resp.get("raw") or {}).get("model") or served_model
            assistant_msg = resp["choice"].get("message") or {}
            tool_calls = assistant_msg.get("tool_calls") or []
            raw_content = assistant_msg.get("content") or ""

            # DeepSeek sometimes emits the tool call as inline DSML markup in
            # `content` with an empty structured `tool_calls` field. Groq
            # Llama-4-Scout sometimes leaks raw search args as JSON content.
            # Recover either shape before treating the turn as a final answer.
            if not tool_calls:
                cleaned_content, dsml_tool_calls = _parse_dsml_tool_calls(raw_content)
                recovered_tool_calls = (
                    _recover_tool_calls_from_content(raw_content)
                    if not dsml_tool_calls else []
                )
                # Leftover L4: XML/DSML leaked during force_synthesis is the
                # answer to sanitize/graft, not another tool round.
                if force_synthesis and (dsml_tool_calls or recovered_tool_calls):
                    dsml_tool_calls = []
                    recovered_tool_calls = []
                # UI-PHYS A5: once search is capped, do not re-execute the same
                # leaked search envelope — fall through to sanitize + synthesis.
                if (
                    recovered_tool_calls
                    and _recovered_calls_are_search_only(recovered_tool_calls)
                    and (
                        "search_project_documents" in excluded_tools
                        or search_calls >= SEARCH_TOOL_CAP
                    )
                ):
                    recovered_tool_calls = []
                if dsml_tool_calls:
                    # Treat as a tool-calling turn — do NOT stream the markup.
                    tool_calls = dsml_tool_calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": cleaned_content,
                        "tool_calls": dsml_tool_calls,
                    }
                elif recovered_tool_calls:
                    tool_calls = recovered_tool_calls
                    assistant_msg = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": recovered_tool_calls,
                    }
                else:
                    # Final answer — sanitize DSML markup, raw tool JSON, and
                    # empty content before streaming it to the user.
                    final_text = _sanitize_final_text(
                        raw_content, messages=messages, tool_results=stream_tool_results,
                    )
                    # If sanitization left nothing usable (empty or raw tool JSON
                    # fallback), force one no-tools call so the model must produce
                    # a plain-text answer instead of an empty bubble or leak.
                    if _final_text_needs_forced_retry(final_text, user_message=user_message):
                        _left = _remaining_budget()
                        _need = _forced_retry_min_seconds()
                        if _left is not None and _left < _need:
                            # Not enough turn left to finish another LLM call.
                            # Starting one anyway is exactly how 43e40b3a-e8f
                            # spent its last 113s and still ended on a timeout
                            # banner. Deliver the fallback NOW instead: same
                            # text, ~2 minutes sooner, no error banner. The
                            # unusable text itself is never streamed -- that is
                            # the defect #454 closed and it stays closed.
                            _LOG.warning(
                                "chat_stream: skipping forced retry — %.1fs of "
                                "the turn left, need %.1fs",
                                _left, _need,
                            )
                            if _timing:
                                _LOG.warning(
                                    "TIMING chat_stream FORCED-RETRY-SKIPPED "
                                    "raw=%dc left=%.1fs cum=%.1fs",
                                    len(raw_content), _left,
                                    time.monotonic() - _turn_t0,
                                )
                            final_text = _EMPTY_RESPONSE_FALLBACK
                        else:
                            _LOG.info("chat_stream: unusable final_text, forcing no-tools retry")
                            if _timing:
                                _LOG.warning("TIMING chat_stream EMPTY-FINAL raw=%dc -> forced retry, cum=%.1fs",
                                             len(raw_content), time.monotonic() - _turn_t0)
                            if final_text == _TOOL_FORMAT_FALLBACK:
                                messages.append({"role": "user", "content": _TOOL_FORMAT_RETRY_NUDGE})
                            elif _looks_like_search_preamble(final_text):
                                messages.append({"role": "user", "content": _SEARCH_PREAMBLE_RETRY_NUDGE})
                            _fr_t0 = time.monotonic()
                            _set_phase("forced-retry")
                            forced_resp = await self._call_llm(
                                messages, api_key, project_id=project_id,
                                with_tools=False, user_id=user_id,
                                deadline=_llm_deadline(),
                            )
                            if _timing:
                                _LOG.warning("TIMING chat_stream forced-retry call=%.1fs status=%s cum=%.1fs",
                                             time.monotonic() - _fr_t0, forced_resp.get("status"), time.monotonic() - _turn_t0)
                            if forced_resp.get("status") == "error":
                                final_text = _EMPTY_RESPONSE_FALLBACK
                            else:
                                served_model = (forced_resp.get("raw") or {}).get("model") or served_model
                                forced_msg = forced_resp["choice"].get("message") or {}
                                final_text = _sanitize_final_text(
                                    forced_msg.get("content") or "",
                                    messages=messages, tool_results=stream_tool_results,
                                )
                                if _final_text_needs_forced_retry(final_text, user_message=user_message):
                                    final_text = _EMPTY_RESPONSE_FALLBACK
                    final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
                    final_text = _postprocess_answer(final_text, _rag_sys_msg, messages, fallback_used=bool(_rag_audit.get("fallback_used")), agent_name=self.name)
                    if _timing:
                        _LOG.warning("TIMING chat_stream STREAMING-FINAL iter=%d chars=%d cum=%.1fs",
                                     iteration, len(final_text), time.monotonic() - _turn_t0)
                    _set_phase(f"streaming-final iter={iteration}")
                    _LOG.info("chat_stream: final_text iter=%d chars=%d", iteration, len(final_text))
                    for chunk in _chunks(final_text, 80):
                        yield {"type": "token", "content": chunk}
                    if conversation_id:
                        from app.core import agent_memory
                        # User turn was already persisted up front.
                        agent_memory.append_message(conversation_id, "assistant", final_text)
                    # rag_debug opt-in: run a second LLM call with the RAG
                    # system message stripped so the caller can compare
                    # on/off responses for the same turn. Audit record is
                    # passed through unmodified for downstream inspection.
                    if rag_debug and _rag_sys_msg is not None:
                        no_rag_messages = [m for m in messages if m is not _rag_sys_msg]
                        try:
                            no_rag_resp = await self._call_llm(
                                no_rag_messages, api_key,
                                project_id=project_id, user_id=user_id,
                                deadline=_llm_deadline(),
                            )
                            off_response = (no_rag_resp.get("choice", {})
                                            .get("message", {})
                                            .get("content", "") or "")
                        except Exception as _e:
                            off_response = f"[rag_debug off-run failed: {_e}]"
                        yield {
                            "type": "end",
                            "content": final_text,
                            "iterations": iteration + 1,
                            "tools": list(tools_invoked),
                            "rag_debug": {
                                "on_response": final_text,
                                "off_response": off_response,
                                "audit": _rag_audit,
                            },
                        }
                        return
                    yield {
                        "type": "end",
                        "content": final_text,
                        "iterations": iteration + 1,
                        "model": served_model,
                        "tools": list(tools_invoked),
                        "sources": _build_sources_from_audit(_rag_audit, final_text),
                        "exports": _build_exports_from_audit(_rag_audit, final_text, stream_tool_results),
                    }
                    return

            tool_calls = _normalize_tool_call_ids(list(tool_calls))
            assistant_msg = {**assistant_msg, "tool_calls": tool_calls}
            messages.append(assistant_msg)
            pending_nudges: list[dict[str, Any]] = []
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                if fn.get("name") == "search_project_documents":
                    search_calls += 1
                    if search_calls >= SEARCH_TOOL_CAP:
                        # Stop offering the search tool for the rest of this turn —
                        # the model has searched enough; force it to answer.
                        excluded_tools.add("search_project_documents")
                # SSE contract (distinct from the ``on_event`` callback above,
                # which uses "name"/"args"/"id"): the browser reads "tool" and
                # "args_preview" — see frontend ProjectWorkspace.tsx. "name" is
                # emitted as an ALIAS so a consumer written against the callback
                # shape reads the tool name instead of silently getting None.
                _note_tool(fn.get("name"))
                yield {
                    "type": "tool_call",
                    "tool": fn.get("name"),
                    "name": fn.get("name"),
                    "args_preview": (fn.get("arguments") or "")[:200],
                }
                tool_result = await self._run_tool_call(
                    tc,
                    api_key=api_key,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    _depth=_depth,
                    _call_stack=_call_stack,
                    user_message=user_message,
                    history=effective_history,
                )
                stream_tool_results.append(tool_result)
                _tool_content = _tool_result_content(
                    {**(tool_result["result"] if isinstance(tool_result.get("result"), dict) else {"result": tool_result.get("result")}),
                     **({"validation": tool_result["validation"]} if "validation" in tool_result else {})},
                    offset=_requested_char_offset(tc),
                )
                # A successful deliverable tool (anything but search) means the
                # model now has authoritative data to synthesize from — stop
                # offering tools so the next call is a clean, tool-free answer.
                # Unless it does NOT have the data yet: a result with windows
                # left to read is half a document, and disarming here leaves
                # the model holding an instruction to read the rest with no
                # tool to read it with.
                if (
                    _force_synth_enabled
                    and tool_result.get("ok", True)
                    and _should_force_synthesis(tool_result)
                    and not _has_unread_windows(_tool_content)
                ):
                    force_synthesis = True
                yield {
                    "type": "tool_result",
                    "tool": tool_result["name"],
                    "name": tool_result["name"],  # alias, see tool_call above
                    "ok": tool_result.get("ok", True),
                    "summary": _summarize_result(tool_result["result"])[:400],
                }
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": tool_result["name"],
                    "content": _tool_content,
                })
                if _empty_router_verdict(tool_result):
                    pending_nudges.append({"role": "user", "content": _EMPTY_ROUTER_NUDGE})
                else:
                    if (_tool_result_errored(tool_result)
                            and error_nudges < _TOOL_ERROR_NUDGE_CAP):
                        error_nudges += 1
                        pending_nudges.append({"role": "user",
                                         "content": _nudge_for_failed_tool(
                                             tool_result, self)
                                         + _file_tool_hint(messages, project_id,
                                                           self.allowed_blocks)})
            messages.extend(pending_nudges)

        # Hit the cap without a final answer — force one more call with tools disabled.
        _LOG.warning("chat_stream: hit MAX_TOOL_ITERATIONS=%d, forcing no-tools retry",
                     MAX_TOOL_ITERATIONS)
        _set_phase("forced-retry (iteration cap)")
        forced_resp = await self._call_llm(
            messages, api_key, project_id=project_id, with_tools=False,
            user_id=user_id, deadline=_llm_deadline(),
        )
        if forced_resp.get("status") == "error":
            _persist_failed_turn(conversation_id)
            yield {"type": "error", "message": f"Hit {MAX_TOOL_ITERATIONS}-iteration cap."}
            return
        served_model = (forced_resp.get("raw") or {}).get("model") or served_model
        forced_msg = forced_resp["choice"].get("message") or {}
        final_text = _sanitize_final_text(
            forced_msg.get("content") or "",
            messages=messages, tool_results=stream_tool_results,
        )
        if _final_text_needs_forced_retry(final_text, user_message=user_message):
            # Forced retry returned empty or raw tool JSON — substitute the
            # user-safe fallback so the UI never renders an empty bubble.
            _LOG.warning("chat_stream: forced final unusable, using fallback")
            final_text = _EMPTY_RESPONSE_FALLBACK
        final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
        final_text = _postprocess_answer(final_text, _rag_sys_msg, messages, fallback_used=bool(_rag_audit.get("fallback_used")), agent_name=self.name)
        for chunk in _chunks(final_text, 80):
            yield {"type": "token", "content": chunk}
        if conversation_id:
            from app.core import agent_memory
            # User turn was already persisted up front.
            agent_memory.append_message(conversation_id, "assistant", final_text)
        _LOG.info("chat_stream: end (forced_final) chars=%d", len(final_text))
        yield {
            "type": "end",
            "content": final_text,
            "iterations": MAX_TOOL_ITERATIONS,
            "forced_final": True,
            "model": served_model,
            "tools": list(tools_invoked),
            "sources": _build_sources_from_audit(_rag_audit, final_text),
            "exports": _build_exports_from_audit(_rag_audit, final_text, stream_tool_results),
        }

    # ── Internals ─────────────────────────────────────────────────────────
    def _build_messages(
        self,
        user_message: str,
        history: list[dict[str, str]],
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

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

        # CM cross-domain inject (compact metadata; preserves RAG grounding).
        if self.name in _CM_INJECT_AGENTS:
            cm_fragment = _cm_prompt_fragment_for_turn(user_message)
            if cm_fragment:
                msgs.append({"role": "system", "content": cm_fragment})

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
        messages: list[dict[str, Any]],
        api_key: str,
        project_id: str | None = None,
        with_tools: bool = True,
        user_id: str | None = None,
        exclude_tools: set | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        cfg = _llm_config()
        # Soft daily cap: refuse the call when today's spend already meets
        # USAGE_DAILY_CAP_USD for this user. Only enforced for authenticated
        # callers — internal calls without a user_id are not capped (they
        # shouldn't be billable in the first place). A missing / unparseable
        # / non-positive cap disables the check entirely.
        if user_id:
            try:
                cap = float(os.getenv("USAGE_DAILY_CAP_USD") or "0")
            except ValueError:
                cap = 0.0
            if cap > 0:
                try:
                    from app.core import usage_tracker
                    if usage_tracker.is_over_cap(user_id, cap):
                        today = usage_tracker.daily_total(user_id)
                        return {
                            "status": "error",
                            "error": (
                                f"Daily LLM cost cap reached: "
                                f"${today['cost_usd']:.4f} >= ${cap:.4f} "
                                f"(USAGE_DAILY_CAP_USD). Retry after 00:00 UTC."
                            ),
                        }
                except Exception:  # noqa: BLE001
                    # A broken usage tracker must never block a real call.
                    _LOG.warning(
                        "swallowed %s in _call_llm() — continuing",
                        "Exception", exc_info=True,
                    )
        # An agent that pinned a provider-specific model is left alone; an
        # unpinned/legacy-placeholder agent uses the active provider's default
        # (Kimi primary / Groq fallback / Ollama on-prem, from _llm_config).
        model = self.model
        if not model or model.startswith(("deepseek-", "gpt-4", "gpt-3")):
            model = cfg["default_model"]
        # Whitelist-sanitise every outbound message (drops `reasoning` and any
        # other non-standard field that would make a strict provider — Groq —
        # reject the request). Single chokepoint, covers all callers.
        messages = _sanitize_messages_for_provider(messages)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        tools = self.tool_definitions(project_id=project_id)
        if exclude_tools:
            tools = [
                t for t in tools
                if t.get("function", {}).get("name") not in exclude_tools
            ]
        if tools and with_tools:
            payload["tools"] = tools
            # When the latest user message names a deliverable (schedule,
            # WBS, BOQ, cost estimate, etc.), force the model to emit a
            # tool call instead of drifting into prose. Gated to the
            # project-assistant agent because other agents (e.g.
            # heavy-reasoning) have their own discipline + may legitimately
            # answer in prose on the same keywords. Q&A queries that don't
            # name a deliverable keep tool_choice="auto" as before.
            tool_names = {t.get("function", {}).get("name") for t in tools}
            forced_tool = (
                _forced_specific_tool(messages, tool_names)
                if self.name in ("project-assistant", "heavy-reasoning") else None
            )
            requires_tool = (
                self.name == "project-assistant"
                and _user_intent_requires_tool(messages)
            )
        else:
            forced_tool = None
            requires_tool = False

        # Provider attempts: primary first, then the full fallback ladder
        # (same-provider model, then cross-provider). Retryable failures
        # (413/429/5xx/timeout) walk the ladder. Auth/validation 401/403/404
        # and generic 400s are not retried. Kimi conversation-shape 400s
        # (orphaned tool_call_id / tokenization) ARE retried, skipping any
        # remaining same-provider hop — another Moonshot model 400s the
        # same way; Groq is the recovery.
        attempts = [(cfg, api_key, model)]
        for fb in _llm_fallback_ladder(cfg):
            fb_key = os.getenv(fb["env_key"]) if fb.get("env_key") else ""
            attempts.append((fb, fb_key, fb["default_model"]))

        def _is_retryable(status: int) -> bool:
            return status in (408, 413, 429) or status >= 500

        def _tool_choice_for(provider: str):
            # Forcing tool_choice HANGS/400s on Groq: Llama-4-Scout emits the
            # tool as PROSE under a large prod context -> HTTP 400
            # tool_use_failed -> the streaming tool-loop retries/loops. Forcing
            # ALSO does nothing on gpt-oss, which ignores tool_choice outright.
            # So only force on providers that honor it without hard-failing;
            # Groq uses "auto", where Llama calls tools cleanly on its own and
            # the turn always completes. Decided PER-ATTEMPT so a fallback to a
            # different provider gets the right value.
            if provider in ("groq", "kimi"):
                # Groq: forcing tool_choice makes Llama-4-Scout emit the tool as
                # PROSE -> HTTP 400 tool_use_failed. Kimi K2: forcing a specific
                # tool 400s outright ("tool_choice 'specified' is incompatible
                # with thinking enabled" — K2 is a reasoning model). Both call
                # tools cleanly on "auto", so never force them.
                return "auto"
            if forced_tool:
                # Force THIS tool by name — "required" alone let the model pick
                # the wrong tool (search) and bypass the authoritative data.
                return {"type": "function", "function": {"name": forced_tool}}
            if requires_tool:
                return "required"
            return "auto"

        # Wall-clock budget. chat_stream caps the whole TURN; _call_llm walks
        # a provider fallback ladder INSIDE that cap, so without a shared
        # deadline the ladder can spend _llm_http_timeout() per hop (200s by
        # default) against a 240s turn cap and return nothing at all.
        #
        # Live request 43e40b3a-e8f: iter=0 took 126.8s and produced a dangling
        # search preamble, the forced no-tools retry started at cum=126.8s, and
        # the turn deadline cancelled the producer 113s later while that retry
        # was still awaiting -- there is no "forced-retry call=" timing line for
        # that request because the await never returned. The user waited four
        # minutes and got a timeout banner.
        #
        # `deadline` is a time.monotonic() instant, not a duration.
        def _attempt_timeout() -> float:
            base = _llm_http_timeout()
            if deadline is None:
                return base
            return max(
                _MIN_LLM_ATTEMPT_SECONDS,
                min(base, deadline - time.monotonic()),
            )

        def _budget_exhausted() -> bool:
            return (
                deadline is not None
                and (deadline - time.monotonic()) <= _MIN_LLM_ATTEMPT_SECONDS
            )

        last_error: dict[str, Any] = {"status": "error", "error": "LLM call failed"}
        skip_providers: set[str] = set()
        for attempt_idx, (a_cfg, a_key, a_model) in enumerate(attempts):
            is_last = attempt_idx == len(attempts) - 1
            if _budget_exhausted():
                _LOG.warning(
                    "llm: skipping %s attempt -- wall-clock budget exhausted",
                    a_cfg.get("provider"),
                )
                return {
                    "status": "error",
                    "error": (
                        "LLM call skipped -- no wall-clock budget left in "
                        "this turn."
                    ),
                }
            attempt_timeout = _attempt_timeout()
            if a_cfg.get("provider") in skip_providers:
                if is_last:
                    return last_error
                continue
            payload["model"] = a_model
            # Per-attempt like tool_choice: a fallback to a different provider
            # must get that provider's accepted temperature (kimi -> 1, others ->
            # the agent's own), not the primary's.
            payload["temperature"] = _provider_temperature(a_cfg, self.temperature)
            payload["max_tokens"] = _provider_max_tokens(a_cfg, self.max_tokens)
            if a_cfg.get("provider") == "groq":
                payload["messages"] = _compact_messages_for_tpm(messages)
                # Leave TPM headroom under the live on-demand 8000 cap.
                payload["max_tokens"] = min(int(payload["max_tokens"] or 1024), 1024)
            else:
                payload["messages"] = messages
            if tools and with_tools:
                payload["tool_choice"] = _tool_choice_for(a_cfg["provider"])
            try:
                if _is_native_ollama(a_cfg):
                    # Native Ollama path: different payload shape, no tool_choice,
                    # and tool messages must be recast as user messages.
                    native_messages = _sanitize_messages_for_ollama_native(messages)
                    native_payload = _native_ollama_payload(
                        model=a_model,
                        messages=native_messages,
                        temperature=payload["temperature"],
                        max_tokens=self.max_tokens,
                        tools=tools if (tools and with_tools) else None,
                        stream=False,
                    )
                    async with httpx.AsyncClient(timeout=attempt_timeout) as client:
                        r = await client.post(
                            a_cfg["url"],
                            json=native_payload,
                            headers=(
                                {"Authorization": f"Bearer {a_key}", "Content-Type": "application/json"}
                                if a_key
                                else {"Content-Type": "application/json"}
                            ),
                        )
                    if r.status_code >= 400:
                        body = r.text
                        last_error = {"status": "error", "error": f"{a_cfg['provider']} HTTP {r.status_code}: {body[:300]}"}
                        if _is_retryable(r.status_code) and not is_last:
                            _LOG.warning("llm: %s HTTP %s — falling back to %s", a_cfg["provider"], r.status_code, attempts[attempt_idx + 1][0]["provider"])
                            continue
                        _LOG.warning("llm: %s HTTP %s — no fallback taken: %s", a_cfg["provider"], r.status_code, body[:200])
                        return last_error
                    data = r.json()
                    msg = _ollama_message_to_openai(data.get("message") or {})
                    try:
                        from app.core import usage_tracker
                        usage_tracker.record(
                            user_id=user_id,
                            agent_name=self.name,
                            provider=a_cfg.get("provider", ""),
                            model=data.get("model") or a_model,
                            usage=None,
                        )
                    except Exception:  # noqa: BLE001
                        _LOG.warning(
                            "swallowed %s in _call_llm() — continuing",
                            "Exception", exc_info=True,
                        )
                    return {"status": "success", "choice": {"message": msg}, "raw": data}

                headers = (
                    {"Authorization": f"Bearer {a_key}", "Content-Type": "application/json"}
                    if a_key
                    else {"Content-Type": "application/json"}
                )
                groq_413_retried = False
                while True:
                    async with httpx.AsyncClient(timeout=attempt_timeout) as client:
                        r = await client.post(
                            a_cfg["url"],
                            json=payload,
                            headers=headers,
                        )
                    if (
                        r.status_code == 413
                        and a_cfg.get("provider") == "groq"
                        and not groq_413_retried
                    ):
                        compact_model = (
                            os.getenv("GROQ_COMPACT_MODEL") or "openai/gpt-oss-20b"
                        ).strip()
                        if compact_model and compact_model != payload.get("model"):
                            groq_413_retried = True
                            payload["model"] = compact_model
                            payload["messages"] = _compact_messages_for_tpm(
                                messages, budget=12000,
                            )
                            payload["max_tokens"] = min(
                                int(payload.get("max_tokens") or 1024), 768,
                            )
                            _LOG.warning(
                                "llm: groq HTTP 413 — retrying compacted payload on %s",
                                compact_model,
                            )
                            continue
                    break
            except httpx.TimeoutException:
                last_error = {"status": "error", "error": f"{a_cfg['provider']} LLM call timed out ({int(attempt_timeout)}s)."}
                if not is_last:
                    _LOG.warning("llm: %s timed out — falling back to %s", a_cfg["provider"], attempts[attempt_idx + 1][0]["provider"])
                    continue
                return last_error
            except Exception as e:  # noqa: BLE001 — network/transport
                last_error = {"status": "error", "error": f"{a_cfg['provider']} LLM call failed: {e}"}
                if not is_last:
                    _LOG.warning("llm: %s network error (%s) — falling back to %s", a_cfg["provider"], e, attempts[attempt_idx + 1][0]["provider"])
                    continue
                return last_error

            if r.status_code >= 400:
                body = r.text
                # Groq's tool-use validator rejects Llama-native function
                # markup (`<function=name{json}>`) with HTTP 400 and
                # `tool_use_failed`. The raw markup lives in
                # `error.failed_generation`. Recover it into OpenAI-style
                # tool_calls so the agent loop can dispatch and continue
                # rather than bubbling a 400 to the user.
                tool_use_failed_unrecovered = False
                try:
                    err = json.loads(body)
                    err_obj = err.get("error", {}) if isinstance(err, dict) else {}
                    if err_obj.get("code") == "tool_use_failed":
                        failed_gen = err_obj.get("failed_generation", "") or ""
                        recovered = _parse_llama_native_tool_calls(failed_gen)
                        if recovered:
                            return {
                                "status": "success",
                                "choice": {
                                    "message": {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": recovered,
                                    },
                                },
                                "raw": err,
                            }
                        # Llama emitted the answer as PROSE instead of a tool
                        # call (common on Groq when tool_choice is forced with a
                        # large production context). We can't recover a tool
                        # call from prose — but this is a model-side failure a
                        # different provider CAN handle, so treat it as
                        # retryable and fall back rather than erroring the turn.
                        tool_use_failed_unrecovered = True
                except (json.JSONDecodeError, KeyError, TypeError):
                    _LOG.warning(
                        "swallowed %s in _call_llm() — continuing",
                        "(json.JSONDecodeError, KeyError, TypeError)", exc_info=True,
                    )
                last_error = {"status": "error", "error": f"{a_cfg['provider']} HTTP {r.status_code}: {body[:300]}"}
                shape_400 = r.status_code == 400 and _http_400_is_retryable(body)
                if shape_400:
                    skip_providers.add(str(a_cfg.get("provider") or ""))
                if (_is_retryable(r.status_code) or tool_use_failed_unrecovered or shape_400) and not is_last:
                    reason = (
                        "tool_use_failed (prose)" if tool_use_failed_unrecovered
                        else ("conversation-shape 400" if shape_400 else f"HTTP {r.status_code}")
                    )
                    nxt_provider = next(
                        (c[0].get("provider") for c in attempts[attempt_idx + 1:]
                         if c[0].get("provider") not in skip_providers),
                        attempts[attempt_idx + 1][0]["provider"],
                    )
                    _LOG.warning("llm: %s %s — falling back to %s", a_cfg["provider"], reason, nxt_provider)
                    continue
                # No fallback taken (non-retryable status, or this was the last
                # provider): this error ENDS the turn. A silently-returned 4xx
                # (e.g. Moonshot's temperature 400) was previously invisible in
                # prod logs, so the acceptance criterion "no HTTP 400 events"
                # could not be verified from logs that never recorded it. Observe
                # only — no retry logic added here.
                _LOG.warning("llm: %s HTTP %s — no fallback taken, turn errors: %s",
                             a_cfg["provider"], r.status_code, body[:200])
                return last_error

            # ── success on this provider ──────────────────────────────────
            active_cfg = a_cfg
            try:
                data = r.json()
                choice = (data.get("choices") or [{}])[0]
                # Best-effort cost tracking — never let it sink an LLM call.
                try:
                    from app.core import usage_tracker
                    usage_tracker.record(
                        user_id=user_id,
                        agent_name=self.name,
                        provider=active_cfg.get("provider", ""),
                        model=data.get("model") or active_cfg.get("default_model") or "",
                        usage=data.get("usage"),
                    )
                except Exception:  # noqa: BLE001
                    _LOG.warning(
                        "swallowed %s in _call_llm() — continuing",
                        "Exception", exc_info=True,
                    )
                msg = choice.get("message") or {}
                if msg and not (msg.get("tool_calls") or []):
                    # Observe-only tripwire: the model answered a turn whose
                    # intent REQUIRED a tool (requires_tool) without calling one.
                    # On groq/kimi we can't force tool_choice (it 400s), so the
                    # model may skip on "auto" and emit a degenerate prose answer
                    # — the exact failure the hardened smoke catches externally.
                    # Emit one grep-able marker so a resurgence is queryable from
                    # prod logs (list_logs text="TOOL_SKIP"). This does NOT act:
                    # no forced retry is triggered here. (DSML-in-prose
                    # tool call recovered downstream would false-positive here;
                    # this only logs.)
                    if requires_tool:
                        _LOG.warning(
                            "TOOL_SKIP agent=%s provider=%s model=%s answer_chars=%d "
                            "— deliverable intent, no tool call",
                            self.name, a_cfg.get("provider"),
                            data.get("model") or a_model, len(msg.get("content") or ""),
                        )
                if _llm_choice_is_empty_or_filtered(choice) and not is_last:
                    skip_providers.add(str(a_cfg.get("provider") or ""))
                    nxt_provider = next(
                        (c[0].get("provider") for c in attempts[attempt_idx + 1:]
                         if c[0].get("provider") not in skip_providers),
                        attempts[attempt_idx + 1][0]["provider"],
                    )
                    last_error = {
                        "status": "error",
                        "error": (
                            f"{a_cfg['provider']} empty or content-filtered "
                            "completion"
                        ),
                    }
                    _LOG.warning(
                        "llm: %s empty/content-filter — falling back to %s",
                        a_cfg["provider"], nxt_provider,
                    )
                    continue
                return {"status": "success", "choice": choice, "raw": data}
            except Exception as e:  # noqa: BLE001 — response parse / rewrite
                last_error = {"status": "error", "error": f"{a_cfg['provider']} response error: {e}"}
                if not is_last:
                    _LOG.warning("llm: %s response error (%s) — falling back to %s", a_cfg["provider"], e, attempts[attempt_idx + 1][0]["provider"])
                    continue
                return last_error

        return last_error

    async def _stream_synthesis(
        self,
        messages: list[dict[str, Any]],
        api_key: str,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a tool-free synthesis completion, yielding content deltas.

        Used ONLY for the forced (``with_tools=False``) synthesis call and only
        on Groq (the verified provider). Mirrors ``_call_llm``'s setup — same
        ``_llm_config``, same leftover-model-name remap, same soft daily-cap
        semantics, and critically the SAME ``_sanitize_messages_for_provider``
        chokepoint — but sets ``stream=True`` and offers NO tools, so no
        tool_call deltas can appear (honours "tool iterations stay
        non-streaming").

        Raises ``_SynthStreamError`` on any PRE-first-token failure (wrong
        provider, over cap, non-200, connect/transport error) so the caller can
        fall back to the non-streaming ``_call_llm`` path. A mid-stream drop is
        also surfaced as ``_SynthStreamError`` — the caller distinguishes the two
        via whether it has already emitted tokens, and never restarts a stream
        it has begun.
        """
        cfg = _llm_config()
        native_ollama = _is_native_ollama(cfg)
        if cfg["provider"] not in ("groq", "openai", "kimi") and not native_ollama:
            # Only Groq, OpenAI, Kimi, and native Ollama streaming are verified.
            raise _SynthStreamError(
                "streaming synthesis only verified for groq/openai/kimi/native-ollama"
            )
        # Soft daily cap: mirror _call_llm. Over cap -> fall back so the
        # non-streaming path emits the structured cap error the UI expects.
        if user_id:
            try:
                cap = float(os.getenv("USAGE_DAILY_CAP_USD") or "0")
            except ValueError:
                cap = 0.0
            if cap > 0:
                try:
                    from app.core import usage_tracker
                    over = usage_tracker.is_over_cap(user_id, cap)
                except Exception:  # noqa: BLE001 — broken tracker must not block
                    over = False
                if over:
                    raise _SynthStreamError("daily cap reached")
        model = self.model
        if not model or model.startswith(("deepseek-", "gpt-4", "gpt-3")):
            model = cfg["default_model"]
        messages = _sanitize_messages_for_provider(messages)
        temperature = _provider_temperature(cfg, self.temperature)
        stream_max_tokens = _provider_max_tokens(cfg, self.max_tokens)
        headers = (
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            if api_key
            else {"Content-Type": "application/json"}
        )
        usage: dict[str, Any] | None = None
        # Reasoning-phase deltas seen but deliberately not yielded (see below).
        reasoning_deltas = 0
        content_deltas = 0
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(_llm_http_timeout(), read=_llm_http_timeout())) as client:
                if native_ollama:
                    payload = _native_ollama_payload(
                        model=model,
                        messages=_sanitize_messages_for_ollama_native(messages),
                        temperature=temperature,
                        max_tokens=stream_max_tokens,
                        stream=True,
                    )
                    async with client.stream(
                        "POST", cfg["url"], json=payload, headers=headers,
                    ) as r:
                        if r.status_code != 200:
                            await r.aread()
                            raise _SynthStreamError(
                                f"{cfg['provider']} HTTP {r.status_code}: {r.text[:200]}"
                            )
                        async for line in r.aiter_lines():
                            if not line:
                                continue
                            try:
                                evt = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            msg = evt.get("message") or {}
                            delta = msg.get("content")
                            if delta:
                                yield delta
                            if evt.get("done"):
                                break
                else:
                    payload = {
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": stream_max_tokens,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    }
                    async with client.stream(
                        "POST", cfg["url"], json=payload, headers=headers,
                    ) as r:
                        if r.status_code != 200:
                            await r.aread()
                            raise _SynthStreamError(
                                f"{cfg['provider']} HTTP {r.status_code}: {r.text[:200]}"
                            )
                        async for line in r.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                evt = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = evt.get("choices") or []
                            if choices:
                                _delta_obj = choices[0].get("delta") or {}
                                # Reasoning models (Kimi K2 thinking) stream a
                                # THINKING phase as ``reasoning_content`` before
                                # any ``content`` arrives. It is not the answer:
                                # it contains discarded intermediate claims that
                                # would read as authoritative in the UI, and it
                                # is not what gets persisted. Count it so the
                                # caller can tell "model is working" apart from
                                # "provider returned nothing", but never yield
                                # it as answer text.
                                if _delta_obj.get("reasoning_content"):
                                    reasoning_deltas += 1
                                delta = _delta_obj.get("content")
                                if delta:
                                    content_deltas += 1
                                    yield delta
                            if evt.get("usage"):
                                usage = evt["usage"]
        except _SynthStreamError:
            raise
        except Exception as e:  # noqa: BLE001 — network/transport/parse
            raise _SynthStreamError(f"{cfg['provider']} stream error: {e}")
        if reasoning_deltas and not content_deltas:
            # HTTP 200 with a full thinking phase and no answer. The caller sees
            # zero tokens and falls back to non-streaming, which is correct but
            # looks identical to a dead provider in the logs. Say which it was.
            _LOG.warning(
                "%s streamed %d reasoning deltas and no content; "
                "falling back to non-streaming synthesis",
                cfg["provider"], reasoning_deltas,
            )
        # Best-effort cost tracking — never let it sink the turn.
        if usage is not None:
            try:
                from app.core import usage_tracker
                usage_tracker.record(
                    user_id=user_id,
                    agent_name=self.name,
                    provider=cfg.get("provider", ""),
                    model=model,
                    usage=usage,
                )
            except Exception:  # noqa: BLE001
                _LOG.warning(
                    "swallowed %s in _stream_synthesis() — continuing",
                    "Exception", exc_info=True,
                )

    async def _run_tool_call(
        self,
        tool_call: dict[str, Any],
        api_key: str | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        _depth: int = 0,
        _call_stack: list[str] | None = None,
        user_message: str | None = None,
        history: list | None = None,
    ) -> dict[str, Any]:
        fn = tool_call.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        try:
            # Presentation params (char_offset) are removed here, not later:
            # they choose which window of the SERIALIZED result comes back,
            # and the block reads the whole file either way. The window
            # itself is read off the tool call by _requested_char_offset.
            args = _dispatch_args(raw_args)
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

        # Retrieval-shaped manifest actions are the search tool by another
        # name; rewrite and fall through to the search branch below.
        if name in ("search_documents", "fidic_clause_lookup", "standards_lookup"):
            name = "search_project_documents"

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

        # ── synthetic tool: list_project_documents ───────────────────────────
        if name == "list_project_documents":
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
                limit = int(args.get("limit") or 100)
            except (TypeError, ValueError):
                limit = 100
            limit = max(1, min(limit, 200))
            from app.core import projects as _projects_store
            # The pilot's master-corpus project is an alias with no documents
            # of its own — list the backing corpus, like the chat path does.
            resolved_pid = (_projects_store._master_corpus_source(project_id)
                            or project_id)
            docs = _projects_store.list_documents(
                resolved_pid, limit=limit, offset=0, newest_first=True,
            )
            total = _projects_store.count_documents(resolved_pid)
            return {
                "name": "list_project_documents",
                "ok": True,
                "result": {
                    "total_documents": total,
                    "returned": len(docs),
                    "documents": [
                        {
                            "filename": d.get("original_name"),
                            "doc_type": d.get("doc_type"),
                            "size_bytes": d.get("size"),
                        }
                        for d in docs
                    ],
                },
            }

        # ── synthetic tool: fetch_document ───────────────────────────────────
        if name == "fetch_document":
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
            doc_id = (args.get("document_id") or "").strip()
            filename = (args.get("filename") or "").strip()
            if not doc_id and not filename:
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": "provide document_id or filename",
                        "hint": "Pass the id from the attachment note or a search result, or the file's name.",
                    },
                }
            content, doc, err = _fetch_document_content(project_id, doc_id, filename)
            if err:
                return {"name": name, "ok": False, "result": {"status": "error", "error": err}}
            return {
                "name": "fetch_document",
                "ok": True,
                "result": {
                    "document_id": doc.get("id"),
                    "filename": doc.get("original_name"),
                    "doc_type": doc.get("doc_type"),
                    "content": content["text"],
                    "truncated": content["truncated"],
                    "source": content["source"],
                },
            }

        # ── synthetic tool: generate_wbs (direct construction shortcut) ──────
        # Bypasses the generic "construction" tool's input/params ambiguity by
        # giving the model a typed call: brief, target_count, project_type,
        # start_date. Maps straight to ConstructionContainer.generate_wbs().
        if name == "generate_wbs":
            if message_is_contract_data_lookup(user_message or ""):
                return {
                    "name": name,
                    "ok": False,
                    "result": {
                        "status": "error",
                        "error": (
                            "This is a Contract Data lookup (Time for "
                            "Completion / milestones / similar). Do not "
                            "generate a WBS. Answer from retrieved contract "
                            "context."
                        ),
                    },
                }
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
            # Coerce target_count if the provider shipped it as a string ("30" → 30).
            tc_raw = args.get("target_count", 200)
            try:
                tc = int(tc_raw) if tc_raw not in (None, "") else 200
            except (TypeError, ValueError):
                tc = 200
            from app.lib.wbs_duration_overrides import collect_overrides
            params = {
                "brief": args.get("brief") or "",
                "target_count": tc,
                "project_type": args.get("project_type"),
                "start_date": args.get("start_date"),
                "user_message": user_message,
                "history": history,
                "duration_overrides": collect_overrides(
                    user_message,
                    args.get("brief"),
                    explicit=args.get("duration_overrides"),
                    history=history,
                ),
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

        # ── synthetic tool: cash_flow_forecast (direct construction shortcut)
        if name == "cash_flow_forecast":
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
            cv_raw = args.get("contract_value")
            try:
                cv = float(cv_raw) if cv_raw not in (None, "") else None
            except (TypeError, ValueError):
                cv = None
            dm_raw = args.get("duration_months")
            try:
                dm = int(dm_raw) if dm_raw not in (None, "") else None
            except (TypeError, ValueError):
                dm = None
            params: dict[str, Any] = {}
            if cv is not None:
                params["contract_value"] = cv
            if dm is not None:
                params["duration_months"] = dm
            try:
                result = await container.cash_flow_forecast(
                    {"message": args.get("message") or user_message or ""},
                    params,
                )
            except Exception as e:
                return {
                    "name": name,
                    "ok": False,
                    "result": {"status": "error", "error": f"cash_flow_forecast failed: {e}"},
                }
            return {
                "name": "cash_flow_forecast",
                "ok": isinstance(result, dict) and result.get("status") == "success",
                "result": result,
            }

        # ── synthetic tool: commissioning_checklist ──────────────────────────
        if name == "commissioning_checklist":
            if "construction" not in self.allowed_blocks:
                return {
                    "name": name, "ok": False,
                    "result": {"status": "error", "error": "construction container not in agent's allowed_blocks"},
                }
            try:
                from app.dependencies import get_block_instance
                container = get_block_instance("construction")
            except Exception as e:
                return {
                    "name": name, "ok": False,
                    "result": {"status": "error", "error": f"construction unavailable: {e}"},
                }
            systems = args.get("systems")
            inferred = _infer_commissioning_systems(
                user_message or args.get("message") or ""
            )
            if inferred:
                systems = inferred
            elif not isinstance(systems, list) or not systems:
                systems = ["hvac", "electrical", "fire"]
            try:
                result = await container.commissioning_checklist({}, {"systems": systems})
            except Exception as e:
                return {
                    "name": name, "ok": False,
                    "result": {"status": "error", "error": f"commissioning_checklist failed: {e}"},
                }
            # Trim the flattened master list — the model only needs the organised
            # per-system checklists + summary to present; the full flat list is
            # redundant and bloats context.
            if isinstance(result, dict):
                result = {k: v for k, v in result.items() if k != "master_test_schedule"}
            return {
                "name": "commissioning_checklist",
                "ok": isinstance(result, dict) and result.get("status") == "success",
                "result": result,
            }

        # ── synthetic tool: resource_histogram ───────────────────────────────
        if name == "resource_histogram":
            if "construction" not in self.allowed_blocks:
                return {
                    "name": name, "ok": False,
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
                    "name": name, "ok": False,
                    "result": {"status": "error", "error": f"construction unavailable: {e}"},
                }
            raw = args.get("schedule_file") or args.get("file_path") or ""
            resolved = _resolve_file_path(project_id, raw) if raw else ""
            if not resolved or not os.path.exists(str(resolved)):
                resolved, _picked = _resolve_histogram_schedule_file(
                    project_id, user_message or "",
                )
            if not resolved:
                return {
                    "name": name, "ok": False,
                    "result": {
                        "status": "error",
                        "error": (
                            "No schedule file resolved — name the .xer "
                            "(e.g. resource_loaded.xer) or upload one."
                        ),
                    },
                }
            period_unit = args.get("period_unit") or "week"
            try:
                result = await container.resource_histogram(
                    {},
                    {"schedule_file": resolved, "period_unit": period_unit},
                )
            except Exception as e:
                return {
                    "name": name, "ok": False,
                    "result": {
                        "status": "error",
                        "error": f"resource_histogram failed: {e}",
                    },
                }
            return {
                "name": "resource_histogram",
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

        # ── synthetic tool: construction_calc (deterministic formula library) ─
        if name == "construction_calc":
            from app.lib import construction_formulas as _cf
            result = _cf.run_calculation(args.get("calculation"), args.get("params"))
            return {
                "name": "construction_calc",
                "ok": isinstance(result, dict) and result.get("status") != "error",
                "result": result,
            }

        # ── declared hat-manifest actions -> real implementations ───────────
        # Calculator aliases: the declared name runs the registry calculator
        # it always meant. The result carries `aliased_to` so the trace shows
        # the real machinery that produced the numbers.
        if name in ("concrete_maturity_calculator", "grout_pressure_calculator",
                    "thermal_crack_assessor", "mix_design_validator",
                    "crane_planner", "critical_path_calc", "float_analysis",
                    "tender_evaluator", "supplier_scoring",
                    "compliance_gate_checker", "ncr_tracker"):
            from app.lib import construction_formulas as _cf
            calc = _HAT_CALC_ALIASES[name]
            result = _cf.run_calculation(calc, args.get("params") or args)
            payload = result if isinstance(result, dict) else {"result": result}
            return {
                "name": name,
                "ok": isinstance(result, dict) and result.get("status") != "error",
                "result": {"aliased_to": f"construction_calc:{calc}", **payload},
            }

        # Container aliases: the declared name runs the construction
        # container route that implements it (same gate as generate_wbs:
        # the agent must hold the construction block).
        if name in ("interim_certificate_generator", "schedule_analysis",
                    "baseline_compare", "eot_claim_assessor",
                    "dispute_timeline_builder", "variation_order_generator",
                    "boq_cost_analyzer", "drawing_qto_extract",
                    "milestone_tracker", "rate_card_lookup", "itp_generator",
                    "po_generator"):
            if "construction" not in self.allowed_blocks:
                return {
                    "name": name, "ok": False,
                    "result": {"status": "error",
                               "error": "construction container not in agent's allowed_blocks"},
                }
            route = _HAT_CONTAINER_ALIASES[name]
            try:
                from app.dependencies import get_block_instance
                block = get_block_instance("construction")
                params = dict(args.get("params") or {})
                params["action"] = route
                _alias_input = args.get("input") or args
                # F43: container file actions need the same original-name ->
                # stored-path resolution as file-schema blocks (dicts only).
                if project_id and isinstance(_alias_input, dict):
                    _alias_input = _resolve_block_file_input(project_id, _alias_input)
                if project_id and isinstance(params, dict):
                    params = _resolve_block_file_input(project_id, params)
                    params["action"] = route
                result = await block.process(_alias_input, params)
            except Exception as e:  # noqa: BLE001 — a tool error is a payload, not a crash
                result = {"status": "error", "error": f"{route} failed: {e}"}
            payload = result if isinstance(result, dict) else {"result": result}
            return {
                "name": name,
                "ok": isinstance(result, dict) and result.get("status") != "error",
                "result": {"aliased_to": f"construction:{route}", **payload},
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
        # File-consuming blocks get an explicit `file_path` schema (see
        # _FILE_TOOL_SCHEMAS above in tool_definitions). When the LLM
        # responds to that schema it emits `{"file_path": "<name>"}` at
        # the top level — NOT nested under input/params. Detect that
        # shape and synthesize the block's expected envelope.
        if name in _FILE_CONSUMING_BLOCKS and "file_path" in args and "input" not in args:
            block_input = {"file_path": args.get("file_path")}
            block_params = {k: v for k, v in args.items() if k != "file_path"}
        else:
            block_input = args.get("input")
            block_params = args.get("params") or {}
        if name == "validation_pipeline":
            # Leftover-hat L4: the model called validation_pipeline with
            # value=null on a prose claim (40 m span / 50 mm beam). The
            # pipeline short-circuited at syntactic and skipped Physical.
            # Fold top-level LLM keys and the user message in as `claim`.
            merged: dict[str, Any] = {}
            if isinstance(block_input, dict):
                merged.update(block_input)
            if isinstance(block_params, dict):
                for k, v in block_params.items():
                    merged.setdefault(k, v)
            for k, v in args.items():
                if k not in ("input", "params"):
                    merged.setdefault(k, v)
            if merged.get("value") is None and not merged.get("claim") and user_message:
                merged["claim"] = user_message
            block_input = merged
        # File-consuming blocks: the LLM typically supplies just the filename
        # (e.g. 'Infra-1 - Demolition BOQ.pdf') because that is what the
        # user said. The block then calls os.path.exists on a bare filename
        # which always fails on the deployed disk, producing
        # 'File not found: <name>'. Resolve to the absolute file_path of the
        # uploaded document for this project before dispatch.
        if name in _FILE_CONSUMING_BLOCKS and project_id:
            block_input = _resolve_block_file_input(project_id, block_input)
            block_params = _resolve_block_file_input(project_id, block_params)
        elif name == "construction" and project_id:
            # F43: the construction container's file actions (bim_extract,
            # boq_process, drawing takeoffs) received the BARE filename and
            # died on 'File not found: qa_building.ifc' while file-schema
            # blocks got the stored path. Dict payloads only -- a bare-string
            # input here is a natural-language request, and the resolver's
            # substring matching must never rewrite prose into a file path.
            if isinstance(block_input, dict):
                block_input = _resolve_block_file_input(project_id, block_input)
            if isinstance(block_params, dict):
                block_params = _resolve_block_file_input(project_id, block_params)
        if name == "construction" and user_message:
            # Live M14: the model rewrote wir_form scope to "blinding pour"
            # so refuse missed the operator RFP / job-req / claim.
            action = ""
            if isinstance(block_params, dict):
                action = str(block_params.get("action") or "")
            if action in {
                "wir_form", "inspection_request", "job_requisition",
                "rfp_draft", "rfp_management", "claims_builder",
            }:
                if isinstance(block_params, dict):
                    block_params = dict(block_params)
                    block_params.setdefault("user_message", user_message)
                else:
                    block_params = {"user_message": user_message, "action": action}
                if isinstance(block_input, dict):
                    block_input = dict(block_input)
                    block_input.setdefault("user_message", user_message)
                    block_input.setdefault("text", user_message)
                elif not block_input:
                    block_input = {"user_message": user_message, "text": user_message}
        try:
            result = await instance.execute(block_input, block_params)
            envelope = {"name": name, "ok": True, "result": result}
            await _auto_validate(envelope)
            return envelope
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


# ── Auto-validation middleware ───────────────────────────────────────────
# Every successful block call gets its numeric payload run through the
# validation_pipeline block automatically, and the verdict is grafted onto
# the tool result so the LLM sees it next turn. The heavy-reasoning prompt
# is updated to refuse to report numbers whose `validation.overall == fail`.
# Without this, the validation_pipeline block existed but only ran when the
# LLM remembered to call it — which is exactly the failure mode that let
# the 5,900 °C unit-conversion slip through earlier.

# Synthetic tools that aren't in BLOCK_REGISTRY and so can't carry a
# class-level `auto_validate` flag. Hardcoded here because they're part
# of the runtime contract, not a block.
_SYNTHETIC_NEVER_VALIDATE = {
    "validation_pipeline",        # don't recurse
    "remember_fact",              # synthetic, no numeric
    "delegate_to_agent",          # nested agent result
    "search_project_documents",   # text results
    "generate_wbs",               # has its own CPM validation
}


def _block_should_auto_validate(name: str) -> bool:
    """Per-block opt-out for auto-validation. Blocks declare
    ``auto_validate = False`` as a class attribute; the runtime reads
    it instead of an enumerated skip list. Synthetic tools without a
    ``BLOCK_REGISTRY`` entry use ``_SYNTHETIC_NEVER_VALIDATE``.
    """
    if name in _SYNTHETIC_NEVER_VALIDATE:
        return False
    cls = BLOCK_REGISTRY.get(name)
    if cls is None:
        return False
    return bool(getattr(cls, "auto_validate", True))


def _collect_numerics(result: Any) -> list[dict[str, Any]]:
    """Walk a block result dict and pick out numeric payloads worth checking.

    Yields a list of ``{value, unit?, context}`` packets in the shape
    validation_pipeline.process() expects. The detection is lenient — better
    to spot-check a few extras than miss a value silently.
    """
    if not isinstance(result, dict):
        return []
    out: list[dict[str, Any]] = []

    # sympy_reasoning / recommendation_template / formula_executor_v2 shapes
    for key, ctx_extra in (
        ("variances",     {"metric": "percent",   "label": "variance_pct"}),
        ("cost_impacts",  {"metric": "cost_usd",  "label": "cost_impact"}),
    ):
        items = result.get(key)
        if isinstance(items, list):
            for it in items[:8]:  # cap so we don't blow context
                if isinstance(it, dict):
                    v = it.get("value", it.get("variance_pct", it.get("cost_impact")))
                    if isinstance(v, (int, float)):
                        ctx = {**ctx_extra}
                        for c_key in ("material_type", "material", "item"):
                            if c_key in it:
                                ctx["material_type"] = str(it[c_key]).lower()
                                break
                        out.append({"value": v, "unit": it.get("unit"), "context": ctx})

    # boq_processor / construction container shapes
    if "total_cost" in result and isinstance(result["total_cost"], (int, float)):
        out.append({
            "value": result["total_cost"],
            "unit": result.get("currency", "USD"),
            "context": {"metric": "cost_usd", "currency": result.get("currency", "USD")},
        })

    # formula_executor result envelope
    fr = result.get("result")
    if isinstance(fr, (int, float)) and not isinstance(fr, bool):
        ctx = {"metric": result.get("metric") or result.get("task_metric") or ""}
        unit = result.get("unit") or result.get("output_unit")
        out.append({"value": fr, "unit": unit, "context": ctx})

    return out


async def _auto_validate(envelope: dict[str, Any]) -> None:
    """In-place: attach a `validation` field to the tool envelope.

    Runs the validation_pipeline block over every numeric in the result.
    Aggregates per-numeric verdicts into a single summary the LLM can read.
    """
    name = envelope.get("name", "")
    if not _block_should_auto_validate(name):
        return
    result = envelope.get("result")
    if not isinstance(result, dict) or result.get("status") != "success":
        return
    # UniversalBlock.execute returns {block, status, result: {actual...}}.
    # The numerics live in the inner result; unwrap one level when present.
    inner = result.get("result")
    if isinstance(inner, dict) and inner.get("status") == "success":
        result = inner
    numerics = _collect_numerics(result)
    if not numerics:
        envelope["validation"] = {"skipped": "no numeric value found"}
        return
    try:
        from app.blocks import BLOCK_REGISTRY
        from app.dependencies import _create_block_instance as _create
        from app.dependencies import block_instances as _bi
        if "validation_pipeline" not in BLOCK_REGISTRY:
            envelope["validation"] = {"skipped": "validation_pipeline not registered"}
            return
        vp_block = _bi.get("validation_pipeline")
        if vp_block is None:
            vp_block = _create(BLOCK_REGISTRY["validation_pipeline"])
            _bi["validation_pipeline"] = vp_block
    except Exception as e:
        envelope["validation"] = {"skipped": f"validation_pipeline init failed: {type(e).__name__}"}
        return

    per: list[dict[str, Any]] = []
    overall = "pass"
    first_failure: str | None = None
    for n in numerics[:8]:  # hard cap
        try:
            envelope_inner = await vp_block.execute(n, {})
        except Exception as e:  # noqa: BLE001
            per.append({"input": n, "overall": "error", "error": str(e)[:120]})
            continue
        # UniversalBlock.execute wraps the block's process() return in a
        # {block, status, result: {...}} envelope; the real verdict lives
        # under `result`.
        vr = envelope_inner.get("result") if isinstance(envelope_inner, dict) else None
        if not isinstance(vr, dict):
            continue
        v_overall = vr.get("overall")
        per.append({
            "value": n.get("value"),
            "unit": n.get("unit"),
            "overall": v_overall,
            "first_failure": vr.get("first_failure"),
        })
        if v_overall == "fail" and overall == "pass":
            overall = "fail"
            first_failure = vr.get("first_failure")
    envelope["validation"] = {
        "overall": overall,
        "first_failure": first_failure,
        "checks": per,
        "note": (
            "auto-run by runtime middleware; refuse to report any number "
            "whose check shows overall=fail without explaining which "
            "stage rejected it."
        ),
    }


# ── Loader ────────────────────────────────────────────────────────────────
AGENT_REGISTRY: dict[str, Agent] = {}


def load_agents(configs_dir: Path | None = None) -> dict[str, Agent]:
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
            print(f"failed to load agent {md.name}: {e}")
    return AGENT_REGISTRY


def get_agent(name: str) -> Agent | None:
    return AGENT_REGISTRY.get(name)


# ── Smart-orchestrator routing gate ─────────────────────────────────────────
# Pre-PR-#78, the production chat path bypassed smart_orchestrator entirely:
# the React UI calls /v1/agents/project-assistant/chat/stream which lands
# directly on Agent.chat_stream() with no keyword-routing consultation. The
# only place smart_orchestrator was consulted was /v1/chat/stream — a route
# the React frontend never hits.
#
# The fix is `select_agent_for_message`: a thin helper the agents-router
# calls BEFORE dispatching to Agent.chat()/chat_stream(). When the user's
# message classifies as a needs_planning intent (e.g. "create a 200-activity
# schedule" → generate_wbs at confidence >= 0.4), and the caller asked for
# anything OTHER than heavy-reasoning, the helper redirects to the
# heavy-reasoning agent so the tool-call loop actually runs the requested
# pipeline instead of producing prose.
#
# Design constraints from the operator:
#   * Real user traffic must pass through smart_orchestrator (this gate).
#   * Internal delegation (runtime.py:_run_tool_call → target.chat) must
#     NOT re-route — the parent agent already made the decision and a
#     sub-agent invocation shouldn't be hijacked. The agents-router never
#     hits the delegation path, so the gate-at-router-only design is
#     sufficient; we don't need an extra _depth check here.
#   * Test paths must not break. Tests that POST to /v1/agents/.../chat
#     will see the gate. Tests that call Agent.chat() directly (most
#     unit tests) skip it entirely. The gate is also kill-switchable via
#     SMART_ORCH_ROUTING_DISABLED=true for fast prod rollback.

_SMART_ORCH_BLOCK_CACHE: Any | None = None


def _get_smart_orchestrator_block() -> Any | None:
    """Lazy-load and cache a SmartOrchestratorBlock instance.

    Returns None if the block isn't registered (e.g. running without the
    construction kit) so the caller can fall back to the no-op routing
    decision (pass-through to the requested agent)."""
    global _SMART_ORCH_BLOCK_CACHE
    if _SMART_ORCH_BLOCK_CACHE is not None:
        return _SMART_ORCH_BLOCK_CACHE
    try:
        from app.blocks import BLOCK_REGISTRY
        cls = BLOCK_REGISTRY.get("smart_orchestrator")
        if cls is None:
            return None
        _SMART_ORCH_BLOCK_CACHE = cls()
        return _SMART_ORCH_BLOCK_CACHE
    except Exception:  # noqa: BLE001
        return None


def _routing_disabled() -> bool:
    """Kill-switch read at every call so Render env-var flips take effect
    without a restart."""
    return os.getenv("SMART_ORCH_ROUTING_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _asks_self_coding(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in _SELF_CODING_PHRASES)


def _message_names_registered_calculator(text: str) -> bool:
    """True when the message literally names a registry calculator.

    Named formulas are an existing feature — construction_calc stays in
    charge. Unnamed custom arithmetic is self-coding's job.
    """
    try:
        from app.lib.construction_formulas import CALCULATORS
    except Exception:  # noqa: BLE001
        return False
    raw = text or ""
    t = raw.lower().replace("-", "_")
    spaced = raw.lower()
    for name in CALCULATORS:
        if len(name) < 6:
            continue
        if name.lower() in t or name.replace("_", " ") in spaced:
            return True
    return False


def _should_handoff_unmatched_calc(text: str) -> bool:
    """Computation-shaped, no registered formula name → self-coding once."""
    if _message_names_registered_calculator(text):
        return False
    return _looks_like_self_contained_calculation(text) or _asks_self_coding(text)


def _nudge_for_failed_tool(tool_result: dict[str, Any], agent: "Agent") -> str:
    """Prefer a one-shot self-coding handoff over retrying unknown formulas."""
    inner = tool_result.get("result") if isinstance(tool_result, dict) else None
    err = ""
    if isinstance(inner, dict):
        err = str(inner.get("error") or "")
    if (
        agent.can_delegate
        and agent.name != "self-coding"
        and "unknown calculation" in err.lower()
    ):
        return _UNKNOWN_CALC_SELF_CODING_NUDGE
    name = str((tool_result or {}).get("name") or "").lower()
    if name in ("image", "ocr", "ocr_v2"):
        return (
            "The site photo could not be read (empty, placeholder, or no "
            "usable pixels). Do not retry the image tool. Answer the safety "
            "question from the depth, soil type, and geometry the user already "
            "stated. Use OSHA 29 CFR 1926 Subpart P."
        )
    return _TOOL_ERROR_NUDGE


# Generalist entry points that MAY be redirected to heavy-reasoning (and
# whose turns MAY be replaced by a predefined deliverable flow). A caller
# who addressed a specialist by name (learning, quantity-surveyor, …) chose
# that agent deliberately — keyword routing must not steal the turn.
# Live find: /v1/agents/learning/chat with "invoice rate 480 SAR/m³" was
# classified as payment_certificate and answered by heavy-reasoning, which
# has no learning_engine. Same contract as F24 (predefined intercept).
ROUTING_GENERALISTS = frozenset({
    "heavy-reasoning", "project-assistant", "smart-orchestrator",
})


async def select_agent_for_message(
    user_message: str,
    requested_agent: Agent,
) -> tuple[Agent, dict[str, Any]]:
    """Decide which agent should actually handle this message.

    Returns ``(final_agent, routing_info)``. ``routing_info`` is a dict
    suitable for emitting as an SSE event so the client can see the
    routing decision. Shape::

        {
            "requested": "project-assistant",
            "final": "heavy-reasoning",
            "action": "generate_wbs",
            "confidence": 0.8,
            "reason": "needs_planning",
        }

    Pass-through cases (``final == requested``):
      * The kill-switch ``SMART_ORCH_ROUTING_DISABLED`` is set.
      * smart_orchestrator isn't registered (no construction kit loaded).
      * Message is empty / whitespace.
      * Message is a Contract Data lookup (Time for Completion / milestones).
      * Top action confidence is below the routing threshold.
      * Top action is not in ``GENERATIVE_INTENTS`` (i.e. small talk / Q&A).
      * The requested agent is already ``heavy-reasoning``.
      * ``heavy-reasoning`` isn't registered in AGENT_REGISTRY.

    Each pass-through still populates ``routing_info`` with the
    classification result so the caller can observe what would have
    happened, and so the UI can show a "stayed on" badge if it wants.
    """
    info: dict[str, Any] = {
        "requested": requested_agent.name,
        "final": requested_agent.name,
        "action": None,
        "confidence": 0.0,
        "reason": "no-op",
    }

    if _routing_disabled():
        info["reason"] = "routing_disabled_env"
        return requested_agent, info

    if not user_message or not user_message.strip():
        info["reason"] = "empty_message"
        return requested_agent, info

    if message_is_contract_data_lookup(user_message):
        info["reason"] = "contract_data_lookup"
        return requested_agent, info

    block = _get_smart_orchestrator_block()
    if block is None:
        info["reason"] = "smart_orchestrator_not_registered"
        return requested_agent, info

    try:
        result = await block.process({"user_message": user_message})
    except Exception as exc:  # noqa: BLE001
        # Routing is a best-effort enhancement; a classifier crash must
        # never break chat. Pass through with the error logged so the
        # operator can see it in Sentry/logs without a user-visible
        # failure.
        _LOG.warning("smart_orchestrator classification failed: %s", exc)
        info["reason"] = "classifier_error"
        info["error"] = str(exc)[:200]
        return requested_agent, info

    # Local import to avoid the runtime → action_router cycle that exists
    # because action_router consumes smart_orchestrator output and lives
    # in app.core (which runtime.py doesn't import at module top).
    from app.core.action_router import (
        best_action,
        needs_planning,
    )

    action, confidence = best_action(result)
    info["action"] = action
    info["confidence"] = confidence

    # Explicit "use self-coding" from a generalist wins even when the
    # classifier also matched a deliverable. One hop to self-coding.
    if (
        requested_agent.name in ROUTING_GENERALISTS
        and requested_agent.name != "self-coding"
        and _asks_self_coding(user_message)
    ):
        sc = AGENT_REGISTRY.get("self-coding")
        if sc is not None:
            info["final"] = sc.name
            info["reason"] = "self_coding_requested"
            return sc, info

    # "use N days per slab and re-run" must re-call generate_wbs, not a
    # named calculator or a stale WBS table from history.
    try:
        from app.lib.wbs_duration_overrides import message_wants_wbs_duration_rerun
        if message_wants_wbs_duration_rerun(user_message):
            info["action"] = "generate_wbs"
            info["confidence"] = max(float(info.get("confidence") or 0.0), 0.85)
            info["reason"] = "duration_override_rerun"
            if requested_agent.name in ROUTING_GENERALISTS:
                heavy = AGENT_REGISTRY.get("heavy-reasoning")
                if heavy is not None and requested_agent.name != "heavy-reasoning":
                    info["final"] = heavy.name
                    return heavy, info
            return requested_agent, info
    except Exception:  # noqa: BLE001
        _LOG.debug(
            "duration-override routing skipped; continuing classifier path",
            exc_info=True,
        )

    if _message_wants_named_calculator(user_message):
        info["action"] = None
        info["reason"] = "named_calculator"
        return requested_agent, info

    if not needs_planning(action, confidence):
        # No existing feature/workflow. A computation with no registered
        # calculator name is self-coding's job — one hop, then that agent
        # runs formula_executor_v2 once. Named calculators stay on the
        # requested generalist so construction_calc remains the path.
        if (
            requested_agent.name in ROUTING_GENERALISTS
            and requested_agent.name != "self-coding"
            and _should_handoff_unmatched_calc(user_message)
        ):
            sc = AGENT_REGISTRY.get("self-coding")
            if sc is not None:
                info["final"] = sc.name
                info["reason"] = "no_feature_or_formula"
                return sc, info
        info["reason"] = "below_routing_gate"
        return requested_agent, info

    if requested_agent.name not in ROUTING_GENERALISTS:
        info["reason"] = "specialist_passthrough"
        return requested_agent, info

    # Already on the heavy path — no redirect needed.
    if requested_agent.name == "heavy-reasoning":
        info["reason"] = "already_heavy_reasoning"
        return requested_agent, info

    heavy = AGENT_REGISTRY.get("heavy-reasoning")
    if heavy is None:
        info["reason"] = "heavy_reasoning_not_registered"
        return requested_agent, info

    info["final"] = heavy.name
    info["reason"] = "needs_planning"
    _LOG.info(
        "smart_orch routing: %s -> %s (action=%s, confidence=%.2f)",
        requested_agent.name, heavy.name, action, confidence,
    )
    return heavy, info


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_agent_file(path: Path) -> Agent:
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"missing YAML frontmatter in {path}")
    frontmatter, body = m.group(1), m.group(2).strip()

    # Lightweight YAML parsing — we don't import PyYAML to keep deps minimal.
    # Supports: key: value scalars, and `key:` followed by `  - item` lists.
    config: dict[str, Any] = {}
    current_list_key: str | None = None
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
    body, hat_ids = _apply_hat_kernels(body, config.get("hats"), path)
    return Agent(
        name=name,
        description=config.get("description", ""),
        system_prompt=body,
        allowed_blocks=list(config.get("allowed_blocks") or []),
        model=config.get("model") or KIMI_DEFAULT_MODEL,
        temperature=float(config.get("temperature", 0.3)),
        max_tokens=int(config.get("max_tokens", 2048)),
        icon=config.get("icon", ""),
        can_delegate=_resolve_can_delegate(config),
        hats=hat_ids,
        user_data_authoritative=(
            str(config.get("user_data_authoritative", "false")).strip().lower()
            in ("true", "1", "yes", "on")
        ),
    )


def _apply_hat_kernels(body: str, hats_cfg: Any, path: Path) -> tuple[str, list[str]]:
    """Bind an agent to its discipline-hat kernels (app/agents/manifests/).

    ``hats:`` in the config frontmatter is a list of manifest ids, or the
    scalar ``all`` (the orchestrator carries every discipline kernel). Each
    resolved hat's system_prompt_guidance is appended to the agent's system
    prompt under a Discipline Kernels section, and formula bindings are
    validated at load so a manifest pointing at a missing implementation
    fails HERE, not mid-conversation. An unknown hat id raises -- a typo
    must not silently produce an unhatted agent."""
    if not hats_cfg:
        return body, []
    from app.agents import catalog as _hat_catalog
    from app.agents.formulas import validate_manifest_bindings

    if isinstance(hats_cfg, str):
        if hats_cfg.strip().lower() == "all":
            manifests = sorted(_hat_catalog.list_hats(), key=lambda m: m.id)
        else:
            manifests = [m for m in [_hat_catalog.get_hat_by_id(hats_cfg.strip())] if m]
            if not manifests:
                raise ValueError(f"unknown hat '{hats_cfg}' in {path}")
    else:
        manifests = []
        for hid in hats_cfg:
            m = _hat_catalog.get_hat_by_id(str(hid).strip())
            if m is None:
                raise ValueError(f"unknown hat '{hid}' in {path}")
            manifests.append(m)
    if not manifests:
        return body, []
    for m in manifests:
        errors = validate_manifest_bindings(m)
        if errors:
            raise ValueError(f"hat '{m.id}' has broken formula bindings: {errors}")
    sections = ["\n\n## Discipline kernels\n"]
    for m in manifests:
        sections.append(f"### {m.name} ({m.id})\n{m.system_prompt_guidance}\n")
    return body + "".join(sections), [m.id for m in manifests]


def _agent_delegation_enabled() -> bool:
    """W7 — master switch for lighting up the dormant delegator agents. Default
    OFF (unset/false): only agents with an explicit ``can_delegate: true`` (today
    just heavy-reasoning) can delegate, so the live routing surface is unchanged.
    Flip ``FORK_AGENT_DELEGATION`` on to activate the ``can_delegate_when_enabled``
    agents' hand-offs. Kept default-off deliberately — enabling delegation expands
    the mis-route surface and is a per-pilot decision."""
    return (os.getenv("FORK_AGENT_DELEGATION") or "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_can_delegate(config: dict[str, Any]) -> bool:
    """Explicit ``can_delegate`` always wins; otherwise a dormant agent that
    declares ``can_delegate_when_enabled`` delegates ONLY when the master flag is
    on (W7). Default-off: unchanged behaviour."""
    def _truthy(v) -> bool:
        return str(v).strip().lower() in ("true", "1", "yes", "on")
    if _truthy(config.get("can_delegate", "false")):
        return True
    if _truthy(config.get("can_delegate_when_enabled", "false")):
        return _agent_delegation_enabled()
    return False


def _chunks(text: str, n: int) -> list[str]:
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
