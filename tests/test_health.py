"""Health endpoint tests."""

import os
import pytest
from fastapi.testclient import TestClient

from app.core import redis_client


@pytest.fixture(autouse=True)
def _reset_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_client.reset_for_tests()
    yield
    redis_client.reset_for_tests()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    from app.main import app
    return TestClient(app, headers={"Authorization": "Bearer cb_dev_key"})


def test_health_sync_legacy(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert "redis" not in data


def test_health_v1_async_includes_redis(client: TestClient):
    response = client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "observability" in data
    assert "block_metrics" in data
    assert "redis" in data
    assert data["redis"] == {"connected": False, "latency_ms": None}
