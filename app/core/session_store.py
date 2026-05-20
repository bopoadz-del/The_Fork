"""Session state store — Reasoning Engine Plan 2.

Two interchangeable backends behind one interface: InMemorySessionStore
(dev/test) and RedisSessionStore (production, added in Task 4).
"""

import os
import time
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

from app.schemas.project_session import ProjectSession

DEFAULT_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "14400"))  # 4 hours


class SessionStore(ABC):
    """Persist and retrieve ProjectSession objects, with TTL expiry."""

    @abstractmethod
    def get(self, session_id: str) -> Optional[ProjectSession]: ...

    @abstractmethod
    def save(self, session: ProjectSession) -> None: ...

    @abstractmethod
    def delete(self, session_id: str) -> bool: ...

    def get_or_create(self, session_id: str) -> ProjectSession:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        session = ProjectSession.new(session_id)
        self.save(session)
        return session


class InMemorySessionStore(SessionStore):
    """Process-local dict backend. Fine for dev/test and single-process runs."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._data: Dict[str, Tuple[ProjectSession, float]] = {}

    def get(self, session_id: str) -> Optional[ProjectSession]:
        entry = self._data.get(session_id)
        if entry is None:
            return None
        session, expires_at = entry
        if time.time() > expires_at:
            del self._data[session_id]
            return None
        return session

    def save(self, session: ProjectSession) -> None:
        session.touch()
        self._data[session.id] = (session, time.time() + self._ttl)

    def delete(self, session_id: str) -> bool:
        return self._data.pop(session_id, None) is not None


class RedisSessionStore(SessionStore):
    """Redis backend. Sessions are JSON blobs keyed `session:<id>` with a TTL.

    Requires the `redis` package and a reachable server. Only instantiated
    when REDIS_URL is set — see get_session_store().
    """

    _PREFIX = "session:"

    def __init__(self, redis_url: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        import redis  # imported lazily so the dependency is optional
        self._ttl = ttl_seconds
        self._client = redis.from_url(redis_url, decode_responses=True)

    def _key(self, session_id: str) -> str:
        return f"{self._PREFIX}{session_id}"

    def get(self, session_id: str) -> Optional[ProjectSession]:
        raw = self._client.get(self._key(session_id))
        if raw is None:
            return None
        return ProjectSession.model_validate_json(raw)

    def save(self, session: ProjectSession) -> None:
        session.touch()
        self._client.set(
            self._key(session.id), session.model_dump_json(), ex=self._ttl
        )

    def delete(self, session_id: str) -> bool:
        return self._client.delete(self._key(session_id)) > 0


def get_session_store() -> SessionStore:
    """Pick a backend: Redis when REDIS_URL is set and reachable, else
    in-memory. Falls back to in-memory if Redis cannot be reached."""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            store = RedisSessionStore(redis_url)
            store._client.ping()
            return store
        except Exception:
            pass  # fall through to in-memory
    return InMemorySessionStore()
