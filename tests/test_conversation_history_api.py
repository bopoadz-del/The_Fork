"""Tests for GET /v1/agents/conversations/{conversation_id}/messages.

Stream D Part 1 — expose persisted chat history for the workspace.

Uses TestClient(app) with a real JWT and a monkeypatched Agent._call_llm so
no network calls are made.  DATA_DIR is NOT isolated (same pattern as
test_agents_router_c6.py) because the HTTP layer needs the real users/projects
tables; uuid suffixes prevent cross-run collisions.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.agents.runtime import Agent

# ── module-level run-id ────────────────────────────────────────────────────────

_RUN = uuid.uuid4().hex[:8]


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ── helpers ────────────────────────────────────────────────────────────────────

def _register_and_login(client, suffix: str) -> dict:
    email = f"histapi-{suffix}-{_RUN}@x.com"
    client.post("/v1/users/register", json={"email": email, "password": "password12"})
    token = client.post(
        "/v1/users/login", json={"email": email, "password": "password12"}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _fake_llm(text: str = "LLM reply"):
    """Return a scripted _call_llm that resolves with no tool calls."""
    async def _inner(self, messages, api_key, project_id=None):
        return {
            "status": "success",
            "choice": {"message": {"content": text, "tool_calls": []}},
            "raw": {},
        }
    return _inner


# ── tests ──────────────────────────────────────────────────────────────────────

def test_history_empty_for_new_conversation(client):
    """GET a conversation_id that has never been used returns 200 + empty list."""
    headers = _register_and_login(client, "empty")
    cid = f"ws-never-used-{_RUN}"

    resp = client.get(f"/v1/agents/conversations/{cid}/messages", headers=headers)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["conversation_id"] == cid
    assert data["messages"] == []


def test_history_returns_persisted_turns(client, monkeypatch):
    """After a POST chat, GET should return the user + assistant turns oldest-first."""
    monkeypatch.setattr(Agent, "_call_llm", _fake_llm("mocked answer"))
    headers = _register_and_login(client, "persist")

    # Create a project so the workspace conversation has an owner
    proj_resp = client.post(
        "/v1/projects", json={"name": f"HistTest-{_RUN}"}, headers=headers
    )
    assert proj_resp.status_code in (200, 201), proj_resp.text
    pid = proj_resp.json()["id"]
    cid = f"ws-{pid}"

    # Send a chat message via the /chat endpoint (not streaming) so the turn is persisted
    chat_resp = client.post(
        "/v1/agents/project-assistant/chat",
        json={
            "message": "hello history",
            "project_id": pid,
            "conversation_id": cid,
        },
        headers=headers,
    )
    assert chat_resp.status_code == 200, chat_resp.text

    # GET the history
    hist_resp = client.get(f"/v1/agents/conversations/{cid}/messages", headers=headers)
    assert hist_resp.status_code == 200, hist_resp.text
    data = hist_resp.json()
    assert data["conversation_id"] == cid

    msgs = data["messages"]
    assert len(msgs) >= 2, f"Expected at least 2 messages, got: {msgs}"

    roles = [m["role"] for m in msgs]
    # Oldest-first: user comes before assistant
    assert roles[0] == "user"
    assert roles[-1] == "assistant"

    # Content check
    contents = [m["content"] for m in msgs]
    assert "hello history" in contents
    assert "mocked answer" in contents


def test_history_cross_tenant_404(client, monkeypatch):
    """User B cannot read User A's workspace conversation — should get 404."""
    monkeypatch.setattr(Agent, "_call_llm", _fake_llm("tenant reply"))

    headers_a = _register_and_login(client, "tenantA")
    headers_b = _register_and_login(client, "tenantB")

    # User A creates a project and chats, creating ws-{pid} tied to A's project_id
    proj_resp = client.post(
        "/v1/projects", json={"name": f"A-Project-{_RUN}"}, headers=headers_a
    )
    assert proj_resp.status_code in (200, 201), proj_resp.text
    pid = proj_resp.json()["id"]
    cid = f"ws-{pid}"

    chat_resp = client.post(
        "/v1/agents/project-assistant/chat",
        json={
            "message": "secret message",
            "project_id": pid,
            "conversation_id": cid,
        },
        headers=headers_a,
    )
    assert chat_resp.status_code == 200, chat_resp.text

    # Verify the conversation row has project_id set (sanity-check our assumption)
    from app.core import agent_memory as am
    conv = am.get_conversation(cid)
    assert conv is not None, "Conversation row should exist after chat"
    assert conv["project_id"] == pid, (
        f"Expected project_id={pid!r}, got {conv['project_id']!r}"
    )

    # User B tries to GET User A's conversation — must be 404
    resp_b = client.get(f"/v1/agents/conversations/{cid}/messages", headers=headers_b)
    assert resp_b.status_code == 404, (
        f"Expected 404 for cross-tenant access, got {resp_b.status_code}: {resp_b.text}"
    )
