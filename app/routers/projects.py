"""Project API — create projects, attach documents, gated progress tracking.

Roadmap V2 · Part 0:
  0.1  Project entity
  0.2  Readiness gate — progress tracking refuses to run on an unready project
  0.3  Execution-intent model — attaching a document stores it and runs NOTHING;
       analysis happens only when explicitly requested.
"""

import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core import audit, doc_index, file_crypto, projects as store
from app.blocks import BLOCK_REGISTRY
from app.dependencies import (
    require_user,
    block_instances,
    _create_block_instance,
)

router = APIRouter()

DATA_DIR = os.getenv("DATA_DIR", "./data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except PermissionError:
    import tempfile
    DATA_DIR = tempfile.gettempdir()

ALLOWED_DOC_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff",
    ".txt", ".md", ".csv", ".json", ".xml",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Construction-domain formats the registered blocks know how to parse.
    # ezdxf reads .dxf; ifcopenshell reads .ifc; xer/mpp are schedule exports;
    # .dwg is kept even though drawing_qto rejects it with a "convert to DXF"
    # message so the upload doesn't 400 before the user sees that guidance.
    ".dxf", ".dwg", ".ifc", ".xer", ".mpp", ".rvt",
}


def _owned_or_404(project_id: str, user_id: str):
    """Load a project the caller owns, or 404 (never leak existence)."""
    proj = store.get_project(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(404, f"Project '{project_id}' not found")
    return proj


# ── request models ──────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    name: str
    client: Optional[str] = None


class ConnectorRequest(BaseModel):
    connected: bool = True


class ProgressRequest(BaseModel):
    planned_percent: float = 0
    actual_percent: float = 0
    contract_value: float = 0
    reporting_period: Optional[str] = None
    activities: List[Dict[str, Any]] = []
    photos: List[str] = []


# ── projects ────────────────────────────────────────────────────────────────

@router.post("/v1/projects", status_code=201)
async def create_project(req: CreateProjectRequest, auth: dict = Depends(require_user)):
    """Create a project. Documents and analytics hang off this entity."""
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "Project name is required")
    proj = store.create_project(name, (req.client or "").strip() or None, user_id=auth["user_id"])
    audit.record("project.created", project_id=proj["id"], name=name, user_id=auth["user_id"])
    return proj


@router.get("/v1/projects")
async def list_projects(auth: dict = Depends(require_user)):
    """List all projects with their readiness state."""
    return {"projects": store.list_projects(user_id=auth["user_id"])}


@router.get("/v1/projects/{project_id}")
async def get_project(project_id: str, auth: dict = Depends(require_user)):
    """Project detail — documents + the computed readiness gate."""
    proj = _owned_or_404(project_id, auth["user_id"])
    return proj


@router.delete("/v1/projects/{project_id}")
async def delete_project(project_id: str, auth: dict = Depends(require_user)):
    """Delete a project: its document records, facts, AND files on disk."""
    proj = _owned_or_404(project_id, auth["user_id"])
    files_purged = 0
    for doc in proj.get("documents", []):
        fp = doc.get("file_path")
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
                files_purged += 1
            except OSError:
                pass
    store.delete_project(project_id)  # cascades documents + facts
    audit.record("project.deleted", project_id=project_id,
                 files_purged=files_purged, user_id=auth["user_id"])
    return {
        "status": "deleted",
        "project_id": project_id,
        "files_purged": files_purged,
    }


@router.post("/v1/projects/{project_id}/conversations/{conversation_id}/clear")
async def clear_project_conversation(
    project_id: str,
    conversation_id: str,
    auth: dict = Depends(require_user),
):
    """Wipe one conversation's messages + facts without deleting the
    conversation row. Lets the operator escape a thread poisoned by
    prior hallucinated tool-skip / fabricated-table turns without
    creating a new project.

    Owner-only. Cross-project conversation IDs are rejected with 404 to
    avoid info-leak via timing.
    """
    _owned_or_404(project_id, auth["user_id"])
    from app.core import agent_memory

    # Workspace conversation IDs are deterministic (ws-{project_id}).
    # Reject any other workspace prefix that doesn't match this project
    # before we let the call near agent_memory.
    if conversation_id.startswith("ws-") and conversation_id != f"ws-{project_id}":
        raise HTTPException(404, "Conversation not found")

    # For non-workspace conversation IDs, confirm the stored row (if any)
    # belongs to this project.
    conv = agent_memory.get_conversation(conversation_id)
    if conv is not None and conv.get("project_id") not in (None, "", project_id):
        raise HTTPException(404, "Conversation not found")

    cleared = agent_memory.clear_conversation(conversation_id)
    audit.record(
        "conversation.cleared",
        project_id=project_id,
        conversation_id=conversation_id,
        user_id=auth["user_id"],
        messages=cleared["messages"],
        facts=cleared["facts"],
    )
    return {
        "status": "cleared",
        "conversation_id": conversation_id,
        **cleared,
    }


