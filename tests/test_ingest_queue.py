import pytest
from unittest.mock import patch, AsyncMock

from app.worker import ingest_queue
from app.worker.ingest_queue import enqueue_ingest


@pytest.fixture(autouse=True)
def reset_pool():
    """Reset the module-level pool cache before each test."""
    ingest_queue._pool = None
    yield
    ingest_queue._pool = None


@pytest.mark.asyncio
async def test_enqueue_ingest_returns_false_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert await enqueue_ingest("p1", "d1", "j1") is False


@pytest.mark.asyncio
async def test_enqueue_ingest_returns_false_when_create_pool_raises(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    with patch(
        "app.worker.ingest_queue.create_pool",
        new_callable=AsyncMock,
        side_effect=ConnectionError("Redis unreachable"),
    ):
        result = await enqueue_ingest("p1", "d1", "j1")
    assert result is False


@pytest.mark.asyncio
async def test_enqueue_ingest_returns_true_when_enqueue_succeeds(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=None)
    with patch(
        "app.worker.ingest_queue.create_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ):
        result = await enqueue_ingest("p1", "d1", "j1")
    assert result is True
    mock_pool.enqueue_job.assert_awaited_once_with(
        "ingest_document", "p1", "d1", "j1"
    )
