"""Saved workflows — Roadmap V2 · Epic 7 (power-user chaining).

The chain mechanism already works but was JSON/dev-only. This makes chains
first-class: name them, save them, re-run them. SQLAlchemy-backed (shares the
unified The Fork DB), scoped to an owner and optionally to a project.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select

from app.core.db import SessionLocal, engine, get_database_url
from app.core.models import Workflow

_lock = threading.Lock()
_initialized = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_sqlite_parent_dir() -> None:
    url = get_database_url()
    if url.startswith("sqlite:///"):
        parent = os.path.dirname(url[len("sqlite:///") :])
        if parent:
            os.makedirs(parent, exist_ok=True)


def _workflow_as_dict(workflow: Workflow) -> Dict[str, Any]:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "project_id": workflow.project_id,
        "owner_id": workflow.owner_id,
        "steps": workflow.steps,
        "created_at": workflow.created_at,
    }


def init_db() -> None:
    global _initialized
    with _lock:
        from app.core.projects import init_db as init_projects_db

        init_projects_db()
        _ensure_sqlite_parent_dir()
        Workflow.__table__.create(bind=engine, checkfirst=True)
        _initialized = True


def _ensure() -> None:
    if not _initialized:
        init_db()


def save_workflow(
    name: str,
    steps: List[Dict[str, Any]],
    project_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure()
    wid = str(uuid.uuid4())[:8]
    with _lock:
        with SessionLocal() as session:
            session.add(
                Workflow(
                    id=wid,
                    name=name,
                    project_id=project_id,
                    owner_id=owner_id,
                    steps=steps,
                    created_at=_now(),
                )
            )
            session.commit()
    return get_workflow(wid, owner_id=owner_id)


def get_workflow(
    workflow_id: str, owner_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Fetch a workflow. When ``owner_id`` is given, a workflow owned by a
    different user is treated as not found (tenant isolation)."""
    _ensure()
    with SessionLocal() as session:
        workflow = session.get(Workflow, workflow_id)
        if not workflow:
            return None
        if owner_id is not None and workflow.owner_id != owner_id:
            return None
        return _workflow_as_dict(workflow)


def list_workflows(
    project_id: Optional[str] = None, owner_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List workflows. When ``owner_id`` is given, only that owner's
    workflows are returned."""
    _ensure()
    with SessionLocal() as session:
        stmt = select(Workflow).order_by(Workflow.created_at.desc())
        if owner_id is not None:
            stmt = stmt.where(Workflow.owner_id == owner_id)
        if project_id:
            stmt = stmt.where(Workflow.project_id == project_id)
        rows = session.scalars(stmt).all()
    return [_workflow_as_dict(w) for w in rows]


def delete_workflow(workflow_id: str, owner_id: Optional[str] = None) -> bool:
    """Delete a workflow. When ``owner_id`` is given, a workflow owned by a
    different user is not deleted (returns False)."""
    _ensure()
    with _lock:
        with SessionLocal() as session:
            stmt = delete(Workflow).where(Workflow.id == workflow_id)
            if owner_id is not None:
                stmt = stmt.where(Workflow.owner_id == owner_id)
            result = session.execute(stmt)
            session.commit()
            return result.rowcount > 0