@router.post("/v1/projects/{project_id}/connectors/aconex")
async def connect_aconex(
    project_id: str, req: ConnectorRequest, auth: dict = Depends(require_user)
):
    """Set the Aconex connection flag for a project.

    Stub for the full Oracle Aconex connector (Roadmap V2 cross-cutting item).
    Until the real OAuth/import client lands, this lets the readiness gate be
    satisfied explicitly.
    """
    _owned_or_404(project_id, auth["user_id"])
    if not store.set_aconex(project_id, req.connected):
        raise HTTPException(404, f"Project '{project_id}' not found")
    audit.record("connector.aconex", project_id=project_id,
                 connected=req.connected, user_id=auth["user_id"])
    return {
        "status": "ok",
        "project_id": project_id,
        "aconex_connected": req.connected,
        "readiness": store.compute_readiness(project_id),
    }


@router.get("/v1/projects/{project_id}/connectors")
async def list_connectors(project_id: str, auth: dict = Depends(require_user)):
    """Connector status for a project.

    Aconex is currently a connection *flag* — the full Oracle Aconex OAuth
    client is pending API credentials (Roadmap V2 open question). Setting the
    flag lets the readiness gate be satisfied for projects whose live Aconex
    feed is managed outside the platform.
    """
    proj = _owned_or_404(project_id, auth["user_id"])
    return {
        "project_id": project_id,
        "connectors": [
            {
                "name": "aconex",
                "connected": proj["aconex_connected"],
                "mode": "flag",
                "note": "Full OAuth client pending Aconex API credentials.",
            }
        ],
    }


# ── documents — store only, no analysis (Roadmap V2 · 0.3) ──────────────────

@router.post("/v1/projects/{project_id}/documents", status_code=201)
async def add_document(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    role: Optional[str] = Form(None),
    auth: dict = Depends(require_user),
):
    """Attach a document to a project.

    This STORES and CLASSIFIES the file — it runs no pipeline, no blocks, no
    analysis. Attaching a file is not the same as asking for analysis; run a
    block explicitly (via /v1/execute) when you actually want results.
    """
    proj = _owned_or_404(project_id, auth["user_id"])

    original_name = (file.filename or "unknown").strip()
    if not original_name or original_name in (".", ".."):
        raise HTTPException(400, "Invalid filename")
    original_name = os.path.basename(original_name.replace("\\", "/"))
    _, ext = os.path.splitext(original_name.lower())
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(400, f"File type '{ext}' not allowed")

    file_id = str(uuid.uuid4())[:8]
    stored_as = f"{file_id}_{original_name}"
    filepath = os.path.join(DATA_DIR, stored_as)
    # Persist the document — encrypted at rest iff DATA_ENCRYPTION_KEY is set
    # (opt-in; plaintext otherwise — see app/core/file_crypto.py). The recorded
    # `size` is the original plaintext size, not the (larger) ciphertext size.
    file.file.seek(0)
    raw_bytes = file.file.read()
    file_crypto.write_document(filepath, raw_bytes)
    size = len(raw_bytes)

    if role is not None and role not in store.VALID_ROLES:
        raise HTTPException(
            400, f"Invalid role '{role}'. Allowed: {sorted(store.VALID_ROLES)}"
        )

    doc = store.add_document(
        project_id, original_name, stored_as, filepath, size, role=role
    )
    audit.record("document.added", project_id=project_id,
                 document_id=doc["id"], name=original_name, size=size, user_id=auth["user_id"])
    background_tasks.add_task(doc_index.maybe_eager_index, project_id, doc["id"])
    return {
        "status": "stored",
        "message": (
            f"Added '{original_name}' — classified as {doc['doc_type']} "
            f"(role: {doc['doc_role']}). No analysis was run; ask in chat to "
            f"analyze it."
        ),
        "document": doc,
        "readiness": store.compute_readiness(project_id),
    }


# ── gated progress tracking (Roadmap V2 · 0.2) ──────────────────────────────

@router.post("/v1/projects/{project_id}/progress")
async def project_progress(
    project_id: str, req: ProgressRequest, auth: dict = Depends(require_user)
):
    """Run the progress tracker for a project — but only if the project is ready.

    Until the project has a baseline schedule, daily and weekly reports, and
    Aconex connected, this returns a structured 'not_ready' response naming
    exactly what is missing — never fabricated all-zero numbers.
    """
    proj = _owned_or_404(project_id, auth["user_id"])

    readiness = proj["readiness"]
    if not readiness["ready"]:
        return {
            "status": "not_ready",
            "project_id": project_id,
            "message": (
                "Project is not ready for progress tracking. "
                "Load the missing items, then try again."
            ),
            "missing": readiness["missing"],
            "readiness": readiness,
        }

    container = block_instances.get("construction")
    if container is None:
        container = _create_block_instance(BLOCK_REGISTRY["construction"])
        block_instances["construction"] = container

    params = req.model_dump()
    if not params.get("reporting_period"):
        params.pop("reporting_period", None)

    tracker = await container.progress_tracker({}, params)
    return {
        "status": "success",
        "project_id": project_id,
        "readiness": readiness,
        "tracker": tracker,
    }


