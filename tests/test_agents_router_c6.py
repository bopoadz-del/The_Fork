"""Phase C6 router tests — project/conversation wiring, delegation, reasoning toolkits.

Uses TestClient(app) with a real JWT (registered user) and a monkeypatched
Agent._call_llm so no network calls are made.
"""

import importlib
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.agents.runtime import Agent, load_agents, AGENT_REGISTRY

# ── Helpers ───────────────────────────────────────────────────────────────────

_RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _stub_llm_key(monkeypatch):
    """Agent.chat() guards on DEEPSEEK_API_KEY *before* _call_llm is reached.

    These tests monkeypatch _call_llm so no network call ever happens, but the
    guard still fires when the key is unset (e.g. in CI, where conftest's
    load_dotenv finds no .env key).  A placeholder satisfies the guard.

    Deliberately a per-file fixture, not in conftest.py: a global key would
    un-skip the live DEEPSEEK acceptance tests (their skipif is evaluated at
    collection time) and make them run against the real API with a fake key.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")


def _register_and_login(client, suffix):
    email = f"c6-{suffix}-{_RUN}@x.com"
    client.post("/v1/users/register", json={"email": email, "password": "password12"})
    token = client.post("/v1/users/login", json={"email": email, "password": "password12"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _llm_ok(text="Hello from the mock LLM."):
    """Scripted _call_llm that returns a clean final answer (no tool calls)."""
    async def fake(self, messages, api_key, project_id=None, **kwargs):
        return {
            "status": "success",
            "choice": {"message": {"content": text, "tool_calls": []}},
            "raw": {},
        }
    return fake


def _isolate_memory(tmp_path, monkeypatch):
    """Redirect DATA_DIR to a temp dir and reload only memory-related stores.

    Note: This redirects ALL SQLite stores (users, projects, agent_memory) to
    tmp_path.  For HTTP-level tests that call /v1/users/register and
    /v1/projects we must NOT call this, because those endpoints rely on the
    users/projects tables being pre-initialised.  Use it only for pure-Python
    unit tests that never hit the HTTP layer.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.core.agent_memory as _am
    import app.core.projects as _proj
    import app.core.project_memory as _pm
    import app.core.users as _users
    importlib.reload(_proj)
    importlib.reload(_pm)
    importlib.reload(_am)
    importlib.reload(_users)
    return _am


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_agent_chat_basic(client, monkeypatch):
    """POST /v1/agents/smart-orchestrator/chat with just a message returns 200."""
    monkeypatch.setattr(Agent, "_call_llm", _llm_ok())
    headers = _register_and_login(client, "basic")

    resp = client.post(
        "/v1/agents/smart-orchestrator/chat",
        json={"message": "hi"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert "answer" in data


def test_agent_chat_accepts_project_id_for_owned_project(client, monkeypatch):
    """POST with project_id owned by caller returns 200."""
    monkeypatch.setattr(Agent, "_call_llm", _llm_ok())
    headers = _register_and_login(client, "owner")

    # Create a project
    proj_resp = client.post("/v1/projects", json={"name": "C6 Test Project"}, headers=headers)
    assert proj_resp.status_code in (200, 201), proj_resp.text
    pid = proj_resp.json()["id"]

    resp = client.post(
        "/v1/agents/smart-orchestrator/chat",
        json={"message": "what is this project?", "project_id": pid},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"


def test_agent_chat_rejects_unowned_project(client, monkeypatch):
    """POST with project_id owned by user A, called by user B → 404."""
    monkeypatch.setattr(Agent, "_call_llm", _llm_ok())

    headers_a = _register_and_login(client, "ownA")
    headers_b = _register_and_login(client, "ownB")

    # User A creates a project
    proj_resp = client.post("/v1/projects", json={"name": "A's Project"}, headers=headers_a)
    assert proj_resp.status_code in (200, 201), proj_resp.text
    pid = proj_resp.json()["id"]

    # User B tries to chat with it
    resp = client.post(
        "/v1/agents/smart-orchestrator/chat",
        json={"message": "hi", "project_id": pid},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text


def test_agent_chat_persists_conversation(client, monkeypatch):
    """POST with conversation_id stores messages in agent_memory."""
    from app.core import agent_memory as am
    monkeypatch.setattr(Agent, "_call_llm", _llm_ok("remembered answer"))
    headers = _register_and_login(client, "conv")
    conv_id = f"conv-{_RUN}"

    resp = client.post(
        "/v1/agents/smart-orchestrator/chat",
        json={"message": "remember this", "conversation_id": conv_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # conversation_id echoed back
    assert data.get("conversation_id") == conv_id

    # Check messages were persisted
    msgs = am.get_messages(conv_id)
    roles = [(m["role"], m["content"]) for m in msgs]
    assert ("user", "remember this") in roles
    assert ("assistant", "remembered answer") in roles


def test_orchestrator_can_delegate():
    """smart-orchestrator and heavy-reasoning have can_delegate=True; bim-analyst does not."""
    load_agents()
    assert AGENT_REGISTRY["smart-orchestrator"].can_delegate is True
    assert AGENT_REGISTRY["heavy-reasoning"].can_delegate is True
    assert AGENT_REGISTRY["bim-analyst"].can_delegate is False


def test_reasoning_blocks_broadened():
    """construction-pm, contracts-manager, safety-officer, smart-orchestrator all have sympy_reasoning and formula_executor."""
    load_agents()
    for agent_name in ("construction-pm", "contracts-manager", "safety-officer", "smart-orchestrator"):
        agent = AGENT_REGISTRY[agent_name]
        assert "sympy_reasoning" in agent.allowed_blocks, (
            f"{agent_name} missing sympy_reasoning"
        )
        assert "formula_executor" in agent.allowed_blocks, (
            f"{agent_name} missing formula_executor"
        )


def test_project_assistant_loads():
    """project-assistant loads with can_delegate=True and exactly [sympy_reasoning, formula_executor] as allowed_blocks."""
    agents = load_agents()

    # New agent is present
    assert "project-assistant" in agents, "project-assistant not in AGENT_REGISTRY"

    pa = agents["project-assistant"]
    assert pa.can_delegate is True, "project-assistant.can_delegate should be True"
    assert pa.allowed_blocks == ["sympy_reasoning", "formula_executor"], (
        f"project-assistant.allowed_blocks = {pa.allowed_blocks!r}; "
        "expected ['sympy_reasoning', 'formula_executor']"
    )

    # Total count: the new agent makes 14
    assert len(agents) == 14, f"Expected 14 agents total, got {len(agents)}: {sorted(agents)}"

    # All 13 original agents still load without error
    _ORIGINAL_AGENTS = {
        "bim-analyst",
        "construction-pm",
        "contracts-manager",
        "document-analyst",
        "document-ingestion",
        "external-mcp",
        "heavy-reasoning",
        "learning",
        "quantity-surveyor",
        "safety-officer",
        "self-coding",
        "smart-orchestrator",
        "validation",
    }
    for name in _ORIGINAL_AGENTS:
        assert name in agents, f"Original agent '{name}' missing after adding project-assistant"
