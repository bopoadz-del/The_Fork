"""Agent memory — persistent conversations, messages, and durable agent facts.

Phase C4 — Stream C: persistent agent memory.

SQLAlchemy-backed via app.core.db — unified The Fork schema.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update

from app.core.db import SessionLocal, engine, get_database_url
from app.core.models import AgentFact, Conversation, Message

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


def _project_id_to_db(project_id: Optional[str]) -> Optional[str]:
    """Map API project_id ('' or None) to NULL in the DB."""
    if project_id is None or project_id == "":
        return None
    return project_id


def _fact_project_id_from_db(project_id: Optional[str]) -> str:
    """Map DB NULL back to '' for agent-fact API compatibility."""
    return project_id if project_id is not None else ""


def _conversation_as_dict(conversation: Conversation) -> Dict[str, Any]:
    return {
        "id": conversation.id,
        "agent_name": conversation.agent_name,
        "project_id": conversation.project_id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _message_as_dict(message: Message) -> Dict[str, Any]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
    }


def _agent_fact_as_dict(fact: AgentFact) -> Dict[str, Any]:
    return {
        "id": fact.id,
        "agent_name": fact.agent_name,
        "project_id": _fact_project_id_from_db(fact.project_id),
        "conversation_id": fact.conversation_id,
        "key": fact.key,
        "value": fact.value,
        "updated_at": fact.updated_at,
    }


def init_db() -> None:
    """Create the schema if absent. Idempotent — safe to call on every startup."""
    global _initialized
    with _lock:
        _ensure_sqlite_parent_dir()
        Conversation.__table__.create(bind=engine, checkfirst=True)
        Message.__table__.create(bind=engine, checkfirst=True)
        AgentFact.__table__.create(bind=engine, checkfirst=True)
        _initialized = True


def _ensure_db() -> None:
    if not _initialized:
        init_db()


# ── conversations ────────────────────────────────────────────────────────────

def get_or_create_conversation(
    conversation_id: str,
    agent_name: str,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the existing conversation row or create it with the given id. Idempotent."""
    _ensure_db()
    db_project_id = _project_id_to_db(project_id)
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation:
            existing = _conversation_as_dict(conversation)
            # Hygiene: backfill a NULL project_id when a real one is now known.
            # Never overwrite a non-NULL stored value (would re-tenant the row).
            if existing.get("project_id") is None and db_project_id is not None:
                with _lock:
                    now = _now()
                    session.execute(
                        update(Conversation)
                        .where(
                            Conversation.id == conversation_id,
                            Conversation.project_id.is_(None),
                        )
                        .values(project_id=db_project_id, updated_at=now)
                    )
                    session.commit()
                    session.refresh(conversation)
                return _conversation_as_dict(conversation)
            return existing

    now = _now()
    with _lock:
        with SessionLocal() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation:
                return _conversation_as_dict(conversation)
            session.add(
                Conversation(
                    id=conversation_id,
                    agent_name=agent_name,
                    project_id=db_project_id,
                    title=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            return _conversation_as_dict(
                session.get(Conversation, conversation_id)  # type: ignore[arg-type]
            )


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Return the conversation row for the given id, or None if it does not exist."""
    _ensure_db()
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
    return _conversation_as_dict(conversation) if conversation else None


def list_conversations(
    agent_name: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    _ensure_db()
    with SessionLocal() as session:
        stmt = select(Conversation).order_by(Conversation.updated_at.desc())
        if agent_name is not None:
            stmt = stmt.where(Conversation.agent_name == agent_name)
        if project_id is not None:
            stmt = stmt.where(
                Conversation.project_id == _project_id_to_db(project_id)
            )
        rows = session.scalars(stmt).all()
    return [_conversation_as_dict(c) for c in rows]


def delete_conversation(conversation_id: str) -> bool:
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            conversation = session.get(Conversation, conversation_id)
            if not conversation:
                return False
            session.delete(conversation)
            session.commit()
            return True


def clear_conversation(conversation_id: str) -> Dict[str, int]:
    """Wipe the conversation's messages and agent_facts without dropping
    the conversation row itself. Used by the UI's "Clear history" button
    to escape a thread poisoned by prior hallucinated turns while keeping
    the conversation_id stable (so the React composer doesn't need to
    remount).

    Returns ``{"messages": N, "facts": M}`` so the caller can surface
    how much was removed. Idempotent — clearing an empty / nonexistent
    conversation returns zeros without raising.
    """
    _ensure_db()
    with _lock:
        with SessionLocal() as session:
            msgs = session.execute(
                delete(Message).where(Message.conversation_id == conversation_id)
            ).rowcount
            facts = session.execute(
                delete(AgentFact).where(
                    AgentFact.conversation_id == conversation_id
                )
            ).rowcount
            session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=_now())
            )
            session.commit()
    return {"messages": int(msgs or 0), "facts": int(facts or 0)}


# ── messages ─────────────────────────────────────────────────────────────────

def append_message(conversation_id: str, role: str, content: str) -> Dict[str, Any]:
    """Insert a message and bump the conversation's updated_at."""
    _ensure_db()
    mid = str(uuid.uuid4())
    now = _now()
    with _lock:
        with SessionLocal() as session:
            session.add(
                Message(
                    id=mid,
                    conversation_id=conversation_id,
                    role=role,
                    content=content,
                    created_at=now,
                )
            )
            session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=now)
            )
            session.commit()
    with SessionLocal() as session:
        message = session.get(Message, mid)
    return _message_as_dict(message)  # type: ignore[arg-type]


