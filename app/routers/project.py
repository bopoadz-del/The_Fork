"""Project reasoning API — Reasoning Engine Plan 6.

POST /v1/project/ask — run the Project Reasoner over a persistent session.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import require_api_key
from app.core.session_store import SessionStore, get_session_store

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


@router.post("/v1/project/ask")
async def project_ask(
    body: ProjectAskRequest, auth: dict = Depends(require_api_key)
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

    session = _store.get_or_create(body.session_id)
    if body.activities is not None:
        session.data["activities"] = body.activities

    reasoner = _reasoner_factory()
    result = await reasoner.process({"request": body.request,
                                     "session": session})

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
