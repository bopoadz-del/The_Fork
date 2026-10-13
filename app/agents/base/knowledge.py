"""General knowledge as a tool (F-DRIVER Phase B): the model searches the
platform's general-knowledge library when it needs to, instead of the
library being injected into every turn. Base package: every hat has it."""
from __future__ import annotations

import re

from app.agents.core.tool_registry import ToolCall, tool

# A stored upload is "{id}_{original name}". The id is how the file was
# saved, not part of the name a person reads.
_STORED_ID_PREFIX = re.compile(r"^[0-9a-fA-F]{8,}_")
_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_YAML_TITLE = re.compile(r"(?im)^title:\s*[\"']?(.+?)[\"']?\s*$")

# What a person reads for a hit's layer. A library is the knowledge base.
# The open project's own files are project documents. Same words for every
# project.
_LAYER_LABELS = {
    "general_knowledge": "Knowledge base",
    "shared_domain": "Knowledge base",
    "knowledge_base": "Knowledge base",
    "master_corpus": "Knowledge base",
    "shared": "Knowledge base",
    "own": "Project document",
    "project": "Project document",
    "project_record": "Project document",
    "project_document": "Project document",
}


def layer_label_for(layer: str) -> str:
    """``general_knowledge`` is "Knowledge base"; ``own`` is "Project document"."""
    key = str(layer or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in _LAYER_LABELS:
        return _LAYER_LABELS[key]
    return "Project document"


def _clean_filename(name: str) -> str:
    """The file's name as words: no directory, no extension, no stored id."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = _STORED_ID_PREFIX.sub("", stem)
    stem = stem.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", stem).strip()


def _title_in_text(text: str) -> str:
    """A title the document itself states: front-matter ``title``, else the
    first markdown heading."""
    raw = str(text or "").lstrip("\ufeff")
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        block = raw[3:end] if end != -1 else ""
        found = _YAML_TITLE.search(block)
        if found and found.group(1).strip():
            return found.group(1).strip()
    found = _HEADING.search(raw)
    return found.group(1).strip() if found else ""


def _metadata_title(hit: dict) -> str:
    for key in ("title", "document_title"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    meta = hit.get("metadata")
    if isinstance(meta, dict):
        for key in ("title", "document_title"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def label_search_hit(hit: dict, *, layer: str = "") -> dict:
    """Add the words a person should read for this hit.

    ``source_label`` is the title metadata, then a title the text states,
    then the file name cleaned to words. ``layer_label`` is the layer.
    The stored ``filename`` is left as it is, so a later read can fetch it.
    """
    if not isinstance(hit, dict):
        return hit
    row = dict(hit)
    chosen = str(layer or row.get("layer") or row.get("origin") or "own").strip()
    row["layer_label"] = layer_label_for(chosen)
    title = _metadata_title(row) or _title_in_text(
        row.get("snippet") or row.get("text") or row.get("chunk") or "")
    if not title:
        title = _clean_filename(row.get("filename") or row.get("original_name") or "")
    if title:
        row["source_label"] = title
    return row

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_general_knowledge",
        "description": "Search the platform's general-knowledge library (standards, codes, "
                       "guidance and training material) -- not the project's own documents. "
                       "Cite each result by its source_label and layer_label. "
                       "filename is the stored name for a later read, not the name to show.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "top_k": {"type": "integer", "description": "How many passages (default 5)."},
            },
            "required": ["query"],
        },
    },
}


@tool("search_general_knowledge", owner="base", display_name='Knowledge base search', schema=SCHEMA)
async def handle_search_general_knowledge(call: ToolCall) -> dict:
    """Search every configured general-knowledge project; best passages first."""
    from app.core.doc_index import search_project_documents
    from app.core.rag.retriever import _general_knowledge_project_ids

    query = str(call.args.get("query") or "").strip()
    try:
        top_k = max(1, min(20, int(call.args.get("top_k") or 5)))
    except (TypeError, ValueError):
        top_k = 5
    projects = _general_knowledge_project_ids()
    if not query or not projects:
        return {"name": call.name, "ok": False, "result": {
            "status": "error",
            "error": "no query" if not query else "no general-knowledge library is configured"}}
    found = []
    for project_id in projects:
        for hit in await search_project_documents(project_id, query, top_k) or []:
            if isinstance(hit, dict):
                # Inside the library project a row's own origin is "own".
                # This tool only searches that library, so the hit is
                # knowledge base, not one of the open project's documents.
                origin = str(hit.get("origin") or "").strip()
                if origin in ("", "own"):
                    origin = "general_knowledge"
                found.append(label_search_hit(
                    {**hit, "layer": "general_knowledge", "origin": origin,
                     "project_id": project_id},
                    layer="general_knowledge",
                ))
    found.sort(key=lambda h: float(h.get("score") or 0.0), reverse=True)
    return {"name": call.name, "ok": True, "result": {"results": found[:top_k]}}
