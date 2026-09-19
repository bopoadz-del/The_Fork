"""Admin-only blocks that execute arbitrary code.

Shared by /v1/execute, /v1/chain, the orchestrator, and MCP call_tool so the
403 gate cannot drift across surfaces.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Iterable, Optional

from fastapi import HTTPException

PRIVILEGED_BLOCKS = frozenset({"code", "sandbox"})

_caller_role: ContextVar[Optional[str]] = ContextVar("caller_role", default=None)


def set_caller_role(role: Optional[str]) -> None:
    _caller_role.set(role)


def caller_role() -> Optional[str]:
    return _caller_role.get()


def privileged_forbidden_detail(block_name: str) -> str:
    return f"Block '{block_name}' executes arbitrary code and is admin-only."


def raise_if_privileged_block(block_name: Optional[str], role: Optional[str] = None) -> None:
    if not block_name or block_name not in PRIVILEGED_BLOCKS:
        return
    effective = role if role is not None else _caller_role.get()
    if effective != "admin":
        raise HTTPException(403, privileged_forbidden_detail(block_name))


def raise_if_privileged_steps(steps: Optional[Iterable[Any]], role: Optional[str] = None) -> None:
    for step in steps or []:
        if isinstance(step, dict):
            name = step.get("block")
        else:
            name = getattr(step, "block", None)
        raise_if_privileged_block(name, role)


def collect_project_ids(obj: Any, found: Optional[set] = None) -> set:
    """Walk a request blob and collect nested ``project_id`` strings."""
    if found is None:
        found = set()
    if isinstance(obj, dict):
        pid = obj.get("project_id")
        if isinstance(pid, str) and pid.strip():
            found.add(pid.strip())
        for value in obj.values():
            collect_project_ids(value, found)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            collect_project_ids(value, found)
    return found


def raise_if_inaccessible_project_ids(auth: Optional[dict], *blobs: Any) -> None:
    """404 when any nested project_id is not readable by this caller.

    Same grant as chat/RAG (``get_project_accessible``): owner, approved
    shared, master_corpus. A miss is 404 so existence is not leaked.
    """
    from app.core import projects as projects_store

    uid = (auth or {}).get("user_id")
    for blob in blobs:
        for pid in collect_project_ids(blob):
            if projects_store.get_project_accessible(pid, uid) is None:
                raise HTTPException(404, "Project not found")


def collect_document_ids(obj: Any, found: Optional[set] = None) -> set:
    """Walk a request blob and collect nested ``document_id`` / ``document_ids``."""
    if found is None:
        found = set()
    if isinstance(obj, dict):
        did = obj.get("document_id")
        if isinstance(did, str) and did.strip():
            found.add(did.strip())
        ids = obj.get("document_ids")
        if isinstance(ids, (list, tuple)):
            for item in ids:
                if isinstance(item, str) and item.strip():
                    found.add(item.strip())
        for value in obj.values():
            collect_document_ids(value, found)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            collect_document_ids(value, found)
    return found


def raise_if_inaccessible_document_ids(auth: Optional[dict], *blobs: Any) -> None:
    """404 when any nested document_id is not on a project this caller can read.

    Missing and foreign ids use the same 404 so existence is not leaked.
    """
    from app.core import projects as projects_store

    uid = (auth or {}).get("user_id")
    for blob in blobs:
        for did in collect_document_ids(blob):
            doc = projects_store.get_document(did)
            if doc is None:
                raise HTTPException(404, "document not found")
            pid = doc.get("project_id")
            if projects_store.get_project_accessible(pid, uid) is None:
                raise HTTPException(404, "document not found")
