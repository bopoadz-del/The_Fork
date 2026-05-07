"""Notification Block - Multi-channel alerts and messaging"""
from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import asyncio
from datetime import datetime, timedelta
from enum import Enum


class NotificationChannel(Enum):
    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    SMS = "sms"
    PUSH = "push"


class NotificationSeverity(Enum):
    CRITICAL = "critical"  # Immediate, all channels
    HIGH = "high"          # Immediate, primary channels
    MEDIUM = "medium"      # Batch, within 15 min
    LOW = "low"            # Digest, daily
    INFO = "info"          # Log only


class NotificationBlock(LegoBlock):
    """
    Notification Block - Multi-channel alerting system
    
    Features:
    - Multi-channel: email, slack, webhook, SMS
    - Severity-based routing
    - Rate limiting
    - Message templates
    - Batch/digest mode
    - Acknowledgment tracking
    """
    name = "notification"
    version = "1.0.0"
    requires = ["email", "config"]
    layer = 4  # Utility layer
    tags = ["notification", "alerts", "communication", "utility"]
    default_config = {
        "channels": ["email", "slack", "webhook"],
        "rate_limit_per_hour": 100,
        "batch_window_minutes": 15,
        "default_template": "default",
        "retry_attempts": 3,
        "retry_delay_seconds": 60
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.templates: Dict[str, Dict] = {}
        self.pending: List[Dict] = []  # Batched notifications
        self.sent_count = 0
        self.failed_count = 0
        self.acknowledged = set()
        self._batch_task = None
        
    async def initialize(self) -> bool:
        """Initialize notification system"""
        print("📢 Notification Block initialized")
        
        # Load default templates
        self._load_default_templates()
        
        # Start batch processor
        self._batch_task = asyncio.create_task(self._batch_processor())
        
        print(f"   Channels: {self.config.get('channels', [])}")
        print(f"   Rate limit: {self.config.get('rate_limit_per_hour', 100)}/hour")
        
        self.initialized = True
        return True
    
    def _load_default_templates(self):
        """Load default message templates"""
        self.templates = {
            "default": {
                "subject": "Notification from {app_name}",
                "body": "{message}",
                "format": "text"
            },
            "alert": {
                "subject": "🚨 ALERT: {severity} - {title}",
                "body": """Alert Details:
Severity: {severity}
Time: {timestamp}
Message: {message}

Acknowledge: {ack_url}""",
                "format": "markdown"
            },
            "digest": {
                "subject": "📊 Daily Digest - {app_name}",
                "body": """Summary of {count} notifications:

{items}

---
View full details: {dashboard_url}""",
                "format": "html"
            },
            "welcome": {
                "subject": "Welcome to {app_name}!",
                "body": "Hi {user_name},\n\nWelcome! Your account is ready.",
                "format": "text"
            }
        }
    
    async def execute(self, input_data: Dict) -> Dict:
        """Handle notification actions"""
        action = input_data.get("action")
        
        if action == "alert":
            return await self._send_alert(input_data)
        elif action == "notify":
            return await self._send_notification(input_data)
        elif action == "broadcast":
            return await self._broadcast(input_data)
        elif action == "create_template":
            return self._create_template(input_data)
        elif action == "get_template":
            return self._get_template(input_data)
        elif action == "acknowledge":
            return self._acknowledge(input_data)
        elif action == "get_pending":
            return self._get_pending(input_data)
        elif action == "flush_batch":
            return await self._flush_batch(input_data)
            
        return {"error": f"Unknown action: {action}"}
    
    async def _send_alert(self, data: Dict) -> Dict:
        """
        Send alert with severity-based routing
        
        Severity routing:
        - critical: All channels, immediate, no batching
        - high: Primary channels, immediate
        - medium: Batch within 15 min
        - low: Daily digest
        - info: Log only
        """
        severity = NotificationSeverity(data.get("severity", "info"))
        message = data.get("message", "")
        title = data.get("title", "Alert")
        channels = data.get("channels", self.config.get("channels", []))
        
        # Check rate limit
        if not await self._check_rate_limit():
            return {"error": "Rate limit exceeded", "retry_after": 3600}
        
        # Route based on severity
        if severity == NotificationSeverity.CRITICAL:
            # Immediate, all channels
            return await self._send_immediate({
                **data,
                "channels": channels,
                "require_ack": True
            })
            
        elif severity == NotificationSeverity.HIGH:
            # Immediate, primary channels
            primary = [c for c in channels if c in ["email", "slack"]]
            return await self._send_immediate({
                **data,
                "channels": primary,
                "require_ack": data.get("require_ack", False)
            })
            
        elif severity == NotificationSeverity.MEDIUM:
            # Batch
            self.pending.append({
                **data,
                "queued_at": datetime.utcnow().isoformat(),
                "severity": severity.value
            })
            return {"queued": True, "severity": severity.value, "batch": True}
            
        elif severity == NotificationSeverity.LOW:
            # Add to daily digest
            self.pending.append({
                **data,
                "queued_at": datetime.utcnow().isoformat(),
                "severity": severity.value,
                "digest": True
            })
            return {"queued": True, "severity": severity.value, "digest": True}
            
        else:  # INFO
            # Log only
            return {"logged": True, "severity": severity.value, "sent": False}
    
    async def _send_notification(self, data: Dict) -> Dict:
        """Send simple notification"""
        to = data.get("to")
        subject = data.get("subject", "Notification")
        body = data.get("body", "")
        channel = data.get("channel", "email")
        
        if channel == "email" and hasattr(self, 'email_block') and self.email_block:
            return await self.email_block.execute({
                "action": "send",
                "to": to,
                "subject": subject,
                "body": body
            })
        
        elif channel == "webhook":
            return await self._send_webhook(data)
        
        elif channel == "slack":
            return await self._send_slack(data)
        
        return {"error": f"Channel {channel} not available"}
    
    async def _send_immediate(self, data: Dict) -> Dict:
        """Send immediately (no batching)"""
        channels = data.get("channels", ["email"])
        results = {}
        
        for channel in channels:
            try:
                if channel == "email":
                    result = await self._send_via_email(data)
                elif channel == "slack":
                    result = await self._send_slack(data)
                elif channel == "webhook":
                    result = await self._send_webhook(data)
                else:
                    result = {"error": f"Unknown channel: {channel}"}
                
                results[channel] = result
                if result.get("sent"):
                    self.sent_count += 1
                else:
                    self.failed_count += 1
                    
            except Exception as e:
                results[channel] = {"error": str(e)}
                self.failed_count += 1
        
        return {
            "sent": True,
            "channels": results,
            "require_ack": data.get("require_ack", False),
            "ack_id": self._generate_ack_id(data) if data.get("require_ack") else None
        }
    
    async def _send_via_email(self, data: Dict) -> Dict:
        """Send via email block"""
        if not hasattr(self, 'email_block') or not self.email_block:
            return {"error": "Email block not available"}
        
        template_name = data.get("template", "alert")
        template = self.templates.get(template_name, self.templates["default"])
        
        subject = template["subject"].format(
            app_name="Cerebrum",
            severity=data.get("severity", "info").upper(),
            title=data.get("title", "Notification")
        )
        
        body = template["body"].format(
            message=data.get("message", ""),
            timestamp=datetime.utcnow().isoformat(),
            severity=data.get("severity", "info"),
            ack_url=f"/ack/{self._generate_ack_id(data)}"
        )
        
        return await self.email_block.execute({
            "action": "send",
            "to": data.get("to"),
            "subject": subject,
            "body": body,
            "html": template.get("format") == "html"
        })
    
    async def _send_slack(self, data: Dict) -> Dict:
        """Send to Slack webhook"""
        webhook_url = data.get("slack_webhook") or self.config.get("slack_webhook")
        
        if not webhook_url:
            return {"error": "Slack webhook not configured"}
        
        try:
            import aiohttp
            
            payload = {
                "text": data.get("message"),
                "attachments": [{
                    "color": self._severity_color(data.get("severity", "info")),
                    "title": data.get("title", "Notification"),
                    "text": data.get("message"),
                    "footer": "Cerebrum",
                    "ts": int(datetime.utcnow().timestamp())
                }]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status == 200:
                        return {"sent": True, "channel": "slack"}
                    return {"error": f"Slack returned {resp.status}"}
                    
        except ImportError:
            return {"error": "aiohttp not installed"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _send_webhook(self, data: Dict) -> Dict:
        """Send to generic webhook"""
        url = data.get("webhook_url")
        
        if not url:
            return {"error": "Webhook URL not provided"}
        
        try:
            import aiohttp
            
            payload = {
                "event": "notification",
                "severity": data.get("severity", "info"),
                "title": data.get("title"),
                "message": data.get("message"),
                "timestamp": datetime.utcnow().isoformat(),
                "source": data.get("source", "cerebrum")
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=30) as resp:
                    return {
                        "sent": resp.status < 400,
                        "channel": "webhook",
                        "status": resp.status
                    }
                    
        except Exception as e:
            return {"error": str(e)}
    
    async def _broadcast(self, data: Dict) -> Dict:
        """Broadcast to all users/channels"""
        message = data.get("message")
        severity = data.get("severity", "info")
        
        results = []
        for channel in self.config.get("channels", []):
            result = await self._send_notification({
                **data,
                "channel": channel
            })
            results.append({"channel": channel, "result": result})
        
        return {
            "broadcast": True,
            "channels": len(results),
            "results": results
        }
    
    def _create_template(self, data: Dict) -> Dict:
        """Create custom message template"""
        name = data.get("name")
        template = {
            "subject": data.get("subject", ""),
            "body": data.get("body", ""),
            "format": data.get("format", "text")
        }
        
        self.templates[name] = template
        return {"created": True, "template": name}
    
    def _get_template(self, data: Dict) -> Dict:
        """Get template"""
        name = data.get("name", "default")
        template = self.templates.get(name)
        
        if not template:
            return {"error": "Template not found"}
        
        return {"template": template}
    
    def _acknowledge(self, data: Dict) -> Dict:
        """Acknowledge alert"""
        ack_id = data.get("ack_id")
        
        if not ack_id:
            return {"error": "ack_id required"}
        
        self.acknowledged.add(ack_id)
        return {"acknowledged": True, "ack_id": ack_id}
    
    def _get_pending(self, data: Dict) -> Dict:
        """Get pending batched notifications"""
        return {
            "pending": len(self.pending),
            "items": self.pending[-10:]  # Last 10
        }
    
    async def _flush_batch(self, data: Dict) -> Dict:
        """Force send batched notifications"""
        if not self.pending:
            return {"flushed": False, "reason": "No pending notifications"}
        
        batch = self.pending[:]
        self.pending = []
        
        # Group by recipient
        by_recipient = {}
        for item in batch:
            recipient = item.get("to", "default")
            if recipient not in by_recipient:
                by_recipient[recipient] = []
            by_recipient[recipient].append(item)
        
        # Send digests
        results = []
        for recipient, items in by_recipient.items():
            result = await self._send_digest(recipient, items)
            results.append(result)
        
        return {
            "flushed": True,
            "sent": len(results),
            "batched": len(batch)
        }
    
    async def _send_digest(self, recipient: str, items: List[Dict]) -> Dict:
        """Send digest email"""
        template = self.templates.get("digest", self.templates["default"])
        
        subject = template["subject"].format(
            app_name="Cerebrum",
            count=len(items)
        )
        
        items_text = "\n".join([
            f"- [{i.get('severity', 'info').upper()}] {i.get('title', 'No title')}"
            for i in items[-20:]  # Last 20
        ])
        
        body = template["body"].format(
            count=len(items),
            items=items_text,
            dashboard_url="/dashboard/notifications"
        )
        
        return await self._send_notification({
            "to": recipient,
            "subject": subject,
            "body": body,
            "channel": "email"
        })
    
    async def _batch_processor(self):
        """Background task to process batched notifications"""
        while True:
            try:
                # Check every 5 minutes
                await asyncio.sleep(300)
                
                if self.pending:
                    # Check if any are old enough to flush
                    now = datetime.utcnow()
                    window = timedelta(minutes=self.config.get("batch_window_minutes", 15))
                    
                    to_flush = [
                        p for p in self.pending
                        if datetime.fromisoformat(p["queued_at"]) < now - window
                    ]
                    
                    if to_flush:
                        await self._flush_batch({})
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Batch processor error: {e}")
    
    async def _check_rate_limit(self) -> bool:
        """Check if under rate limit"""
        # Simple counter - would use sliding window in production
        return self.sent_count < self.config.get("rate_limit_per_hour", 100)
    
    def _severity_color(self, severity: str) -> str:
        """Get Slack color for severity"""
        colors = {
            "critical": "danger",
            "high": "warning",
            "medium": "#ff9900",
            "low": "good",
            "info": "#36a64f"
        }
        return colors.get(severity, "good")
    
    def _generate_ack_id(self, data: Dict) -> str:
        """Generate acknowledgment ID"""
        import hashlib
        content = f"{data.get('title')}:{data.get('message')}:{datetime.utcnow().timestamp()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def health(self) -> Dict:
        """Notification health"""
        h = super().health()
        h["sent"] = self.sent_count
        h["failed"] = self.failed_count
        h["pending"] = len(self.pending)
        h["templates"] = len(self.templates)
        h["channels"] = self.config.get("channels", [])
        return h