def get_messages(conversation_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """Return messages oldest-first. If limit is set, return the most recent `limit` rows, still oldest-first."""
    _ensure_db()
    with SessionLocal() as session:
        rows = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        ).all()
    return [_message_as_dict(m) for m in reversed(rows)]


# ── agent facts ───────────────────────────────────────────────────────────────

def set_agent_fact(
    agent_name: str,
    key: str,
    value: str,
    conversation_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a durable fact for an agent, scoped to a project.

    Facts are keyed by (agent_name, project_id, key) so a fact remembered in
    one project is not visible to the same agent in another project. A
    project-less conversation uses the '' scope.
    """
    _ensure_db()
    api_project_id = project_id or ""
    db_project_id = _project_id_to_db(api_project_id)
    now = _now()
    with _lock:
        with SessionLocal() as session:
            stmt = select(AgentFact).where(
                AgentFact.agent_name == agent_name,
                AgentFact.key == key,
            )
            if db_project_id is None:
                stmt = stmt.where(AgentFact.project_id.is_(None))
            else:
                stmt = stmt.where(AgentFact.project_id == db_project_id)
            existing = session.scalars(stmt).one_or_none()
            if existing:
                existing.value = value
                existing.conversation_id = conversation_id
                existing.updated_at = now
            else:
                session.add(
                    AgentFact(
                        id=str(uuid.uuid4()),
                        agent_name=agent_name,
                        project_id=db_project_id,
                        conversation_id=conversation_id,
                        key=key,
                        value=value,
                        updated_at=now,
                    )
                )
            session.commit()
    with SessionLocal() as session:
        stmt = select(AgentFact).where(
            AgentFact.agent_name == agent_name,
            AgentFact.key == key,
        )
        if db_project_id is None:
            stmt = stmt.where(AgentFact.project_id.is_(None))
        else:
            stmt = stmt.where(AgentFact.project_id == db_project_id)
        fact = session.scalars(stmt).one()
    return _agent_fact_as_dict(fact)


def list_agent_facts(
    agent_name: str, project_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List an agent's facts for one project scope ('' = project-less)."""
    _ensure_db()
    db_project_id = _project_id_to_db(project_id or "")
    with SessionLocal() as session:
        stmt = (
            select(AgentFact)
            .where(AgentFact.agent_name == agent_name)
            .order_by(AgentFact.key)
        )
        if db_project_id is None:
            stmt = stmt.where(AgentFact.project_id.is_(None))
        else:
            stmt = stmt.where(AgentFact.project_id == db_project_id)
        rows = session.scalars(stmt).all()
    return [_agent_fact_as_dict(f) for f in rows]
