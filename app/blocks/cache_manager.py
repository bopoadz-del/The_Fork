"""Cache Manager Block - Redis wrapper with in-memory fallback."""

import json
import os
import time
from typing import Any, Dict, Optional
from app.core.universal_base import UniversalBlock


class CacheManagerBlock(UniversalBlock):
    """Key-value cache with Redis support and local fallback."""

    name = "cache_manager"
    version = "1.0.0"
    description = "Redis wrapper with get/set/delete/stats actions"
    layer = 0
    tags = ["infrastructure", "cache", "redis"]
    requires = []

    default_config = {
        "default_ttl": 3600,
        "max_local_entries": 10000,
        "redis_url": os.environ.get("REDIS_URL")  # falls back to in-memory if unset
    }

    ui_schema = {
        "input": {
            "type": "json",
            "accept": None,
            "placeholder": '{"action": "get", "key": "my-key"}',
            "multiline": False
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "value", "type": "json", "label": "Cached Value"}
            ]
        },
        "quick_actions": [
            {"icon": "📥", "label": "Get Value", "prompt": '{"action":"get","key":"my-key"}'},
            {"icon": "📤", "label": "Set Value", "prompt": '{"action":"set","key":"my-key","value":"my-value","ttl":3600}'},
            {"icon": "🗑️", "label": "Clear Cache", "prompt": '{"action":"clear"}'}
        ]
    }

    def __init__(self, hal_block=None, config=None):
        super().__init__(hal_block, config)
        self._local_cache: Dict[str, Dict] = {}
        self._redis = None
        self._init_redis()

    def _init_redis(self):
        redis_url = self.config.get("redis_url") or self.default_config.get("redis_url")
        if redis_url:
            try:
                import redis
                self._redis = redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """Route to appropriate cache action."""
        params = params or {}
        action = params.get("action") or (input_data.get("action") if isinstance(input_data, dict) else "stats")
        handlers = {
            "get": self.get,
            "set": self.set,
            "delete": self.delete,
            "exists": self.exists,
            "flush": self.flush,
            "stats": self.stats,
            "health_check": self.health_check,
        }
        handler = handlers.get(action)
        if not handler:
            return {"status": "error", "error": f"Unknown action: {action}"}
        return await handler(input_data, params)

    async def get(self, input_data: Any, params: Dict) -> Dict:
        """Retrieve value by key."""
        key = self._resolve_key(input_data, params)
        if not key:
            return {"status": "error", "error": "No key provided"}

        if self._redis:
            try:
                raw = self._redis.get(key)
                if raw is None:
                    return {"status": "success", "found": False, "key": key}
                return {"status": "success", "found": True, "key": key, "value": json.loads(raw)}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        entry = self._local_cache.get(key)
        if entry is None or entry.get("expires", float("inf")) < time.time():
            return {"status": "success", "found": False, "key": key}
        return {"status": "success", "found": True, "key": key, "value": entry["value"]}

    async def set(self, input_data: Any, params: Dict) -> Dict:
        """Store value by key with optional TTL."""
        key = self._resolve_key(input_data, params)
        if not key:
            return {"status": "error", "error": "No key provided"}

        value = params.get("value") or (input_data.get("value") if isinstance(input_data, dict) else None)
        ttl = params.get("ttl", self.config.get("default_ttl", 3600))

        if self._redis:
            try:
                self._redis.setex(key, ttl, json.dumps(value))
                return {"status": "success", "action": "set", "key": key, "ttl": ttl}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # Enforce local limit
        if len(self._local_cache) >= self.config.get("max_local_entries", 10000):
            self._evict_oldest()

        self._local_cache[key] = {"value": value, "expires": time.time() + ttl}
        return {"status": "success", "action": "set", "key": key, "ttl": ttl}

    async def delete(self, input_data: Any, params: Dict) -> Dict:
        """Remove key from cache."""
        key = self._resolve_key(input_data, params)
        if not key:
            return {"status": "error", "error": "No key provided"}

        if self._redis:
            try:
                deleted = self._redis.delete(key)
                return {"status": "success", "deleted": bool(deleted), "key": key}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        existed = key in self._local_cache
        self._local_cache.pop(key, None)
        return {"status": "success", "deleted": existed, "key": key}

    async def exists(self, input_data: Any, params: Dict) -> Dict:
        """Check if key exists."""
        key = self._resolve_key(input_data, params)
        if not key:
            return {"status": "error", "error": "No key provided"}

        if self._redis:
            try:
                return {"status": "success", "exists": bool(self._redis.exists(key)), "key": key}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        entry = self._local_cache.get(key)
        exists = entry is not None and entry.get("expires", float("inf")) >= time.time()
        return {"status": "success", "exists": exists, "key": key}

    async def flush(self, input_data: Any = None, params: Dict = None) -> Dict:
        """Clear all cached entries."""
        if self._redis:
            try:
                self._redis.flushdb()
                return {"status": "success", "action": "flush"}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        count = len(self._local_cache)
        self._local_cache.clear()
        return {"status": "success", "action": "flush", "local_entries_cleared": count}

    async def stats(self, input_data: Any = None, params: Dict = None) -> Dict:
        """Return cache statistics."""
        if self._redis:
            try:
                info = self._redis.info()
                return {
                    "status": "success",
                    "backend": "redis",
                    "keys": self._redis.dbsize(),
                    "used_memory_human": info.get("used_memory_human", "unknown")
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        # Clean expired local entries
        now = time.time()
        valid = {k: v for k, v in self._local_cache.items() if v.get("expires", float("inf")) >= now}
        self._local_cache = valid
        return {
            "status": "success",
            "backend": "local",
            "entries": len(self._local_cache)
        }

    async def health_check(self, input_data: Any = None, params: Dict = None) -> Dict:
        """Health check for cache manager."""
        return {
            "status": "success",
            "block": self.name,
            "version": self.version,
            "redis_connected": self._redis is not None
        }

    def _resolve_key(self, input_data: Any, params: Dict) -> Optional[str]:
        return params.get("key") or (input_data.get("key") if isinstance(input_data, dict) else None)

    def _evict_oldest(self):
        if self._local_cache:
            oldest = min(self._local_cache, key=lambda k: self._local_cache[k]["expires"])
            self._local_cache.pop(oldest, None)

    def get_actions(self) -> Dict[str, Any]:
        """Return all public methods for block registry."""
        return {
            "get": self.get,
            "set": self.set,
            "delete": self.delete,
            "exists": self.exists,
            "flush": self.flush,
            "stats": self.stats,
            "health_check": self.health_check,
        }
