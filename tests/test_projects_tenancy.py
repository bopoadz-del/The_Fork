"""Multi-tenant isolation at the projects API layer — Stream A."""
import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

LEGACY = {"Authorization": "Bearer cb_dev_key"}

# Run-unique suffix so each test run creates fresh users with no prior history.
_RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _user_token(client, email):
    client.post("/v1/users/register",
                json={"email": email, "password": "password12"})
    return client.post("/v1/users/login",
                       json={"email": email, "password": "password12"}).json()["token"]


def test_user_only_sees_own_projects(client):
    alice = {"Authorization": f"Bearer {_user_token(client, f'ten-alice-{_RUN}@x.com')}"}
    bob = {"Authorization": f"Bearer {_user_token(client, f'ten-bob-{_RUN}@x.com')}"}
    a_pid = client.post("/v1/projects", json={"name": "Alice P"},
                        headers=alice).json()["id"]
    client.post("/v1/projects", json={"name": "Bob P"}, headers=bob)
    alice_list = client.get("/v1/projects", headers=alice).json()["projects"]
    assert [p["name"] for p in alice_list] == ["Alice P"]
    assert all(p["id"] != a_pid for p in
               client.get("/v1/projects", headers=bob).json()["projects"])


def test_cross_tenant_get_returns_404(client):
    alice = {"Authorization": f"Bearer {_user_token(client, f'x-alice-{_RUN}@x.com')}"}
    bob = {"Authorization": f"Bearer {_user_token(client, f'x-bob-{_RUN}@x.com')}"}
    pid = client.post("/v1/projects", json={"name": "Alice Secret"},
                      headers=alice).json()["id"]
    assert client.get(f"/v1/projects/{pid}", headers=bob).status_code == 404
    assert client.delete(f"/v1/projects/{pid}", headers=bob).status_code == 404


def test_cross_tenant_document_and_memory_404(client):
    alice = {"Authorization": f"Bearer {_user_token(client, f'd-alice-{_RUN}@x.com')}"}
    bob = {"Authorization": f"Bearer {_user_token(client, f'd-bob-{_RUN}@x.com')}"}
    pid = client.post("/v1/projects", json={"name": "Alice Docs"},
                      headers=alice).json()["id"]
    files = {"file": ("x.pdf", b"%PDF-1.4", "application/pdf")}
    assert client.post(f"/v1/projects/{pid}/documents", files=files,
                       headers=bob).status_code == 404
    assert client.get(f"/v1/projects/{pid}/memory",
                      headers=bob).status_code == 404


def test_legacy_key_sees_system_projects(client):
    pid = client.post("/v1/projects", json={"name": "Legacy P"},
                      headers=LEGACY).json()["id"]
    listed = client.get("/v1/projects", headers=LEGACY).json()["projects"]
    assert pid in [p["id"] for p in listed]
