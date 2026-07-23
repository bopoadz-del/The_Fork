"""Audit entries carry the acting user_id — Stream A."""
import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

_RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _token(client, email):
    client.post("/v1/users/register",
                json={"email": email, "password": "password12"})
    return client.post("/v1/users/login",
                       json={"email": email, "password": "password12"}).json()["token"]


def test_project_audit_records_user_id(client):
    alice = {"Authorization": f"Bearer {_token(client, f'au-{_RUN}@x.com')}"}
    pid = client.post("/v1/projects", json={"name": "Audited"},
                      headers=alice).json()["id"]
    entries = client.get(f"/v1/projects/{pid}/audit", headers=alice).json()["entries"]
    created = [e for e in entries if e["event"] == "project.created"]
    assert created and created[0]["user_id"] != "system"


def test_legacy_audit_records_system_user(client):
    legacy = {"Authorization": "Bearer cb_dev_key"}
    pid = client.post("/v1/projects", json={"name": "Legacy Audited"},
                      headers=legacy).json()["id"]
    entries = client.get(f"/v1/projects/{pid}/audit", headers=legacy).json()["entries"]
    created = [e for e in entries if e["event"] == "project.created"]
    assert created and created[0]["user_id"] == "system"
