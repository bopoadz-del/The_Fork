"""Regression tests for recently hardened API behavior."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app, headers={"Authorization": "Bearer cb_dev_key"})


def test_v1_block_detail_route_uses_block_info_handler():
    """The v1 block detail route should resolve through the same handler as /blocks/{name}."""
    response = client.get("/v1/blocks/chat")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "chat"
    assert data["config"]["version"] == "3.0.0"
