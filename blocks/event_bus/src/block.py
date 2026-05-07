"""Event Bus Block - BLOCK 42: The Answer

Central nervous system routes events between 8 containers.
Non-blocking, correlation ID based, dead letter queue.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, Callable, List, Optional
import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Event:
    """Event structure for cross-container communication"""
    topic: str
    correlation_id: str
    payload: Dict[str, Any]
    timestamp: float
    reply_to: Optional[str] = None
    target_module: Optional[str] = None
    priority: int = 0  # 0=normal, 1=high, 2=critical


class EventBusBlock(LegoBlock):
    """
    BLOCK 42: The Answer.
    Central nervous system routes events between 8 containers.
    Non-blocking, correlation ID based, dead letter queue.
    """
    name = "event_bus"
    version = "1.0.0"
    requires = ["memory", "queue"]
    layer = 0
    tags = ["infra", "messaging", "core", "nervous_system", "block_42"]
    
    default_config = {
        "delivery_guarantee": "at_least_once",
        "max_retries": 3,
        "dead_letter_enabled": True,
        "event_ttl": 86400,  # 24 hours
        "max_pending": 10000,
        "cleanup_interval": 300  # 5 minutes
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.subscribers: Dict[str, List[Callable]] = {}
        self.containers: Dict[str, Any] = {}
        self.pending_responses: Dict[str, asyncio.Future] = {}
        self.dead_letter_queue: List[Dict] = []
        self.event_history: List[Event] = []
        self.metrics = {
            "published": 0,
            "delivered": 0,
            "failed": 0,
            "dead_lettered": 0
        }
        
    async def initialize(self) -> bool:
        """Initialize event bus"""
        print("🔌 Event Bus Block initializing...")
        print("   BLOCK 42: The Answer")
        print(f"   Delivery: {self.config['delivery_guarantee']}")
        print(f"   Max retries: {self.config['max_retries']}")
        
        # Start background cleanup
        asyncio.create_task(self._background_cleanup())
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute event bus actions"""
        action = input_data.get("action")
        
        actions = {
            "publish": self._publish,
            "subscribe": self._subscribe,
            "register_container": self._register_container,
            "unregister_container": self._unregister_container,
            "route_to_container": self._route_to_container,
            "respond": self._handle_response,
            "get_topology": self._get_topology,
            "get_metrics": self._get_metrics,
            "get_dead_letter": self._get_dead_letter,
            "replay_dead_letter": self._replay_dead_letter
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _publish(self, data: Dict) -> Dict:
        """Publish event to a topic"""
        topic = data.get("topic")
        payload = data.get("payload", {})
        correlation_id = data.get("correlation_id") or uuid.uuid4().hex
        reply_to = data.get("reply_to")
        priority = data.get("priority", 0)
        
        if not topic:
            return {"error": "topic required"}
            
        event = Event(
            topic=topic,
            correlation_id=correlation_id,
            payload=payload,
            timestamp=time.time(),
            reply_to=reply_to,
            priority=priority
        )
        
        # Store in history
        self.event_history.append(event)
        if len(self.event_history) > self.config["max_pending"]:
            self.event_history.pop(0)
            
        self.metrics["published"] += 1
        
        # Notify subscribers
        delivered = 0
        handlers = self.subscribers.get(topic, [])
        
        # Also check wildcard subscriptions
        for sub_topic, sub_handlers in self.subscribers.items():
            if sub_topic.endswith("*") and topic.startswith(sub_topic[:-1]):
                handlers.extend(sub_handlers)
                
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    asyncio.create_task(handler(event))
                else:
                    handler(event)
                delivered += 1
            except Exception as e:
                self.metrics["failed"] += 1
                if self.config["dead_letter_enabled"]:
                    self.dead_letter_queue.append({
                        "event": event,
                        "error": str(e),
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    
        self.metrics["delivered"] += delivered
        
        return {
            "published": True,
            "topic": topic,
            "correlation_id": correlation_id,
            "subscribers_notified": len(handlers),
            "delivered": delivered
        }
        
    async def _subscribe(self, data: Dict) -> Dict:
        """Subscribe to a topic"""
        topic = data.get("topic")
        handler = data.get("handler")  # Callable
        
        if not topic:
            return {"error": "topic required"}
            
        if topic not in self.subscribers:
            self.subscribers[topic] = []
            
        if handler:
            self.subscribers[topic].append(handler)
            
        return {
            "subscribed": True,
            "topic": topic,
            "total_subscribers": len(self.subscribers[topic])
        }
        
    async def _register_container(self, data: Dict) -> Dict:
        """Register a container with the event bus"""
        container_id = data.get("container_id")
        container_instance = data.get("instance")
        topics = data.get("topics", [])  # Topics this container listens to
        
        if not container_id:
            return {"error": "container_id required"}
            
        self.containers[container_id] = {
            "id": container_id,
            "instance": container_instance,
            "registered_at": datetime.utcnow().isoformat(),
            "topics": topics
        }
        
        # Auto-subscribe container topics
        for topic in topics:
            if topic not in self.subscribers:
                self.subscribers[topic] = []
                
        print(f"   ✓ Container registered: {container_id}")
        
        return {
            "registered": True,
            "container_id": container_id,
            "topics": topics
        }
        
    async def _unregister_container(self, data: Dict) -> Dict:
        """Unregister a container"""
        container_id = data.get("container_id")
        
        if container_id in self.containers:
            del self.containers[container_id]
            return {"unregistered": True, "container_id": container_id}
            
        return {"error": "Container not found"}
        
    async def _route_to_container(self, data: Dict) -> Dict:
        """Non-blocking cross-container RPC using Correlation ID pattern"""
        target_container = data.get("to")
        target_module = data.get("module")
        payload = data.get("payload", {})
        await_response = data.get("await_response", False)
        timeout = data.get("timeout", 30)
        
        if not target_container:
            return {"error": "target container (to) required"}
            
        if target_container not in self.containers:
            return {"error": f"Container {target_container} not registered"}
            
        correlation_id = uuid.uuid4().hex
        
        # Create future for async response
        if await_response:
            self.pending_responses[correlation_id] = asyncio.Future()
            
        # Build event
        event_data = {
            "topic": f"container.{target_container}.rpc",
            "correlation_id": correlation_id,
            "reply_to": data.get("reply_to", "unknown"),
            "payload": payload,
            "target_module": target_module,
            "await_response": await_response
        }
        
        # Publish
        await self._publish(event_data)
        
        if await_response:
            # Wait for response with timeout
            try:
                response = await asyncio.wait_for(
                    self.pending_responses[correlation_id], 
                    timeout=timeout
                )
                return {
                    "sent": True,
                    "correlation_id": correlation_id,
                    "response": response,
                    "status": "completed"
                }
            except asyncio.TimeoutError:
                if correlation_id in self.pending_responses:
                    del self.pending_responses[correlation_id]
                return {
                    "sent": True,
                    "correlation_id": correlation_id,
                    "status": "timeout",
                    "timeout_after": timeout
                }
        
        return {
            "sent": True,
            "correlation_id": correlation_id,
            "status": "fire_and_forget"
        }
        
    async def _handle_response(self, data: Dict) -> Dict:
        """Handle response from target container"""
        correlation_id = data.get("correlation_id")
        response = data.get("response", {})
        
        if correlation_id in self.pending_responses:
            future = self.pending_responses.pop(correlation_id)
            if not future.done():
                future.set_result(response)
            return {"delivered": True, "correlation_id": correlation_id}
            
        # Response came too late or invalid ID
        return {
            "error": "Unknown correlation_id - response too late or invalid",
            "correlation_id": correlation_id
        }
        
    def _get_topology(self) -> Dict:
        """Get current system topology"""
        return {
            "containers": {
                cid: {
                    "topics": info["topics"],
                    "registered_at": info["registered_at"]
                }
                for cid, info in self.containers.items()
            },
            "topics": list(self.subscribers.keys()),
            "subscriber_counts": {
                topic: len(handlers)
                for topic, handlers in self.subscribers.items()
            },
            "pending_requests": len(self.pending_responses),
            "dead_letter_count": len(self.dead_letter_queue)
        }
        
    async def _get_metrics(self, data: Dict) -> Dict:
        """Get event bus metrics"""
        return {
            "metrics": self.metrics,
            "containers": len(self.containers),
            "topics": len(self.subscribers),
            "pending_responses": len(self.pending_responses),
            "event_history": len(self.event_history),
            "dead_letter_queue": len(self.dead_letter_queue)
        }
        
    async def _get_dead_letter(self, data: Dict) -> Dict:
        """Get dead letter queue contents"""
        limit = data.get("limit", 10)
        
        return {
            "dead_letters": self.dead_letter_queue[-limit:],
            "total": len(self.dead_letter_queue)
        }
        
    async def _replay_dead_letter(self, data: Dict) -> Dict:
        """Replay events from dead letter queue"""
        count = 0
        replayed = []
        
        # Replay recent dead letters
        for item in self.dead_letter_queue[:]:
            event = item.get("event")
            if event:
                result = await self._publish({
                    "topic": event.topic,
                    "payload": event.payload,
                    "correlation_id": event.correlation_id,
                    "reply_to": event.reply_to
                })
                if result.get("published"):
                    count += 1
                    replayed.append(event.correlation_id)
                    
        return {
            "replayed": count,
            "correlation_ids": replayed
        }
        
    async def _background_cleanup(self):
        """Clean up expired events and responses"""
        while True:
            await asyncio.sleep(self.config["cleanup_interval"])
            
            now = time.time()
            ttl = self.config["event_ttl"]
            
            # Clean old events from history
            cutoff = now - ttl
            self.event_history = [
                e for e in self.event_history 
                if e.timestamp > cutoff
            ]
            
            # Clean stale pending responses
            stale_ids = [
                cid for cid, future in self.pending_responses.items()
                if future.done() or time.time() - getattr(future, '_created_at', now) > 3600
            ]
            for cid in stale_ids:
                del self.pending_responses[cid]
                
    def health(self) -> Dict:
        h = super().health()
        h["containers_connected"] = len(self.containers)
        h["topics_active"] = len(self.subscribers)
        h["pending_requests"] = len(self.pending_responses)
        h["dead_letters"] = len(self.dead_letter_queue)
        h["is_block_42"] = True
        return h
