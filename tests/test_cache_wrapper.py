import pytest

from app.core.cache_wrapper import cache_get, cache_set, cache_delete


class FakeRedis:
    def __init__(self):
        self._store = {}

    async def get(self, key):
        return self._store.get(key)

    async def setex(self, key, ttl, value):
        self._store[key] = value

    async def delete(self, key):
        return self._store.pop(key, None) is not None


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.mark.asyncio
async def test_cache_round_trip_with_redis(monkeypatch, fake_redis):
    monkeypatch.setattr("app.core.redis_client._async_client", fake_redis)
    assert await cache_set("k", {"v": 1}) is True
    assert await cache_get("k") == {"v": 1}
    assert await cache_delete("k") is True
    assert await cache_get("k") is None
