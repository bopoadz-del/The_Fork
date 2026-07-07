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
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple, Union

import httpx

from app.blocks import BLOCK_REGISTRY
from app.core.rag.inject import rag_inject
from app.core.rag.retriever import project_is_rag_ready
from app.dependencies import block_instances, _create_block_instance

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


def _apply_rag_context(messages: list, rag_sys_msg: dict) -> bool:
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
        if _is_generative_request(question):
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
        else:
            directive = (
                "\n\n----- END OF REFERENCE CONTEXT -----\n\n"
                "Answer the question below using ONLY the reference context "
                "above. Reproduce its specific facts — numbers, deadlines, clause "
                "references, named principles, definitions — EXACTLY. If the "
                "context conflicts with what you think you know, the context "
                "wins. If the context does not contain the answer, say you don't "
                "have it rather than inventing one.\n\nQUESTION: "
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
_MISSING_REFERENCE_ANSWER = (
    "I could not confirm this reference in the indexed project sources"
)


def _should_short_circuit_rag_miss(
    audit_rec: Optional[Dict[str, Any]],
    rag_sys_msg: Optional[Dict[str, str]],
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
    general knowledge.
    """
    if rag_sys_msg is not None:
        return False
    if not audit_rec:
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
    project_id: Optional[str],
    user_id: Optional[str],
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
            pass

    if project_name:
        return (
            f"{_MISSING_REFERENCE_ANSWER} for {project_name}. "
            "Please check whether the document is indexed or provide the exact filename."
        )
    return (
        f"{_MISSING_REFERENCE_ANSWER}. "
        "Please check whether the document is indexed or provide the exact filename."
    )


def _is_search_tool_args_obj(obj: Any) -> bool:
    """True when ``obj`` looks like a search tool argument payload.

    Detects shapes such as:
      {"query": "...", "top_k": 5, "project_id": "..."}
      {"tool": "search_project_documents", "arguments": {...}}
      {"name": "search_project_documents", "arguments": {...}}
    """
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    # Explicit tool-call envelope shapes.
    if "arguments" in keys and ("name" in keys or "tool" in keys or "function" in keys):
        return True
    # Search argument shape: has "query" plus other search params, and no
    # obvious answer/data keys that would make it user-facing JSON.
    if "query" in keys and not (keys & _ANSWER_DATA_KEYS):
        if keys & {"top_k", "project_id", "corpus", "filters", "source", "tool", "function"}:
            return True
    return False


def _looks_like_internal_tool_json(text: str) -> bool:
    """True if ``text`` is (or contains) JSON shaped like a tool call."""
    if not text:
        return False

    def _is_tool_obj(obj) -> bool:
        if not isinstance(obj, dict):
            return False
        if _INTERNAL_TOOL_KEYS.issubset(obj.keys()):
            return True
        if obj.get("type") == "function" and "function" in obj:
            return True
        if _is_search_tool_args_obj(obj):
            return True
        return False

    stripped = text.strip()
    # Whole-string JSON.
    if stripped.startswith(("{", "[")):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(obj, dict):
                if _is_tool_obj(obj):
                    return True
            if isinstance(obj, list):
                if all(_is_tool_obj(item) for item in obj):
                    return True

    # Embedded JSON objects/lists (common when the model leaks a tool call
    # inside prose / after a formatting preamble).  Handles nested braces.
    def _candidates(open_ch: str, close_ch: str):
        i = 0
        while i < len(text):
            if text[i] == open_ch:
                depth = 1
                j = i + 1
                while j < len(text) and depth > 0:
                    if text[j] == open_ch:
                        depth += 1
                    elif text[j] == close_ch:
                        depth -= 1
                    j += 1
                if depth == 0:
                    yield text[i:j]
                    i = j
                    continue
            i += 1

    for candidate in _candidates("{", "}"):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and _is_tool_obj(obj):
            return True
    for candidate in _candidates("[", "]"):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list) and all(_is_tool_obj(item) for item in obj):
            return True
    return False


def _sanitize_final_text(text: str) -> str:
    """Prepare assistant content for display.

    1. Strip DeepSeek DSML tool-call markup.
    2. Detect raw internal tool-call JSON or search-tool argument objects.
    3. Return a controlled fallback if the content is raw internal tool args.
    4. Return empty string if nothing usable remains.
    """
    if not text:
        return ""
    cleaned = _strip_dsml(text).strip()
    if not cleaned:
        return ""

    # Markdown code block that contains only a tool/search argument payload.
    md_code = re.match(
        r"^```(?:json)?\s*([\s\S]*?)\s*```$",
        cleaned,
        re.IGNORECASE,
    )
    if md_code:
        inner = md_code.group(1).strip()
        if _looks_like_internal_tool_json(inner):
            _LOG.warning("raw tool args inside markdown code block; replacing with fallback")
            return _TOOL_FORMAT_FALLBACK

    if _looks_like_internal_tool_json(cleaned):
        _LOG.warning("raw tool-call JSON detected in final answer; replacing with fallback")
        return _TOOL_FORMAT_FALLBACK
    return cleaned


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
# bare filename (e.g. "DGII - Infra-1 - Demolition BOQ.pdf") instead of the
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


def _scrub_history(turns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Replace assistant turns that contain a WBS/BOQ-shaped table with a
    placeholder so the model can't pattern-match to a prior (often
    hallucinated) table when it should be calling the tool.

    Heuristic only: markdown tables with WBS/BOQ-like headers and >=5 rows.
    Legitimate operator-pasted tables in user turns are NOT touched.
    Returns a new list; the caller's history is left intact.
    """
    out: List[Dict[str, str]] = []
    for t in turns:
        if t.get("role") == "assistant" and _HALLUC_TABLE_RE.search(t.get("content") or ""):
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


def _user_intent_requires_tool(messages: List[Dict[str, Any]]) -> bool:
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
    text = (tail.get("content") or "").lower()
    return any(p in text for p in _DELIVERABLE_PHRASES)


# Deliverable phrase -> the SPECIFIC tool that must produce it. tool_choice=
# "required" alone let the model satisfy the requirement with the wrong tool
# (e.g. search_project_documents) and then generate the answer from memory,
# bypassing the tool's authoritative data. When one of these matches we force
# THAT tool by name so the real data path runs.
_INTENT_TOOL_MAP = (
    (("commissioning checklist", "commissioning plan", "commissioning schedule",
      "testing and commissioning", "t&c checklist"), "commissioning_checklist"),
)


def _forced_specific_tool(messages: List[Dict[str, Any]], available: set) -> Optional[str]:
    """Return a tool name to force via named tool_choice for this turn, or None.
    Gated to the user-tail (iter 0) and to tools actually available, mirroring
    ``_user_intent_requires_tool``."""
    if not messages:
        return None
    tail = messages[-1]
    if tail.get("role") != "user":
        return None
    text = (tail.get("content") or "").lower()
    for phrases, tool in _INTENT_TOOL_MAP:
        if tool in available and any(p in text for p in phrases):
            return tool
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
#   ...protected (Source: [DD-2022-175 - DG II Demolition … Part 3], chunk 941).
#   ...schedule (Source: [DD-2022-175 … Part 2], chunks 1988-1990).
# Distinct from _CITATION_RE ("[source: file, chunk N]" — source INSIDE the
# bracket). Group 1 = filename (often truncated with an ellipsis); group 2 =
# the chunk-number blob (digits, commas, and en-/em-dash ranges).
_CITATION_INLINE_BRACKET_RE = re.compile(
    r"sources?:\s*[\[【]\s*(.+?)\s*[\]】]\s*,?\s*chunks?\s+(\d[\d,\s‐-―−-]*)",
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


def _parse_source_tail(tail: str) -> Tuple[str, str]:
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


def _extract_cited_chunk_indexes(text: str) -> List[Tuple[str, int]]:
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
    out: List[Tuple[str, int]] = []

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

    return out


def _build_sources_from_audit(
    audit_rec: Dict[str, Any],
    final_text: str = "",
) -> List[Dict[str, Any]]:
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

    def _format(chunk_meta: Dict[str, Any], doc_name: str) -> Dict[str, Any]:
        score = chunk_meta.get("score") or 0.0
        conf = "High" if score >= 0.75 else "Medium" if score >= 0.5 else "Low"
        return {
            "doc_id": chunk_meta["doc_id"],
            "doc_name": _clean_path_label(doc_name),
            "page_or_section": f"chunk #{chunk_meta['chunk_index']}",
            "chunk_index": chunk_meta.get("chunk_index"),
            "chunk_id": chunk_meta.get("chunk_id"),
            "project_id": (audit_rec or {}).get("project_id"),
            "score": float(score),
            "confidence": conf,
        }

    # 1) Try to extract citations from the agent's text first.
    cites = _extract_cited_chunk_indexes(final_text)
    if cites:
        matched: List[Dict[str, Any]] = []
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
                    # that rewrote the dash style still resolves.
                    name = _doc_name(doc_id)
                    name_n = _normalise_filename(name)
                    if cited_token_n and name_n and cited_token_n not in name_n and name_n not in cited_token_n:
                        continue
                # else: the cite carried a chunk number that already matched
                # above (cited_idx != -1, cidx == cited_idx). That index
                # uniquely identifies the injected chunk, so trust it even when
                # the model truncated the filename (gpt-oss "DG II … Part 3").
                key = (doc_id, cidx)
                if key in seen:
                    continue
                seen.add(key)
                matched.append(_format(c, _doc_name(doc_id)))
        if matched:
            return matched

    # 2) Filename-mention fallback: the model may have named a source in
    #    prose without a formal citation marker. If any injected filename
    #    appears in the answer, surface the highest-scoring chunk of that
    #    document.
    if final_text:
        text_n = _normalise_filename(final_text)
        mention_hits: List[Dict[str, Any]] = []
        seen_mentions: set = set()
        for c in sorted(chunks, key=lambda x: -(x.get("score") or 0)):
            doc_id = c.get("doc_id")
            name = _doc_name(doc_id)
            name_n = _normalise_filename(name)
            if name_n and name_n in text_n and doc_id not in seen_mentions:
                seen_mentions.add(doc_id)
                mention_hits.append(_format(c, name))
        if mention_hits:
            return mention_hits[:3]

    # 3) Final fallback: top-3 retrieved chunks by score.
    by_score = sorted(chunks, key=lambda c: -(c.get("score") or 0))[:3]
    return [_format(c, _doc_name(c["doc_id"])) for c in by_score]


# File extensions whose BOQs are safe to turn into a cost workbook on click.
# Digital grids only: a scanned PDF BOQ won't parse and OOMs the 2 GB box
# (memory the-fork-boq-always-xlsx), so PDFs are deliberately excluded here.
_BOQ_COST_EXPORT_EXTS = (".xlsx", ".csv")
_BOQ_NAME_HINTS = ("boq", "bill of quantities", "priced", "tender")


def _is_cost_boq_source(doc: Dict[str, Any]) -> bool:
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


def _is_spec_or_rfp_source(doc: Dict[str, Any]) -> bool:
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
    audit_rec: Dict[str, Any],
    final_text: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
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

    exports: List[Dict[str, Any]] = []
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
    rfp_doc_ids: List[str] = []
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
        payload: Dict[str, Any] = {
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


DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"

# Groq provides an OpenAI-compatible chat-completions endpoint, so the only
# things that differ from DeepSeek are the base URL, the env-var name, and the
# default model id. Tool-calling payload shape is identical.
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Kimi / Moonshot native API — OpenAI-compatible chat-completions. Same payload
# shape as Groq/DeepSeek (verified: tool-calling + streaming accepted). K2 models
# (kimi-k2.6 default) are reasoning models: the response carries a separate
# `reasoning_content` chain-of-thought and the real answer in `content`; we read
# `content` and the outbound sanitiser strips `reasoning_content` on replay.
KIMI_API_URL = "https://api.moonshot.ai/v1/chat/completions"
KIMI_DEFAULT_MODEL = "kimi-k2.6"

# Ollama exposes an OpenAI-compatible endpoint at /v1/chat/completions
# (v0.1.31+). Self-hosted on the operator's PC or a VPS. No auth, no token
# cost, no TPM rate limits — bounded by local hardware. Used when the
# operator wants to escape cloud rate limits entirely.
OLLAMA_DEFAULT_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_DEFAULT_MODEL = "qwen2.5:7b-instruct"


def _llm_config() -> Dict[str, Any]:
    """Pick the active LLM provider's URL + env-key + default model.

    Precedence:
      1. Explicit ``LLM_PROVIDER`` env var (``deepseek`` | ``groq`` |
         ``ollama``) wins.
      2. Otherwise: if ``GROQ_API_KEY`` is set, use Groq (free tier).
      3. Otherwise: DeepSeek (the historical default).

    Per-provider override envs let the operator pin a specific model
    without code changes:
      - ``GROQ_MODEL`` / ``DEEPSEEK_MODEL`` / ``OLLAMA_MODEL``
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
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "groq" if os.getenv("GROQ_API_KEY") else "deepseek"
    if provider == "ollama":
        url = os.getenv("OLLAMA_URL", OLLAMA_DEFAULT_URL).rstrip("/")
        # Accept both the bare host (http://host:11434) and the full
        # OAI-shape path. Append the canonical suffix when missing.
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
            "default_model": os.getenv("GROQ_MODEL", GROQ_DEFAULT_MODEL),
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
        }
    return {
        "provider": "deepseek",
        "url": DEEPSEEK_API_URL,
        "env_key": "DEEPSEEK_API_KEY",
        "default_model": os.getenv("DEEPSEEK_MODEL", DEEPSEEK_DEFAULT_MODEL),
    }


def _llm_fallback_config(primary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Config for the fallback LLM provider, or ``None`` when none is usable.

    Controlled by ``LLM_FALLBACK_PROVIDER`` (``ollama`` | ``groq`` |
    ``deepseek``). Returns ``None`` when it is unset, names the primary
    provider, or its API-key env is required but missing. This lets a
    Groq-primary deploy degrade to gpt-oss/Ollama on a retryable failure
    (413 request-too-large, 429 rate-limit, 5xx, network) instead of failing
    the whole turn — the resilience the operator asked about ("keep gpt-oss as
    a fallback to Groq").

    Reuses ``_llm_config`` for URL/suffix normalisation by pinning the provider
    through the env for the duration of one synchronous call (no ``await``
    between set and restore, so no cross-coroutine interleave).
    """
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


def _provider_temperature(cfg: Dict[str, Any], default: float) -> float:
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


def _sanitize_messages_for_provider(
    messages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return a copy of ``messages`` with only OpenAI-standard keys per message
    (and per tool_call). Never mutates the input list."""
    clean: List[Dict[str, Any]] = []
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
    return clean


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

# Llama 3.x native tool-call markup: `<function=name{"k":"v",...}>` — Groq's
# strict tool-use validator rejects this shape with HTTP 400 `tool_use_failed`
# and emits the raw markup in `failed_generation`. We recover it into proper
# OpenAI-style tool_calls so the agent loop can dispatch and continue.
_LLAMA_FUNC_RE = re.compile(
    r"<\s*function\s*=\s*([A-Za-z_][\w]*)\s*(\{.*?\})\s*>",
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
    icon: str = ""
    can_delegate: bool = False

    def tool_definitions(self, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Build DeepSeek-style tool definitions.

        Includes one tool per allowed block, plus synthetic tools:
        - ``remember_fact`` — always available.
        - ``search_project_documents`` — only when ``project_id`` is set.
        - ``delegate_to_agent`` — only when ``self.can_delegate``.
        """
        # File-consuming blocks need an explicit `file_path` schema so the
        # LLM can't emit the call with empty args. Without this, the agent
        # called e.g. boq_processor with {} after search_project_documents,
        # got "No file_path provided", and deflected to the user. The
        # runtime's _resolve_block_file_input then maps the bare filename
        # to the encrypted stored path, so the agent only needs to pass
        # the original_name string it saw from search_project_documents.
        _FILE_TOOL_SCHEMAS = {
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
                            "'DGII - Infra-1 - Demolition BOQ.pdf'). MUST come "
                            "from a prior search_project_documents call."
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
                },
                "required": ["file_path"],
            },
        }

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
                                    "these keys: hvac, electrical, fire, plumbing, "
                                    "elevator, facade, bms."
                                ),
                            },
                        },
                        "required": ["systems"],
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
        user_id: Optional[str] = None,
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
        )
        if _rag_sys_msg and _rag_sys_msg.get("content"):
            _apply_rag_context(messages, _rag_sys_msg)

        # Fast path: exact reference miss with no RAG context.  Skip the
        # model/tool loop entirely and return a controlled not-found answer
        # immediately.  Project facts / document listings are not useful for
        # confirming a specific absent reference.
        if _should_short_circuit_rag_miss(_rag_audit, _rag_sys_msg):
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

        tool_calls_made: List[Dict[str, Any]] = []
        # Root fix for the tool-loop (mirrors chat_stream): cap explicit
        # search_project_documents calls, then stop offering the tool so the
        # model answers from injected context instead of grinding to the cap.
        SEARCH_TOOL_CAP = int(os.getenv("AGENT_SEARCH_TOOL_CAP", "2"))
        search_calls = 0
        excluded_tools: set = set()
        # Once a deliverable (non-search) tool returns, force a tool-free
        # synthesis call — see the streaming loop for the full rationale (stops
        # the tool-loop and the Groq large-context tool_use_failed/429 hang).
        force_synthesis = False
        _force_synth_enabled = os.getenv("AGENT_FORCE_SYNTHESIS", "1") != "0"

        for iteration in range(MAX_TOOL_ITERATIONS):
            await _emit("iteration", {"n": iteration + 1})
            resp = await self._call_llm(
                messages, api_key, project_id=project_id, user_id=user_id,
                exclude_tools=excluded_tools or None,
                with_tools=not force_synthesis,
            )
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
                    # Genuine final answer — sanitize DSML markup, raw tool JSON,
                    # and empty content before it reaches the user.
                    final_text = _sanitize_final_text(raw_content)
                    # If sanitization left nothing usable, force one no-tools call
                    # so the model must produce a plain-text answer instead of an
                    # empty bubble or leaked JSON.
                    if not final_text.strip():
                        forced_resp = await self._call_llm(messages, api_key, project_id=project_id, with_tools=False, user_id=user_id)
                        if forced_resp.get("status") == "error":
                            final_text = _EMPTY_RESPONSE_FALLBACK
                        else:
                            forced_msg = forced_resp["choice"].get("message") or {}
                            final_text = _sanitize_final_text(forced_msg.get("content") or "")
                            if not final_text.strip():
                                final_text = _EMPTY_RESPONSE_FALLBACK
                    final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
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
            messages.append(assistant_msg)
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
                if (
                    _force_synth_enabled and ok
                    and tool_result.get("name") != "search_project_documents"
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
                    "content": json.dumps(
                        {**(tool_result["result"] if isinstance(tool_result.get("result"), dict) else {"result": tool_result.get("result")}),
                         **({"validation": tool_result["validation"]} if "validation" in tool_result else {})},
                        default=str,
                    )[:8000],
                })

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
        final_text = _sanitize_final_text(forced_msg.get("content") or "")
        if not final_text.strip():
            final_text = _EMPTY_RESPONSE_FALLBACK
        final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
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

    async def chat_stream(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        api_key: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        rag_debug: bool = False,
        _depth: int = 0,
        _call_stack: Optional[List[str]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
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

        # Read knobs at call-time so tests can monkeypatch env. Bad values
        # fall back to safe defaults rather than crashing the stream.
        try:
            timeout_s = float(os.getenv("CHAT_STREAM_TIMEOUT_SECONDS") or "90")
        except ValueError:
            timeout_s = 90.0
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

            async def producer() -> None:
                try:
                    async for event in self._chat_stream_impl(
                        user_message=user_message,
                        history=history,
                        api_key=api_key,
                        user_id=user_id,
                        project_id=project_id,
                        conversation_id=conversation_id,
                        rag_debug=rag_debug,
                        _depth=_depth,
                        _call_stack=_call_stack,
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

            # Absolute deadline — computed ONCE and NOT reset by events.
            # Heartbeats keep the queue active even when the upstream LLM is
            # hung, so a per-get() timeout would never trigger. The fixed
            # deadline is the only correct semantic for a wall-clock cap.
            deadline = time.monotonic() + timeout_s

            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # Wall-clock cap exceeded. Emit a structured timeout
                        # error so the frontend's friendlyErrorMessage maps
                        # it (substring "timeout") to a clean banner.
                        _LOG.warning(
                            "chat_stream: wall-clock deadline exceeded after %.1fs",
                            timeout_s,
                        )
                        if not token_emitted:
                            yield {"type": "token", "content": _EMPTY_RESPONSE_FALLBACK}
                            token_emitted = True
                        yield {
                            "type": "error",
                            "message": (
                                f"Response timeout — stream exceeded "
                                f"the wall-clock timeout ({timeout_s:.0f}s)."
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

        try:
            async for event in _inner():
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
            yield {"type": "end", "iterations": 0, "sources": []}

    async def _chat_stream_impl(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        api_key: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        rag_debug: bool = False,
        _depth: int = 0,
        _call_stack: Optional[List[str]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Internal implementation. ``chat_stream`` wraps this with an emit
        guarantee so silent / crashing exits become structured error events."""
        cfg = _llm_config()
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
            yield {"type": "end", "iterations": 0, "sources": []}
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
        )
        if _rag_sys_msg and _rag_sys_msg.get("content"):
            _apply_rag_context(messages, _rag_sys_msg)

        # Fast path: exact reference miss with no RAG context.  Skip the
        # model/tool loop entirely.  Project facts / document listings are not
        # useful for confirming a specific absent reference.
        if _should_short_circuit_rag_miss(_rag_audit, _rag_sys_msg):
            answer = _build_missing_reference_answer(project_id, user_id)
            if conversation_id:
                from app.core import agent_memory
                agent_memory.append_message(conversation_id, "assistant", answer)
            for chunk in _chunks(answer, 80):
                yield {"type": "token", "content": chunk}
            yield {"type": "end", "iterations": 0, "sources": []}
            return

        # Tool results accumulated this turn — feeds the schedule download offer
        # (a generate_wbs call becomes a 'Schedule (Excel)' export descriptor).
        stream_tool_results: List[Dict[str, Any]] = []
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
        force_synthesis = False
        _force_synth_enabled = os.getenv("AGENT_FORCE_SYNTHESIS", "1") != "0"
        # True token streaming for the FINAL synthesis call only. Gated to:
        #   * SYNTHESIS_STREAMING=1  (instant prod kill-switch; default off)
        #   * provider == groq       (the only verified streaming path)
        #   * grounded adapter INACTIVE (a with_tools=False call otherwise routes
        #     to the local tinker adapter / rewrite-pass in _call_llm; streaming
        #     would bypass both, so leave those deploys on the non-streaming path)
        # When enabled, the force_synthesis iteration streams provider deltas as
        # token events instead of computing the whole answer then re-chunking it
        # (which made first_token ~= total). Any pre-first-token failure falls
        # back to the untouched non-streaming path below.
        from app.core.llm import tinker_adapter as _tinker_adapter
        _synth_stream_enabled = (
            os.getenv("SYNTHESIS_STREAMING") == "1"
            and cfg["provider"] == "groq"
            and not _tinker_adapter.is_available()
            and not _tinker_adapter.is_rewrite_pass_enabled()
            # rag_debug needs the whole final text to run its with/without-RAG
            # A/B in the non-streaming branch; don't stream those turns.
            and not rag_debug
        )
        # WARNING-level timing instrumentation: Render drops INFO app logs on this
        # service, so the deliverable-hang call-count/latency was invisible.
        # Gate behind AGENT_TIMING_LOG=1 so it's off by default.
        _timing = os.getenv("AGENT_TIMING_LOG") == "1"
        _turn_t0 = time.monotonic()
        # Served model string of the LAST successful LLM call this turn, surfaced
        # in the `end` event so observability (fork_cli/smoke) can tell the
        # configured provider apart from a silent fallback (e.g. Kimi K2 vs an
        # Ollama fallback) per run. The model string alone is a sufficient
        # discriminator; None until the first successful call.
        served_model: Optional[str] = None
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
                acc: List[str] = []
                pending = ""
                try:
                    async for _delta in self._stream_synthesis(
                        messages, api_key, project_id=project_id, user_id=user_id,
                    ):
                        streamed_any = True
                        acc.append(_delta)
                        pending += _delta
                        # Flush only COMPLETE lines, sanitised. Both citation
                        # regexes are within-line / line-anchored, so per-line
                        # sanitisation equals full-text as long as we hold the
                        # partial last line (which we do).
                        nl = pending.rfind("\n")
                        if nl >= 0:
                            seg, pending = pending[: nl + 1], pending[nl + 1:]
                            seg = _sanitize_inline_paths(_sanitize_citation_labels(seg))
                            if seg:
                                yield {"type": "token", "content": seg}
                except _SynthStreamError as _se:
                    if streamed_any:
                        _LOG.warning("chat_stream: synthesis stream dropped mid-way (%s)", _se)
                    else:
                        _LOG.info("chat_stream: synthesis stream unavailable (%s) — non-streaming fallback", _se)
                        fell_back = True
                if not fell_back:
                    if pending:
                        seg = _sanitize_inline_paths(_sanitize_citation_labels(pending))
                        if seg:
                            yield {"type": "token", "content": seg}
                    raw = "".join(acc)
                    # Fully-sanitised accumulated text: what we persist + feed
                    # sources/exports (must match the non-streaming path, not a
                    # concatenation of per-line flushes).
                    final_text = _sanitize_inline_paths(
                        _sanitize_citation_labels(_sanitize_final_text(raw))
                    )
                    if not final_text.strip():
                        # Nothing usable streamed (empty response) — preserve the
                        # empty-final forced-retry path (non-streamed, chunked).
                        forced_resp = await self._call_llm(
                            messages, api_key, project_id=project_id,
                            with_tools=False, user_id=user_id,
                        )
                        if forced_resp.get("status") == "error":
                            final_text = _EMPTY_RESPONSE_FALLBACK
                        else:
                            served_model = (forced_resp.get("raw") or {}).get("model") or served_model
                            _fm = forced_resp["choice"].get("message") or {}
                            final_text = _sanitize_final_text(_fm.get("content") or "")
                            if not final_text.strip():
                                final_text = _EMPTY_RESPONSE_FALLBACK
                        final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
                        for chunk in _chunks(final_text, 80):
                            yield {"type": "token", "content": chunk}
                    if _timing:
                        _LOG.warning("TIMING chat_stream STREAMED-SYNTH iter=%d chars=%d cum=%.1fs",
                                     iteration, len(final_text), time.monotonic() - _turn_t0)
                    if conversation_id:
                        from app.core import agent_memory
                        agent_memory.append_message(conversation_id, "assistant", final_text)
                    yield {
                        "type": "end",
                        "iterations": iteration + 1,
                        "model": served_model,
                        "sources": _build_sources_from_audit(_rag_audit, final_text),
                        "exports": _build_exports_from_audit(_rag_audit, final_text, stream_tool_results),
                    }
                    return
            _call_t0 = time.monotonic()
            resp = await self._call_llm(
                messages, api_key, project_id=project_id, user_id=user_id,
                exclude_tools=excluded_tools or None,
                with_tools=not force_synthesis,
            )
            if _timing:
                _tcs = [((tc.get("function") or {}).get("name")) for tc in (resp.get("choice", {}).get("message", {}).get("tool_calls") or [])]
                _LOG.warning("TIMING chat_stream iter=%d call=%.1fs status=%s tools=%s cum=%.1fs",
                             iteration, time.monotonic() - _call_t0, resp.get("status"),
                             _tcs or "final", time.monotonic() - _turn_t0)
            if resp.get("status") == "error":
                err = resp.get("error", "LLM call failed")
                _LOG.warning("chat_stream: iter=%d LLM error %s", iteration, err)
                yield {"type": "error", "message": err}
                return
            served_model = (resp.get("raw") or {}).get("model") or served_model
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
                    # Final answer — sanitize DSML markup, raw tool JSON, and
                    # empty content before streaming it to the user.
                    final_text = _sanitize_final_text(raw_content)
                    # If sanitization left nothing usable, force one no-tools call
                    # so the model must produce a plain-text answer instead of an
                    # empty bubble or leaked JSON.
                    if not final_text.strip():
                        _LOG.info("chat_stream: empty final_text, forcing no-tools retry")
                        if _timing:
                            _LOG.warning("TIMING chat_stream EMPTY-FINAL raw=%dc -> forced retry, cum=%.1fs",
                                         len(raw_content), time.monotonic() - _turn_t0)
                        _fr_t0 = time.monotonic()
                        forced_resp = await self._call_llm(messages, api_key, project_id=project_id, with_tools=False, user_id=user_id)
                        if _timing:
                            _LOG.warning("TIMING chat_stream forced-retry call=%.1fs status=%s cum=%.1fs",
                                         time.monotonic() - _fr_t0, forced_resp.get("status"), time.monotonic() - _turn_t0)
                        if forced_resp.get("status") == "error":
                            final_text = _EMPTY_RESPONSE_FALLBACK
                        else:
                            served_model = (forced_resp.get("raw") or {}).get("model") or served_model
                            forced_msg = forced_resp["choice"].get("message") or {}
                            final_text = _sanitize_final_text(forced_msg.get("content") or "")
                            if not final_text.strip():
                                final_text = _EMPTY_RESPONSE_FALLBACK
                    final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
                    if _timing:
                        _LOG.warning("TIMING chat_stream STREAMING-FINAL iter=%d chars=%d cum=%.1fs",
                                     iteration, len(final_text), time.monotonic() - _turn_t0)
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
                            )
                            off_response = (no_rag_resp.get("choice", {})
                                            .get("message", {})
                                            .get("content", "") or "")
                        except Exception as _e:
                            off_response = f"[rag_debug off-run failed: {_e}]"
                        yield {
                            "type": "end",
                            "iterations": iteration + 1,
                            "rag_debug": {
                                "on_response": final_text,
                                "off_response": off_response,
                                "audit": _rag_audit,
                            },
                        }
                        return
                    yield {
                        "type": "end",
                        "iterations": iteration + 1,
                        "model": served_model,
                        "sources": _build_sources_from_audit(_rag_audit, final_text),
                        "exports": _build_exports_from_audit(_rag_audit, final_text, stream_tool_results),
                    }
                    return

            messages.append(assistant_msg)
            for tc in tool_calls:
                fn = (tc.get("function") or {})
                if fn.get("name") == "search_project_documents":
                    search_calls += 1
                    if search_calls >= SEARCH_TOOL_CAP:
                        # Stop offering the search tool for the rest of this turn —
                        # the model has searched enough; force it to answer.
                        excluded_tools.add("search_project_documents")
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
                stream_tool_results.append(tool_result)
                # A successful deliverable tool (anything but search) means the
                # model now has authoritative data to synthesize from — stop
                # offering tools so the next call is a clean, tool-free answer.
                if (
                    _force_synth_enabled
                    and tool_result.get("ok", True)
                    and tool_result.get("name") != "search_project_documents"
                ):
                    force_synthesis = True
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
                    "content": json.dumps(
                        {**(tool_result["result"] if isinstance(tool_result.get("result"), dict) else {"result": tool_result.get("result")}),
                         **({"validation": tool_result["validation"]} if "validation" in tool_result else {})},
                        default=str,
                    )[:8000],
                })

        # Hit the cap without a final answer — force one more call with tools disabled.
        _LOG.warning("chat_stream: hit MAX_TOOL_ITERATIONS=%d, forcing no-tools retry",
                     MAX_TOOL_ITERATIONS)
        forced_resp = await self._call_llm(messages, api_key, project_id=project_id, with_tools=False, user_id=user_id)
        if forced_resp.get("status") == "error":
            yield {"type": "error", "message": f"Hit {MAX_TOOL_ITERATIONS}-iteration cap."}
            return
        served_model = (forced_resp.get("raw") or {}).get("model") or served_model
        forced_msg = forced_resp["choice"].get("message") or {}
        final_text = _sanitize_final_text(forced_msg.get("content") or "")
        if not final_text.strip():
            # Forced retry returned empty or raw tool JSON — substitute the
            # user-safe fallback so the UI never renders an empty bubble.
            _LOG.warning("chat_stream: forced final returned empty, using fallback")
            final_text = _EMPTY_RESPONSE_FALLBACK
        final_text = _sanitize_inline_paths(_sanitize_citation_labels(final_text))
        for chunk in _chunks(final_text, 80):
            yield {"type": "token", "content": chunk}
        if conversation_id:
            from app.core import agent_memory
            # User turn was already persisted up front.
            agent_memory.append_message(conversation_id, "assistant", final_text)
        _LOG.info("chat_stream: end (forced_final) chars=%d", len(final_text))
        yield {
            "type": "end",
            "iterations": MAX_TOOL_ITERATIONS,
            "forced_final": True,
            "model": served_model,
            "sources": _build_sources_from_audit(_rag_audit, final_text),
            "exports": _build_exports_from_audit(_rag_audit, final_text, stream_tool_results),
        }

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

    async def _rewrite_with_adapter(
        self, messages: List[Dict[str, Any]], original_text: str
    ) -> Optional[str]:
        """Broad rewrite-pass: re-ground a cloud-provider prose response
        through the Tinker LoRA adapter. Returns the rewritten string on
        success, ``None`` on any failure (timeout, adapter error, no RAG
        context, empty result) so the caller serves the original text
        unchanged.

        Gated by ``GROUNDED_ADAPTER_REWRITE_PASS`` + ``is_available()``.
        Hard 5s timeout regardless of ``GROUNDED_ADAPTER_TIMEOUT``: the
        rewrite is an extra leg on the chat turn and must not double its
        latency budget. Timeouts are appended to the rag_audit JSONL with
        ``event="rewrite_pass_timeout"`` so prod cost/latency drift is
        visible without reading Render logs.
        """
        from app.core.llm import tinker_adapter

        if not tinker_adapter.is_rewrite_pass_enabled():
            return None
        if not tinker_adapter.is_available():
            return None
        if not (original_text or "").strip():
            return None
        # RAG injection is now project-driven (see rag_inject: project_id guard).
        # On any other agent the reverse-scan below would pick up the
        # agent-identity prompt or project context and "ground" the
        # answer in that — wrong by construction. Skip rewrite entirely.
        if self.name != "project-assistant":
            return None

        # Find the RAG injection by its header — set by
        # format_chunks_as_system_message. Position is unreliable: the
        # agent prompt, project context, memory facts, and the user
        # turn all sit around it. Header match is the durable signal.
        rag_system = ""
        for m in messages:
            if m.get("role") != "system":
                continue
            content = (m.get("content") or "").strip()
            if content.startswith("Relevant project context (top"):
                rag_system = content
                break
        # Threshold fired (top_score < RAG_CONFIDENCE_THRESHOLD) or no
        # injection happened this turn — nothing to ground in. Serve the
        # original cloud response unchanged.
        if not rag_system:
            return None

        rewrite_prompt = (
            "Rewrite the following answer to be strictly grounded in the "
            "context above. Preserve facts that match the context; correct "
            "or remove facts that contradict it. Do not add information not "
            "present in the context. Reply with only the rewritten answer.\n\n"
            f"Answer to rewrite:\n{original_text.strip()}"
        )

        import time as _time
        started = _time.monotonic()
        try:
            result = await tinker_adapter.call(
                rewrite_prompt, rag_system, self.max_tokens, self.temperature,
                timeout_override=5.0,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("rewrite-pass adapter raised: %s; serving original", exc)
            return None
        elapsed = _time.monotonic() - started

        if result.get("status") != "success":
            err = (result.get("error") or "")
            if "timed out" in err.lower():
                try:
                    from app.core.rag import audit as _audit
                    _audit.write({
                        "event": "rewrite_pass_timeout",
                        "agent_name": self.name,
                        "elapsed_seconds": round(elapsed, 3),
                        "error": err,
                        "original_preview": (original_text or "")[:200],
                    })
                except Exception:  # noqa: BLE001
                    pass
            _LOG.warning(
                "rewrite-pass adapter non-success (%.2fs): %s; serving original",
                elapsed, err,
            )
            return None

        rewritten = (result.get("response") or "").strip()
        if not rewritten:
            return None
        _LOG.info("rewrite-pass adapter success in %.2fs", elapsed)
        return rewritten

    async def _call_grounded_adapter(
        self, messages: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Invoke the Tinker-hosted grounded LoRA on a tool-less turn.

        Returns ``None`` on any failure so the caller falls through to the
        normal cloud-provider path. On success, returns the same
        ``{"status": "success", "choice": ..., "raw": ...}`` envelope
        ``_call_llm`` produces, with an OpenAI-shape ``choice`` synthesized
        from the adapter's text reply so the runtime loop's downstream
        parsing (final_text vs tool_calls) is unchanged.

        Conventions:
        - The last user message is the question.
        - The last system message (RAG injection runs last in our build
          order) is passed as the adapter's ``system_prompt`` so its
          training format (``Context:\\n<chunks>\\n\\nQuestion: <q>``) is
          honoured. The agent-identity system prompt is intentionally
          dropped here — the adapter wasn't trained on it.
        """
        from app.core.llm import tinker_adapter

        user_message = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_message = (m.get("content") or "").strip()
                break
        if not user_message:
            return None

        rag_system = ""
        for m in reversed(messages):
            if m.get("role") == "system" and (m.get("content") or "").strip():
                rag_system = m["content"].strip()
                break

        try:
            result = await tinker_adapter.call(
                user_message, rag_system, self.max_tokens, self.temperature
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("grounded adapter raised: %s; falling back", exc)
            return None

        if result.get("status") != "success":
            _LOG.warning(
                "grounded adapter returned non-success: %s; falling back",
                result.get("error"),
            )
            return None

        text = result.get("response") or ""
        choice = {
            "index": 0,
            "message": {"role": "assistant", "content": text, "tool_calls": []},
            "finish_reason": "stop",
        }
        return {
            "status": "success",
            "choice": choice,
            "raw": {
                "provider": result.get("provider", "tinker_grounded_adapter"),
                "model": result.get("model"),
            },
        }

    async def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        api_key: str,
        project_id: Optional[str] = None,
        with_tools: bool = True,
        user_id: Optional[str] = None,
        exclude_tools: Optional[set] = None,
    ) -> Dict[str, Any]:
        cfg = _llm_config()
        # Grounded LoRA adapter (narrow path): serve forced-final / tool-less
        # turns directly so the RAG-grounded weights see production traffic.
        # Tool-using turns stay on the cloud provider — the adapter doesn't
        # emit tool_calls. Gated by GROUNDED_ADAPTER_ENABLED + a configured
        # sampler-weights path; any failure falls through to the normal call.
        if not with_tools:
            from app.core.llm import tinker_adapter
            if tinker_adapter.is_available():
                adapter_result = await self._call_grounded_adapter(messages)
                if adapter_result is not None:
                    return adapter_result
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
                    pass
        # Agent configs default to "deepseek-chat"; when the runtime is routed
        # to a different provider we remap that placeholder to the provider's
        # default model. An agent that explicitly pinned a provider-specific
        # model (e.g. "llama-3.3-70b-versatile") is left alone.
        model = self.model
        if cfg["provider"] != "deepseek" and model.startswith("deepseek-"):
            model = cfg["default_model"]
        # Whitelist-sanitise every outbound message (drops `reasoning` and any
        # other non-standard field that would make a strict provider — Groq —
        # reject the request). Single chokepoint, covers all callers.
        messages = _sanitize_messages_for_provider(messages)
        if "moonshot-v1" in model:
            # Moonshot v1 models fail to tokenize the full K2-style message
            # history (tool_calls arrays, tool role, etc.). Send only the last
            # user turn plus tools; this matches the direct-API shape that v1
            # has been verified to accept.
            #
            # The last user turn has RAG context folded into it, which can be
            # tens of thousands of tokens. v1's tokenizer appears to reject
            # very long concatenated contexts. Truncate to the first 1500
            # characters (the original user prompt + a little context) for the
            # diagnostic test.
            raw_content = messages[-1].get("content", "")
            content = raw_content[:1500] if len(raw_content) > 1500 else raw_content
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": False,
            }
            tools = self.tool_definitions(project_id=project_id)
        else:
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
                if self.name == "project-assistant" else None
            )
            requires_tool = (
                self.name == "project-assistant"
                and _user_intent_requires_tool(messages)
            )
        else:
            forced_tool = None
            requires_tool = False

        # Provider attempts: primary first, then the configured fallback (if
        # any) on a RETRYABLE failure only — 413 (request too large), 429
        # (rate limit), 5xx, or a network/timeout error. Auth/validation
        # errors (400/401/403/404) are not retried: a different provider
        # won't fix a bad key or a malformed request.
        fallback_cfg = _llm_fallback_config(cfg)
        attempts = [(cfg, api_key, model)]
        if fallback_cfg:
            fb_key = os.getenv(fallback_cfg["env_key"]) if fallback_cfg["env_key"] else ""
            attempts.append((fallback_cfg, fb_key, fallback_cfg["default_model"]))

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

        last_error: Dict[str, Any] = {"status": "error", "error": "LLM call failed"}
        for attempt_idx, (a_cfg, a_key, a_model) in enumerate(attempts):
            is_last = attempt_idx == len(attempts) - 1
            payload["model"] = a_model
            # Per-attempt like tool_choice: a fallback to a different provider
            # must get that provider's accepted temperature (kimi -> 1, others ->
            # the agent's own), not the primary's.
            payload["temperature"] = _provider_temperature(a_cfg, self.temperature)
            if tools and with_tools:
                payload["tool_choice"] = _tool_choice_for(a_cfg["provider"])
            try:
                if "moonshot-v1" in a_model:
                    import logging as _v1_log
                    _v1_log.warning("moonshot-v1 payload: %s", json.dumps(payload, default=str)[:2000])
                async with httpx.AsyncClient(timeout=120.0) as client:
                    r = await client.post(
                        a_cfg["url"],
                        json=payload,
                        headers=(
                            {"Authorization": f"Bearer {a_key}", "Content-Type": "application/json"}
                            if a_key
                            else {"Content-Type": "application/json"}
                        ),
                    )
                if "moonshot-v1" in a_model and r.status_code >= 400:
                    import logging as _v1_log
                    _v1_log.warning("moonshot-v1 response %s: %s", r.status_code, r.text[:500])
            except httpx.TimeoutException:
                last_error = {"status": "error", "error": f"{a_cfg['provider']} LLM call timed out (120s)."}
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
                    pass
                last_error = {"status": "error", "error": f"{a_cfg['provider']} HTTP {r.status_code}: {body[:300]}"}
                if (_is_retryable(r.status_code) or tool_use_failed_unrecovered) and not is_last:
                    reason = "tool_use_failed (prose)" if tool_use_failed_unrecovered else f"HTTP {r.status_code}"
                    _LOG.warning("llm: %s %s — falling back to %s", a_cfg["provider"], reason, attempts[attempt_idx + 1][0]["provider"])
                    continue
                # No fallback taken (non-retryable status, or this was the last
                # provider): this error ENDS the turn. A silently-returned 4xx
                # (e.g. Moonshot's temperature 400) was previously invisible in
                # prod logs, so the acceptance criterion "no HTTP 400 events"
                # could not be verified from logs that never recorded it. Observe
                # only — no retry logic added here.
                _LOG.warning("llm: %s HTTP %s — no fallback taken, turn errors: %s",
                             a_cfg["provider"], r.status_code, body[:200])
                if "moonshot-v1" in a_model:
                    diag = json.dumps(payload, default=str, ensure_ascii=False)
                    return {
                        "status": "error",
                        "error": f"{a_cfg['provider']} HTTP {r.status_code}: {body[:300]} | payload={diag[:1500]}",
                    }
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
                    pass
                # Rewrite-pass (broad grounded-adapter path). When the cloud
                # provider returned a tool-free prose answer AND
                # GROUNDED_ADAPTER_REWRITE_PASS is on, re-ground the answer
                # through the Tinker LoRA. Any failure (timeout, error, no
                # RAG context) serves the original answer unchanged.
                msg = choice.get("message") or {}
                if msg and not (msg.get("tool_calls") or []):
                    # Observe-only tripwire: the model answered a turn whose
                    # intent REQUIRED a tool (requires_tool) without calling one.
                    # On groq/kimi we can't force tool_choice (it 400s), so the
                    # model may skip on "auto" and emit a degenerate prose answer
                    # — the exact failure the hardened smoke catches externally.
                    # Emit one grep-able marker so a resurgence is queryable from
                    # prod logs (list_logs text="TOOL_SKIP"). This does NOT act:
                    # no forced retry is triggered here. (A deepseek DSML-in-prose
                    # tool call recovered downstream would false-positive here,
                    # but we don't run deepseek in prod and this only logs.)
                    if requires_tool:
                        _LOG.warning(
                            "TOOL_SKIP agent=%s provider=%s model=%s answer_chars=%d "
                            "— deliverable intent, no tool call",
                            self.name, a_cfg.get("provider"),
                            data.get("model") or a_model, len(msg.get("content") or ""),
                        )
                    original_text = msg.get("content") or ""
                    if original_text.strip():
                        rewritten = await self._rewrite_with_adapter(messages, original_text)
                        if rewritten:
                            msg["content"] = rewritten
                            choice["message"] = msg
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
        messages: List[Dict[str, Any]],
        api_key: str,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream a tool-free synthesis completion, yielding content deltas.

        Used ONLY for the forced (``with_tools=False``) synthesis call and only
        on Groq (the verified provider). Mirrors ``_call_llm``'s setup — same
        ``_llm_config``, same deepseek->default model remap, same soft daily-cap
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
        if cfg["provider"] != "groq":
            # Only Groq streaming is verified; anything else falls back.
            raise _SynthStreamError("streaming synthesis only verified for groq")
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
        if cfg["provider"] != "deepseek" and model.startswith("deepseek-"):
            model = cfg["default_model"]
        messages = _sanitize_messages_for_provider(messages)
        payload = {
            "model": model,
            "messages": messages,
            # Same per-provider shaping as _call_llm (single provider here, no
            # fallback loop): kimi -> 1, everyone else -> the agent's own.
            "temperature": _provider_temperature(cfg, self.temperature),
            "max_tokens": self.max_tokens,
            "stream": True,
            # Ask the provider to append a final usage chunk so cost tracking
            # still works on the streamed path (Groq honours stream_options).
            "stream_options": {"include_usage": True},
        }
        headers = (
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            if api_key
            else {"Content-Type": "application/json"}
        )
        usage: Optional[Dict[str, Any]] = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, read=120.0)) as client:
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
                            delta = (choices[0].get("delta") or {}).get("content")
                            if delta:
                                yield delta
                        if evt.get("usage"):
                            usage = evt["usage"]
        except _SynthStreamError:
            raise
        except Exception as e:  # noqa: BLE001 — network/transport/parse
            raise _SynthStreamError(f"{cfg['provider']} stream error: {e}")
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
                pass

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
            # Coerce target_count if the provider shipped it as a string ("30" → 30).
            tc_raw = args.get("target_count", 200)
            try:
                tc = int(tc_raw) if tc_raw not in (None, "") else 200
            except (TypeError, ValueError):
                tc = 200
            params = {
                "brief": args.get("brief") or "",
                "target_count": tc,
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
            if not isinstance(systems, list) or not systems:
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
        # File-consuming blocks: the LLM typically supplies just the filename
        # (e.g. 'DGII - Infra-1 - Demolition BOQ.pdf') because that is what the
        # user said. The block then calls os.path.exists on a bare filename
        # which always fails on the deployed disk, producing
        # 'File not found: <name>'. Resolve to the absolute file_path of the
        # uploaded document for this project before dispatch.
        if name in _FILE_CONSUMING_BLOCKS and project_id:
            block_input = _resolve_block_file_input(project_id, block_input)
            block_params = _resolve_block_file_input(project_id, block_params)
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


def _collect_numerics(result: Any) -> List[Dict[str, Any]]:
    """Walk a block result dict and pick out numeric payloads worth checking.

    Yields a list of ``{value, unit?, context}`` packets in the shape
    validation_pipeline.process() expects. The detection is lenient — better
    to spot-check a few extras than miss a value silently.
    """
    if not isinstance(result, dict):
        return []
    out: List[Dict[str, Any]] = []

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


async def _auto_validate(envelope: Dict[str, Any]) -> None:
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
        from app.dependencies import block_instances as _bi, _create_block_instance as _create
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

    per: List[Dict[str, Any]] = []
    overall = "pass"
    first_failure: Optional[str] = None
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
            print(f"failed to load agent {md.name}: {e}")
    return AGENT_REGISTRY


def get_agent(name: str) -> Optional[Agent]:
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

_SMART_ORCH_BLOCK_CACHE: Optional[Any] = None


def _get_smart_orchestrator_block() -> Optional[Any]:
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


async def select_agent_for_message(
    user_message: str,
    requested_agent: Agent,
) -> tuple[Agent, Dict[str, Any]]:
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
      * Top action confidence is below the routing threshold.
      * Top action is not in ``GENERATIVE_INTENTS`` (i.e. small talk / Q&A).
      * The requested agent is already ``heavy-reasoning``.
      * ``heavy-reasoning`` isn't registered in AGENT_REGISTRY.

    Each pass-through still populates ``routing_info`` with the
    classification result so the caller can observe what would have
    happened, and so the UI can show a "stayed on" badge if it wants.
    """
    info: Dict[str, Any] = {
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

    if not needs_planning(action, confidence):
        info["reason"] = "below_routing_gate"
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
        icon=config.get("icon", ""),
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
