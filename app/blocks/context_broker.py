"""Context Broker Block - Shared session state across blocks"""

from typing import Any, Dict, Optional
from app.core.universal_base import UniversalBlock


class ContextBrokerBlock(UniversalBlock):
    """Centralized session management and context coordination."""

    name = "context_broker"
    version = "1.0.0"
    description = "Memory coordinator for cross-block session state"
    layer = 0
    tags = ["infrastructure", "memory", "session", "core"]
    requires = ["memory"]

    default_config = {
        "default_ttl": 3600
    }

    ui_schema = {
        "input": {
            "type": "json",
            "accept": None,
            "placeholder": '{"action": "get_context", "session_id": "user_123"}',
            "multiline": False
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "memory", "type": "json", "label": "Memory"},
                {"name": "drive_files", "type": "json", "label": "Drive Files"},
                {"name": "chat_history", "type": "json", "label": "Chat History"},
                {"name": "construction_data", "type": "json", "label": "Construction Data"}
            ]
        },
        "quick_actions": [
            {"icon": "🧠", "label": "Get Context", "prompt": '{"action":"get_context","session_id":"user_123"}'},
            {"icon": "💾", "label": "Save Context", "prompt": '{"action":"set_context","session_id":"user_123","data":{}}'}
        ]
    }

    def __init__(self, hal_block=None, config=None):
        super().__init__(hal_block, config)
        self._registry = {}
        self._instance_cache = {}
        self._create_block_fn = None
        self._memory_fn = None

    def set_platform(self, registry, instance_cache, create_block_fn, memory_fn=None):
        self._registry = registry
        self._instance_cache = instance_cache
        self._create_block_fn = create_block_fn
        self._memory_fn = memory_fn

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        action = params.get("action")
        if not action and isinstance(input_data, dict):
            action = input_data.get("action")
        session_id = params.get("session_id")
        if not session_id and isinstance(input_data, dict):
            session_id = input_data.get("session_id")

        if action in ("get_context", "get"):
            return await self._get_context(session_id)
        elif action in ("set_context", "set"):
            return await self._set_context(session_id, input_data, params)
        elif action in ("merge_context", "merge"):
            return await self._merge_context(session_id, input_data, params)
        elif action in ("clear_context", "clear"):
            return await self._clear_context(session_id)

        return {"status": "error", "error": f"Unknown action: {action}. Use: get_context, set_context, merge_context, clear_context"}

    async def _get_context(self, session_id: Optional[str]) -> Dict:
        if not session_id:
            return {"status": "error", "error": "session_id required"}

        memory = await self._get_memory_block()
        mem_data = {}
        if memory:
            result = await memory.execute({"action": "get", "key": f"ctx:{session_id}:memory"})
            mem_data = result.get("value", {}) if isinstance(result, dict) else {}

        context = {
            "session_id": session_id,
            "memory": mem_data,
        }

        return {"status": "success", "context": context}

    async def _set_context(self, session_id: Optional[str], input_data: Dict, params: Dict) -> Dict:
        if not session_id:
            return {"status": "error", "error": "session_id required"}

        key = params.get("key")
        if not key and isinstance(input_data, dict):
            key = input_data.get("key")
        value = params.get("value")
        if value is None and isinstance(input_data, dict):
            value = input_data.get("value")
        ttl = params.get("ttl", self.config.get("default_ttl", 3600))

        if not key:
            return {"status": "error", "error": "key required"}

        memory = await self._get_memory_block()
        if memory:
            await memory.execute({
                "action": "set",
                "key": f"ctx:{session_id}:memory:{key}",
                "value": value,
                "ttl": ttl
            })
            return {"status": "success", "stored": True, "session_id": session_id, "key": key}

        return {"status": "error", "error": "Memory block not available"}

    async def _merge_context(self, session_id: Optional[str], input_data: Dict, params: Dict) -> Dict:
        if not session_id:
            return {"status": "error", "error": "session_id required"}

        updates = params.get("updates")
        if updates is None and isinstance(input_data, dict):
            updates = input_data.get("updates", {})
        memory = await self._get_memory_block()

        if memory:
            # Get existing
            existing = await memory.execute({"action": "get", "key": f"ctx:{session_id}:memory"})
            merged = existing.get("value", {}) if isinstance(existing, dict) else {}
            if not isinstance(merged, dict):
                merged = {}
            if isinstance(updates, dict):
                merged.update(updates)
            else:
                merged = updates
            await memory.execute({
                "action": "set",
                "key": f"ctx:{session_id}:memory",
                "value": merged,
                "ttl": self.config.get("default_ttl", 3600)
            })
            return {"status": "success", "merged": True, "session_id": session_id}

        return {"status": "error", "error": "Memory block not available"}

    async def _clear_context(self, session_id: Optional[str]) -> Dict:
        if not session_id:
            return {"status": "error", "error": "session_id required"}

        memory = await self._get_memory_block()
        if memory:
            await memory.execute({"action": "delete", "key": f"ctx:{session_id}:memory"})
            return {"status": "success", "cleared": True, "session_id": session_id}

        return {"status": "error", "error": "Memory block not available"}

    async def _get_memory_block(self):
        memory = self.get_dep("memory")
        if memory:
            return memory
        if self._memory_fn:
            try:
                return self._memory_fn()
            except Exception:
                pass
        return None

    async def _call_block(self, block_name: str, payload: Dict) -> Any:
        if block_name in self._instance_cache:
            block = self._instance_cache[block_name]
        elif block_name in self._registry and self._create_block_fn:
            block = self._create_block_fn(self._registry[block_name])
            self._instance_cache[block_name] = block
        else:
            return None

        try:
            result = await block.execute(payload)
            return result.get("result", result)
        except Exception:
            return None
