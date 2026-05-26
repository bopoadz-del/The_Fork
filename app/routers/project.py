"""Project reasoning API — Reasoning Engine Plan 6.

POST /v1/project/ask — run the Project Reasoner over a persistent session.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import require_user
from app.core.session_store import SessionStore, get_session_store
from app.schemas.project_session import ProjectSession

router = APIRouter()

# Process-wide session store. app/main.py overwrites this at startup with the
# shared instance; tests monkeypatch it. Lazily created so importing the module
# never fails.
_store: SessionStore = get_session_store()


def get_project_store() -> SessionStore:
    """Accessor for the active session store (overridable in tests)."""
    return _store


def _reasoner_factory():
    """Build a ProjectReasonerBlock. Indirected so tests can swap in a mock."""
    from app.blocks.project_reasoner import ProjectReasonerBlock
    return ProjectReasonerBlock()


class ProjectAskRequest(BaseModel):
    session_id: str = Field(min_length=1)
    request: str
    # optional: load/replace the session's activity list on this turn
    activities: Optional[List[Dict[str, Any]]] = None
    # optional: the project whose indexed documents the reasoner should
    # consult for evidence. Defaults to session_id for back-compat with the
    # UI convention where the project id IS the session id.
    project_id: Optional[str] = None


@router.post("/v1/project/ask")
async def project_ask(
    body: ProjectAskRequest, auth: dict = Depends(require_user)
):
    """Answer a project question. Creates the session on first use, persists
    it after the turn so follow-up questions build on prior state."""
    if not body.request.strip():
        raise HTTPException(422, "request must not be empty")

    # Cap client-supplied activities to bound session-store memory growth.
    _MAX_ACTIVITIES = 5000
    if body.activities is not None and len(body.activities) > _MAX_ACTIVITIES:
        raise HTTPException(
            422,
            f"activities exceeds the maximum of {_MAX_ACTIVITIES}",
        )

    # Session ownership: create on first use tagged with caller's user_id;
    # reject access if the session exists but was created by a different user.
    caller_id = auth["user_id"]
    session = _store.get(body.session_id)
    if session is None:
        session = ProjectSession.new(body.session_id, user_id=caller_id)
        _store.save(session)
    elif session.user_id != caller_id:
        raise HTTPException(404, "Session not found")
    if body.activities is not None:
        session.data["activities"] = body.activities

    reasoner = _reasoner_factory()
    # Use the explicit project_id when given; otherwise fall back to the
    # session_id (UI convention: activeProjectId IS the session id).
    project_id = body.project_id or body.session_id
    result = await reasoner.process({"request": body.request,
                                     "session": session,
                                     "project_id": project_id})

    _store.save(session)   # persist the turn — history, computed state, cache

    return {
        "session_id": body.session_id,
        "status": result.get("status"),
        "answer": result.get("answer", ""),
        "understanding": result.get("understanding", ""),
        "plan": result.get("plan"),
        "execution": result.get("execution"),
        "artifacts": [a.model_dump() for a in session.artifacts],
        "error": result.get("error"),
    }
