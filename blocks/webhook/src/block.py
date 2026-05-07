"""Webhook Block - Outgoing webhooks"""
from blocks.base import LegoBlock
from typing import Dict, Any, List
import asyncio
import hmac
import hashlib

class WebhookBlock(LegoBlock):
    """Outgoing webhooks with retries and signatures"""
    name = "webhook"
    version = "1.0.0"
    requires = ["config", "queue"]
    layer = 5  # Integration layer
    tags = ["webhook", "http", "integration"]
    default_config = {
        "timeout": 30,
        "retries": 3,
        "verify_ssl": True
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.secret = config.get("secret", "")
        self.timeout = config.get("timeout", 30)
        self.max_retries = config.get("max_retries", 3)
        self.queue_block = None
        
        # Registered webhooks
        self.endpoints = {}  # name -> {url, events, secret}
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "register":
            return await self._register_webhook(input_data)
        elif action == "send":
            return await self._send_webhook(input_data)
        elif action == "trigger":
            return await self._trigger_event(input_data)
        elif action == "list":
            return await self._list_webhooks()
        return {"error": "Unknown action"}
    
    async def _register_webhook(self, data: Dict) -> Dict:
        """Register a webhook endpoint"""
        name = data.get("name")
        url = data.get("url")
        events = data.get("events", ["*"])  # ["user.created", "payment.received"]
        secret = data.get("secret", self.secret)
        
        self.endpoints[name] = {
            "name": name,
            "url": url,
            "events": events,
            "secret": secret,
            "created": asyncio.get_event_loop().time()
        }
        
        return {
            "registered": True,
            "name": name,
            "url": url,
            "events": events
        }
    
    async def _send_webhook(self, data: Dict) -> Dict:
        """Send webhook to specific URL"""
        url = data.get("url")
        payload = data.get("payload", {})
        secret = data.get("secret", self.secret)
        headers = data.get("headers", {})
        
        # Add signature
        payload_str = str(payload)
        signature = hmac.new(
            secret.encode(),
            payload_str.encode(),
            hashlib.sha256
        ).hexdigest()
        
        headers.update({
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": str(int(asyncio.get_event_loop().time()))
        })
        
        # Send with retries
        for attempt in range(self.max_retries):
            try:
                import aiohttp
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=self.timeout
                    ) as resp:
                        if resp.status < 400:
                            return {
                                "sent": True,
                                "url": url,
                                "status": resp.status,
                                "attempt": attempt + 1
                            }
                        else:
                            error_body = await resp.text()
                            if attempt < self.max_retries - 1:
                                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            return {
                                "error": f"HTTP {resp.status}: {error_body}",
                                "url": url,
                                "attempts": self.max_retries
                            }
                            
            except Exception as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {
                    "error": f"Failed after {self.max_retries} attempts: {str(e)}",
                    "url": url
                }
    
    async def _trigger_event(self, data: Dict) -> Dict:
        """Trigger event to all registered webhooks"""
        event = data.get("event")  # e.g., "user.created"
        payload = data.get("payload", {})
        
        results = []
        
        for name, endpoint in self.endpoints.items():
            # Check if webhook subscribed to this event
            if "*" not in endpoint["events"] and event not in endpoint["events"]:
                continue
            
            # Queue or send directly
            if self.queue_block:
                await self.queue_block.execute({
                    "action": "enqueue",
                    "job_type": "webhook",
                    "payload": {
                        "url": endpoint["url"],
                        "payload": {**payload, "event": event},
                        "secret": endpoint["secret"]
                    }
                })
                results.append({"name": name, "status": "queued"})
            else:
                result = await self._send_webhook({
                    "url": endpoint["url"],
                    "payload": {**payload, "event": event},
                    "secret": endpoint["secret"]
                })
                results.append({"name": name, **result})
        
        return {
            "event": event,
            "triggered": len(results),
            "results": results
        }
    
    async def _list_webhooks(self) -> Dict:
        """List registered webhooks"""
        return {
            "webhooks": [
                {"name": w["name"], "url": w["url"], "events": w["events"]}
                for w in self.endpoints.values()
            ],
            "count": len(self.endpoints)
        }
    
    def health(self) -> Dict:
        h = super().health()
        h["endpoints"] = len(self.endpoints)
        h["max_retries"] = self.max_retries
        return h
