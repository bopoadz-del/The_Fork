import pytest
from unittest.mock import patch, AsyncMock

from app.worker.ingest_queue import enqueue_ingest


@pytest.mark.asyncio
async def test_enqueue_ingest_returns_false_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert await enqueue_ingest("p1", "d1", "j1") is False
