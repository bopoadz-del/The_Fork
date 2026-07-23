"""Observability lane — Sentry helpers, block metrics, health enrichment."""

import importlib.util
import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.dependencies import require_api_key
from app.infra.monitoring import (
    BlockMetricsRegistry,
    JsonLogFormatter,
    capture_llm_transport_failure,
    configure_structured_logging,
    get_observability_health_payload,
    is_llm_transport_failure,
    record_block_execution,
    request_id_ctx,
)


def test_is_llm_transport_failure_detects_dead_tunnel():
    assert is_llm_transport_failure("[Errno -2] Name or service not known")
    assert is_llm_transport_failure("ollama not reachable at http://127.0.0.1:11434")
    assert not is_llm_transport_failure("validation failed: missing field")


@pytest.mark.skipif(
    importlib.util.find_spec("sentry_sdk") is None,
    reason="sentry_sdk not installed",
)
@patch.dict(os.environ, {"SENTRY_DSN": "https://example@sentry.io/1", "OLLAMA_URL": "http://dead-tunnel:11434"})
@patch("sentry_sdk.capture_message", return_value="evt-123")
@patch("sentry_sdk.push_scope")
def test_capture_llm_transport_failure_attaches_ollama_url(mock_scope, mock_capture):
    mock_scope.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_scope.return_value.__exit__ = MagicMock(return_value=False)

    with patch("app.infra.monitoring._sentry_enabled", True):
        event_id = capture_llm_transport_failure(
            "ollama failed: [Errno -2] Name or service not known",
            request_id="req-abc",
            path="/v1/chat/stream",
        )

    assert event_id == "evt-123"
    mock_capture.assert_called_once()
    scope = mock_scope.return_value.__enter__.return_value
    scope.set_context.assert_called_once()
    ctx = scope.set_context.call_args[0][1]
    assert ctx["OLLAMA_URL"] == "http://dead-tunnel:11434"


def test_block_metrics_registry_snapshot():
    reg = BlockMetricsRegistry()
    reg.record("translate", 120, "success")
    reg.record("translate", 80, "error")
    snap = reg.snapshot()
    assert snap["tracked_blocks"] == 1
    assert snap["blocks"]["translate"]["execution_count"] == 2
    assert snap["blocks"]["translate"]["avg_ms"] == 100.0
    assert snap["blocks"]["translate"]["error_count"] == 1


def test_record_block_execution_delegates():
    with patch("app.infra.monitoring.block_metrics.record") as mock_record:
        record_block_execution("chat", 42, "success")
        mock_record.assert_called_once_with("chat", 42, "success")


def test_json_log_formatter_includes_request_id():
    token = request_id_ctx.set("rid-99")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        payload = json.loads(JsonLogFormatter().format(record))
        assert payload["request_id"] == "rid-99"
        assert payload["message"] == "hello"
    finally:
        request_id_ctx.reset(token)


def test_health_v1_includes_observability(client: TestClient):
    response = client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "observability" in data
    assert "block_metrics" in data
    assert data["observability"]["request_tracing"] is True


def test_metrics_endpoint_returns_block_snapshot(client: TestClient):
    from app.main import app

    record_block_execution("search", 15, "success")
    app.dependency_overrides[require_api_key] = lambda: {
        "role": "admin", "user": "admin@test", "valid": True,
    }
    try:
        response = client.get("/v1/metrics")
    finally:
        app.dependency_overrides.pop(require_api_key, None)
    assert response.status_code == 200
    data = response.json()
    assert "blocks" in data
    assert data["blocks"]["search"]["last_ms"] == 15


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app, headers={"Authorization": "Bearer cb_dev_key"})
