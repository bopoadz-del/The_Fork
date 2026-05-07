"""Traffic Manager Block - Governance layer for block-to-block calls"""

import time
from typing import Any, Dict, Optional
from app.core.universal_base import UniversalBlock


class TrafficManagerBlock(UniversalBlock):
    """Rate limiting, circuit breaker, and queue management for inter-block calls."""

    name = "traffic_manager"
    version = "1.0.0"
    description = "Governance layer between block-to-block calls"
    layer = 1
    tags = ["security", "governance", "traffic", "core"]
    requires = ["rate_limiter", "failover", "queue"]

    default_config = {
        "heavy_blocks": ["zvec", "pdf", "construction"],
        "rate_limit_enabled": True,
        "circuit_breaker_enabled": True,
        "queue_enabled": True,
        "default_quota": 1000,
        "circuit_threshold": 5,
        "circuit_timeout": 60
    }

    ui_schema = {
        "input": {
            "type": "json",
            "accept": None,
            "placeholder": '{"source": "chat", "target": "pdf", "payload": {}}',
            "multiline": False
        },
        "output": {
            "type": "json",
            "fields": []
        },
        "quick_actions": [
            {"icon": "🚦", "label": "Route Request", "prompt": '{"source":"chat","target":"pdf","payload":{}}'},
            {"icon": "⚡", "label": "Circuit Status", "prompt": "Get circuit breaker status for all blocks"}
        ]
    }

    def __init__(self, hal_block=None, config=None):
        super().__init__(hal_block, config)
        self._circuit_states = {}  # block_name -> {"failures": 0, "open_until": 0}
        self._registry = {}
        self._instance_cache = {}
        self._create_block_fn = None

    def set_platform(self, registry, instance_cache, create_block_fn, memory_fn=None):
        self._registry = registry
        self._instance_cache = instance_cache
        self._create_block_fn = create_block_fn

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        operation = params.get("operation", "route")

        if operation == "route":
            return await self._route(input_data, params)
        elif operation == "check_limit":
            return await self._check_limit(params.get("key", "default"), params.get("resource", "default"))
        elif operation == "check_circuit":
            return await self._check_circuit_state(params.get("target_block"))
        elif operation == "reset_circuit":
            return await self._reset_circuit(params.get("target_block"))
        elif operation == "get_stats":
            return self._get_stats()

        return {"status": "error", "error": f"Unknown operation: {operation}"}

    async def _route(self, input_data: Dict, params: Dict) -> Dict:
        source_block = input_data.get("source", "unknown") if isinstance(input_data, dict) else "unknown"
        target_block = input_data.get("target") or params.get("target_block")
        payload = input_data.get("payload", {}) if isinstance(input_data, dict) else {}

        if not target_block:
            return {"status": "error", "error": "target_block required"}

        # 1. Rate limiting
        if self.config.get("rate_limit_enabled", True):
            rate_limiter = self.get_dep("rate_limiter")
            if rate_limiter:
                limit_result = await rate_limiter.execute({
                    "action": "check_limit",
                    "key": f"{source_block}:{target_block}",
                    "resource": target_block
                })
                if isinstance(limit_result, dict) and limit_result.get("allowed") is False:
                    return {
                        "error": "quota exceeded",
                        "retry_after": limit_result.get("retry_after", 60),
                        "source": source_block,
                        "target": target_block
                    }

        # 2. Circuit breaker
        if self.config.get("circuit_breaker_enabled", True):
            circuit = await self._check_circuit_state(target_block)
            if circuit.get("open"):
                failover = self.get_dep("failover")
                fallback = "ai_core"
                if failover:
                    try:
                        fallback_result = await failover.execute({
                            "block": target_block,
                            "payload": payload
                        })
                        if "error" not in fallback_result:
                            return {
                                "error": "target_block unavailable",
                                "fallback": fallback,
                                "fallback_result": fallback_result,
                                "source": source_block,
                                "target": target_block
                            }
                    except Exception:
                        pass
                return {
                    "error": "target_block unavailable",
                    "fallback": fallback,
                    "source": source_block,
                    "target": target_block
                }

        # 3. Queue management for heavy blocks
        heavy_blocks = self.config.get("heavy_blocks", ["zvec", "pdf", "construction"])
        if self.config.get("queue_enabled", True) and target_block in heavy_blocks:
            queue = self.get_dep("queue")
            if queue:
                enqueue_result = await queue.execute({
                    "action": "enqueue",
                    "job_type": target_block,
                    "payload": {"source": source_block, "payload": payload},
                    "queue": "heavy_blocks"
                })
                if enqueue_result.get("enqueued"):
                    return {
                        "status": "queued",
                        "job_id": enqueue_result.get("job_id"),
                        "source": source_block,
                        "target": target_block
                    }

        # 4. Execute directly
        block = await self._resolve_block(target_block)
        if block:
            try:
                result = await block.execute(payload)
                # Record success for circuit breaker
                self._record_success(target_block)
                return {
                    "status": "success",
                    "target": target_block,
                    "result": result
                }
            except Exception as e:
                self._record_failure(target_block)
                return {
                    "status": "error",
                    "error": str(e),
                    "target": target_block
                }

        return {
            "status": "error",
            "error": f"Target block '{target_block}' not found",
            "target": target_block
        }

    async def _check_limit(self, key: str, resource: str) -> Dict:
        rate_limiter = self.get_dep("rate_limiter")
        if rate_limiter:
            return await rate_limiter.execute({
                "action": "check_limit",
                "key": key,
                "resource": resource
            })
        return {"allowed": True, "fallback": True}

    async def _check_circuit_state(self, target_block: Optional[str]) -> Dict:
        if not target_block:
            return {"open": False, "error": "target_block required"}
        state = self._circuit_states.get(target_block, {"failures": 0, "open_until": 0})
        now = time.time()
        is_open = state["open_until"] > now
        return {
            "open": is_open,
            "failures": state["failures"],
            "open_until": state["open_until"],
            "target_block": target_block
        }

    async def _reset_circuit(self, target_block: Optional[str]) -> Dict:
        if not target_block:
            return {"reset": False, "error": "target_block required"}
        self._circuit_states[target_block] = {"failures": 0, "open_until": 0}
        return {"reset": True, "target_block": target_block}

    def _record_success(self, target_block: str):
        if target_block in self._circuit_states:
            self._circuit_states[target_block]["failures"] = 0

    def _record_failure(self, target_block: str):
        threshold = self.config.get("circuit_threshold", 5)
        timeout = self.config.get("circuit_timeout", 60)
        state = self._circuit_states.setdefault(target_block, {"failures": 0, "open_until": 0})
        state["failures"] += 1
        if state["failures"] >= threshold:
            state["open_until"] = time.time() + timeout

    def _get_stats(self) -> Dict:
        return {
            "circuits": self._circuit_states,
            "heavy_blocks": self.config.get("heavy_blocks", []),
            "rate_limit_enabled": self.config.get("rate_limit_enabled"),
            "circuit_breaker_enabled": self.config.get("circuit_breaker_enabled"),
            "queue_enabled": self.config.get("queue_enabled"),
        }

    async def _resolve_block(self, block_name: str):
        if block_name in self._instance_cache:
            return self._instance_cache[block_name]
        if block_name in self._registry and self._create_block_fn:
            instance = self._create_block_fn(self._registry[block_name])
            self._instance_cache[block_name] = instance
            return instance
        return None
