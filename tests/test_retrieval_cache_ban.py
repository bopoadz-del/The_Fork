"""BAN test: chat and retrieval endpoints never touch cache_wrapper.

Task 8 (P4 PIN) proves that the /v1/chat and /v1/project/ask code paths do
not call cache_get/cache_set/cache_delete from app.core.cache_wrapper. The
wrapper is patched with recording mocks and the endpoints are exercised; the
mocks must remain empty.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core import cache_wrapper
from app.dependencies import block_instances, require_api_key, require_user
from app.main import app
from app.routers import project as project_router
from app.core.session_store import InMemorySessionStore


class _RecordingMock:
    """Async callable that records every invocation."""

    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


class _FakeChatBlock:
    """Deterministic chat block so the test never leaves the machine."""

    async def execute(self, message, params=None):
        return {"result": {"text": "fake chat", "model": "fake", "provider": "fake"}}


class _FakeReasoner:
    """Deterministic project reasoner so no LLM calls are made."""

    async def process(self, input_data, params=None):
        return {
            "status": "success",
            "answer": "fake answer",
            "understanding": "fake understanding",
        }


@asynccontextmanager
async def _noop_lifespan(app):
    """Skip the real lifespan so the test does not pay block/agent startup costs."""
    yield


@pytest.fixture
def client(monkeypatch):
    """Minimal client: no slow lifespan, auth bypassed, LLM blocks mocked."""
    # Keep chat fast and deterministic; do not depend on offline-template fallbacks
    # that still instantiate the smart orchestrator.
    block_instances["chat"] = _FakeChatBlock()

    # /v1/project/ask needs a session store; the noop lifespan skips the lifespan
    # setup in app.main, so inject one directly.
    store = InMemorySessionStore()
    monkeypatch.setattr(project_router, "_store", store)
    monkeypatch.setattr(project_router, "_reasoner_factory", lambda: _FakeReasoner())

    # Bypass both auth gates that the endpoints under test may use.
    fake_auth = {
        "user_id": "cache-ban-user",
        "role": "admin",
        "email": "cache-ban@example.com",
        "tier": "admin",
        "valid": True,
    }
    app.dependency_overrides[require_user] = lambda: fake_auth
    app.dependency_overrides[require_api_key] = lambda: fake_auth

    # Replace the app lifespan with a no-op to avoid slow block/agent warmup.
    monkeypatch.setattr(app.router, "lifespan_context", _noop_lifespan)

    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        block_instances.pop("chat", None)


@pytest.mark.asyncio
async def test_chat_and_retrieval_never_cache(client):
    set_mock = _RecordingMock()
    get_mock = _RecordingMock()
    del_mock = _RecordingMock()

    with patch.object(cache_wrapper, "cache_set", set_mock), \
         patch.object(cache_wrapper, "cache_get", get_mock), \
         patch.object(cache_wrapper, "cache_delete", del_mock):
        # Chat completion path
        resp = client.post("/v1/chat", json={"message": "hello"})
        assert resp.status_code in (200, 202), resp.text

        # Project retrieval/reasoning path
        resp = client.post(
            "/v1/project/ask",
            json={"session_id": "cache-ban-session", "request": "hello"},
        )
        assert resp.status_code in (200, 202), resp.text

    assert not set_mock.calls, "cache_set was called"
    assert not get_mock.calls, "cache_get was called"
    assert not del_mock.calls, "cache_delete was called"
