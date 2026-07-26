"""Session state store — Reasoning Engine Plan 2.

Two interchangeable backends behind one interface: InMemorySessionStore
(dev/test) and RedisSessionStore (production, added in Task 4).
"""

import copy
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

from app.core.redis_client import get_redis_client
from app.schemas.project_session import ProjectSession

DEFAULT_TTL_SECONDS = 14400  # 4 hours; override with the SESSION_TTL_SECONDS env var


class SessionStore(ABC):
    """Persist and retrieve ProjectSession objects, with TTL expiry."""

    @abstractmethod
    async def get(self, session_id: str) -> Optional[ProjectSession]: ...

    @abstractmethod
    async def save(self, session: ProjectSession) -> None: ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool: ...

    async def get_or_create(self, session_id: str) -> ProjectSession:
        existing = await self.get(session_id)
        if existing is not None:
            return existing
        session = ProjectSession.new(session_id)
        await self.save(session)
        return session


class InMemorySessionStore(SessionStore):
    """Process-local dict backend. Fine for dev/test and single-process runs."""

    def __init__(self, ttl_seconds: int = 0):
        self._ttl = ttl_seconds or int(os.getenv("SESSION_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
        self._data: Dict[str, Tuple[ProjectSession, float]] = {}

    async def get(self, session_id: str) -> Optional[ProjectSession]:
        entry = self._data.get(session_id)
        if entry is None:
            return None
        session, expires_at = entry
        if time.time() > expires_at:
            del self._data[session_id]
            return None
        return copy.deepcopy(session)

    async def save(self, session: ProjectSession) -> None:
        session.touch()
        self._data[session.id] = (session, time.time() + self._ttl)

    async def delete(self, session_id: str) -> bool:
        return self._data.pop(session_id, None) is not None


class RedisSessionStore(SessionStore):
    """Redis backend. Sessions are JSON blobs keyed `session:<id>` with a TTL.

    Requires the `redis` package and a reachable server. Only instantiated
    when REDIS_URL is set — see get_session_store().
    """

    _PREFIX = "session:"

    def __init__(self, ttl_seconds: int = 0):
        self._ttl = ttl_seconds or int(os.getenv("SESSION_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))

    def _key(self, session_id: str) -> str:
        return f"{self._PREFIX}{session_id}"

    async def get(self, session_id: str) -> Optional[ProjectSession]:
        client = await get_redis_client()
        if client is None:
            return None
        try:
            raw = await client.get(self._key(session_id))
            if raw is None:
                return None
            return ProjectSession.model_validate_json(raw)
        except Exception:
            return None

    async def save(self, session: ProjectSession) -> None:
        client = await get_redis_client()
        if client is None:
            return
        try:
            session.touch()
            await client.set(self._key(session.id), session.model_dump_json(), ex=self._ttl)
        except Exception:
            pass

    async def delete(self, session_id: str) -> bool:
        client = await get_redis_client()
        if client is None:
            return False
        try:
            return bool(await client.delete(self._key(session_id)))
        except Exception:
            return False


def get_session_store() -> SessionStore:
    """Pick a backend: Redis when REDIS_URL is set and reachable, else
    in-memory. Falls back to in-memory if Redis cannot be reached."""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis as _redis
            _redis.from_url(redis_url).ping()
            return RedisSessionStore()
        except Exception as exc:
            import warnings
            warnings.warn(
                f"Redis unavailable ({exc!r}); falling back to in-memory session store.",
                RuntimeWarning, stacklevel=2,
            )
    return InMemorySessionStore()