# ── project memory (Roadmap V2 · Epic 3) ────────────────────────────────────

class FactRequest(BaseModel):
    key: str
    value: str
    source_document: Optional[str] = None
    confidence: Optional[float] = None


@router.get("/v1/projects/{project_id}/memory")
async def get_memory(
    project_id: str, q: Optional[str] = None, auth: dict = Depends(require_user)
):
    """List the durable facts known about a project (optionally keyword-filtered)."""
    _owned_or_404(project_id, auth["user_id"])
    facts = store.search_facts(project_id, q) if q else store.list_facts(project_id)
    return {"project_id": project_id, "facts": facts, "count": len(facts)}


@router.post("/v1/projects/{project_id}/memory", status_code=201)
async def add_memory(
    project_id: str, req: FactRequest, auth: dict = Depends(require_user)
):
    """Add or correct a project fact (manual entry / correction)."""
    _owned_or_404(project_id, auth["user_id"])
    key = (req.key or "").strip()
    if not key:
        raise HTTPException(400, "Fact key is required")
    return store.set_fact(
        project_id, key, req.value,
        source_document=req.source_document, confidence=req.confidence,
    )


@router.delete("/v1/projects/{project_id}/memory/{key}")
async def delete_memory(
    project_id: str, key: str, auth: dict = Depends(require_user)
):
    """Forget a single project fact."""
    _owned_or_404(project_id, auth["user_id"])
    if not store.delete_fact(project_id, key):
        raise HTTPException(404, f"Fact '{key}' not found for project '{project_id}'")
    return {"status": "deleted", "project_id": project_id, "key": key}


# ── data governance (Roadmap V2 · Epic 6) ───────────────────────────────────

@router.delete("/v1/projects/{project_id}/documents/{document_id}")
async def delete_document(
    project_id: str, document_id: str, auth: dict = Depends(require_user)
):
    """Delete a single document — its record and its file on disk."""
    _owned_or_404(project_id, auth["user_id"])
    doc = store.get_document(document_id)
    if not doc or doc.get("project_id") != project_id:
        raise HTTPException(
            404, f"Document '{document_id}' not found in project '{project_id}'"
        )
    fp = doc.get("file_path")
    file_removed = False
    if fp and os.path.exists(fp):
        try:
            os.remove(fp)
            file_removed = True
        except OSError:
            pass
    store.delete_document(document_id)
    audit.record("document.deleted", project_id=project_id,
                 document_id=document_id, file_removed=file_removed, user_id=auth["user_id"])
    return {
        "status": "deleted",
        "document_id": document_id,
        "file_removed": file_removed,
    }


@router.get("/v1/projects/{project_id}/audit")
async def project_audit(
    project_id: str, limit: int = 100, auth: dict = Depends(require_user)
):
    """The audit trail for a project — uploads, deletions, purges."""
    _owned_or_404(project_id, auth["user_id"])
    return {
        "project_id": project_id,
        "entries": audit.read_audit(limit, project_id),
    }


@router.get("/v1/governance")
async def governance_status(auth: dict = Depends(require_user)):
    """Where client data lives and how long it is kept (Roadmap V2 · Epic 6)."""
    if auth["role"] != "admin":
        raise HTTPException(403, "Admin only")
    retention = int(os.getenv("DATA_RETENTION_DAYS", "0") or "0")
    return {
        "data_directory": os.getenv("DATA_DIR", "./data"),
        "retention_days": retention if retention > 0 else "indefinite",
        "audit_logging": True,
        "delete_on_request": True,
        "policy_document": "DATA_GOVERNANCE.md",
    }


@router.post("/v1/governance/purge")
async def governance_purge(auth: dict = Depends(require_user)):
    """Purge documents older than DATA_RETENTION_DAYS (no-op if unset)."""
    if auth["role"] != "admin":
        raise HTTPException(403, "Admin only")
    days = int(os.getenv("DATA_RETENTION_DAYS", "0") or "0")
    if days <= 0:
        return {"status": "skipped",
                "reason": "DATA_RETENTION_DAYS is not set", "purged": 0}
    purged = store.purge_documents_older_than(days)
    files_removed = 0
    for doc in purged:
        fp = doc.get("file_path")
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
                files_removed += 1
            except OSError:
                pass
    audit.record("governance.purge",
                 documents_purged=len(purged), files_removed=files_removed, user_id=auth["user_id"])
    return {
        "status": "purged",
        "documents_purged": len(purged),
        "files_removed": files_removed,
    }
