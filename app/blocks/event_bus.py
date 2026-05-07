"""Event Bus Block - Request/Response messaging between blocks"""

import asyncio
import uuid
from typing import Any, Dict, Optional
from app.core.universal_base import UniversalBlock


class EventBusBlock(UniversalBlock):
    """Async block-to-block messaging with request/response pattern."""

    name = "event_bus"
    version = "1.0.0"
    description = "Event bus for synchronous and asynchronous block communication"
    layer = 5
    tags = ["interface", "messaging", "events", "core"]
    requires = ["memory"]

    default_config = {
        "default_timeout": 30,
        "max_pending": 1000
    }

    ui_schema = {
        "input": {
            "type": "json",
            "accept": None,
            "placeholder": '{"action": "publish", "topic": "chat.response", "payload": {}}',
            "multiline": True
        },
        "output": {
            "type": "json",
            "fields": []
        },
        "quick_actions": [
            {"icon": "📢", "label": "Publish Event", "prompt": '{"action":"publish","topic":"chat.response","payload":{}}'},
            {"icon": "📋", "label": "List Topics", "prompt": '{"action":"list_topics"}'}
        ]
    }

    def __init__(self, hal_block=None, config=None):
        super().__init__(hal_block, config)
        self._subscribers = {}  # topic -> [handlers]
        self._pending = {}      # correlation_id -> asyncio.Future
        self._event_history = []

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        action = params.get("action")
        if not action and isinstance(input_data, dict):
            action = input_data.get("action")

        if action == "publish":
            return await self._publish(input_data, params)
        elif action == "subscribe":
            return await self._subscribe(input_data, params)
        elif action == "request":
            return await self._request(input_data, params)
        elif action == "respond":
            return await self._respond(input_data, params)
        elif action == "get_topology":
            return self._get_topology()

        return {"status": "error", "error": f"Unknown action: {action}"}

    async def _publish(self, input_data: Dict, params: Dict) -> Dict:
        topic = params.get("topic") or (input_data.get("topic") if isinstance(input_data, dict) else None)
        payload = params.get("payload")
        if payload is None and isinstance(input_data, dict):
            payload = input_data.get("payload", {})
        correlation_id = params.get("correlation_id") or (input_data.get("correlation_id") if isinstance(input_data, dict) else None)

        if not topic:
            return {"status": "error", "error": "topic required"}

        cid = correlation_id or str(uuid.uuid4())[:16]
        event = {
            "topic": topic,
            "correlation_id": cid,
            "payload": payload,
            "timestamp": asyncio.get_event_loop().time()
        }
        self._event_history.append(event)
        if len(self._event_history) > self.config.get("max_pending", 1000):
            self._event_history.pop(0)

        # Notify subscribers
        handlers = list(self._subscribers.get(topic, []))
        for sub_topic, sub_handlers in self._subscribers.items():
            if sub_topic.endswith("*") and topic.startswith(sub_topic[:-1]):
                handlers.extend(sub_handlers)

        delivered = 0
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event))
                else:
                    handler(event)
                delivered += 1
            except Exception:
                pass

        return {
            "status": "success",
            "published": True,
            "topic": topic,
            "correlation_id": cid,
            "delivered": delivered
        }

    async def _subscribe(self, input_data: Dict, params: Dict) -> Dict:
        topic = params.get("topic") or (input_data.get("topic") if isinstance(input_data, dict) else None)
        handler = params.get("handler") or (input_data.get("handler") if isinstance(input_data, dict) else None)

        if not topic:
            return {"status": "error", "error": "topic required"}

        if topic not in self._subscribers:
            self._subscribers[topic] = []
        if handler:
            self._subscribers[topic].append(handler)

        return {
            "status": "success",
            "subscribed": True,
            "topic": topic,
            "total_subscribers": len(self._subscribers[topic])
        }

    async def _request(self, input_data: Dict, params: Dict) -> Dict:
        target_block = params.get("target_block") or (input_data.get("target_block") if isinstance(input_data, dict) else None)
        payload = params.get("payload")
        if payload is None and isinstance(input_data, dict):
            payload = input_data.get("payload", {})
        timeout = params.get("timeout")
        if timeout is None and isinstance(input_data, dict):
            timeout = input_data.get("timeout")
        if timeout is None:
            timeout = self.config.get("default_timeout", 30)

        if not target_block:
            return {"status": "error", "error": "target_block required"}

        correlation_id = str(uuid.uuid4())[:16]
        future = asyncio.get_event_loop().create_future()
        self._pending[correlation_id] = future

        # Publish request
        await self._publish({}, {
            "topic": f"request.{target_block}",
            "payload": {
                "from": self.name,
                "cid": correlation_id,
                "payload": payload
            },
            "correlation_id": correlation_id
        })

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return {
                "status": "success",
                "correlation_id": correlation_id,
                "response": response
            }
        except asyncio.TimeoutError:
            self._pending.pop(correlation_id, None)
            return {
                "status": "error",
                "error": "timeout",
                "correlation_id": correlation_id,
                "timeout_after": timeout
            }

    async def _respond(self, input_data: Dict, params: Dict) -> Dict:
        correlation_id = params.get("correlation_id") or (input_data.get("cid") if isinstance(input_data, dict) else None)
        if not correlation_id and isinstance(input_data, dict):
            correlation_id = input_data.get("correlation_id")
        response = params.get("response")
        if response is None and isinstance(input_data, dict):
            response = input_data.get("response", {})

        if not correlation_id:
            return {"status": "error", "error": "correlation_id required"}

        future = self._pending.pop(correlation_id, None)
        if future and not future.done():
            future.set_result(response)
            return {"status": "success", "delivered": True, "correlation_id": correlation_id}

        return {"status": "error", "error": "Unknown correlation_id", "correlation_id": correlation_id}

    def _get_topology(self) -> Dict:
        return {
            "topics": list(self._subscribers.keys()),
            "subscriber_counts": {t: len(h) for t, h in self._subscribers.items()},
            "pending_requests": len(self._pending),
            "event_history": len(self._event_history)
        }
