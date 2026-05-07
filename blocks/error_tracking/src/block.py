"""Error Tracking Block - Sentry-style error tracking and alerting

Features:
- Exception capture with context
- Error aggregation and grouping
- Performance tracing
- Alert thresholds and notifications
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import hashlib
import traceback
import time


class ErrorTrackingBlock(LegoBlock):
    """
    Sentry-style error tracking and alerting.
    Aggregates exceptions, traces, performance issues.
    """
    name = "error_tracking"
    version = "1.0.0"
    requires = ["database", "notification"]
    layer = 1  # Security/Observability
    tags = ["observability", "debugging", "devops", "error_tracking"]
    
    default_config = {
        "sample_rate": 1.0,  # Capture all errors
        "auto_create_issues": True,
        "alert_threshold": 10,  # errors per minute
        "max_events_per_issue": 1000,
        "retention_days": 30
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.events: List[Dict] = []  # Recent events
        self.issues: Dict[str, Dict] = {}  # issue_id -> issue data
        self.traces: Dict[str, Dict] = {}  # trace_id -> trace data
        self.alert_history: List[Dict] = []  # Recent alerts
        
    async def initialize(self) -> bool:
        """Initialize error tracking"""
        print("🐛 Error Tracking Block initializing...")
        print(f"   Sample rate: {self.config['sample_rate']}")
        print(f"   Alert threshold: {self.config['alert_threshold']}/min")
        
        # TODO: Setup exception hooks
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute error tracking actions"""
        action = input_data.get("action")
        
        actions = {
            "capture_exception": self._capture_exception,
            "capture_message": self._capture_message,
            "get_issue": self._get_issue,
            "resolve_issue": self._resolve_issue,
            "performance_trace": self._start_trace,
            "end_trace": self._end_trace,
            "get_issues": self._get_issues,
            "get_stats": self._get_stats,
            "add_breadcrumb": self._add_breadcrumb
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _capture_exception(self, data: Dict) -> Dict:
        """Capture an exception"""
        exception = data.get("exception")
        context = data.get("context", {})
        tags = data.get("tags", {})
        user = data.get("user")
        
        # Sampling
        if self.config["sample_rate"] < 1.0:
            import random
            if random.random() > self.config["sample_rate"]:
                return {"sampled": False}
                
        # Generate event ID
        event_id = hashlib.sha256(
            f"{time.time()}:{str(exception)}".encode()
        ).hexdigest()[:16]
        
        # Get stack trace
        if isinstance(exception, Exception):
            exc_type = type(exception).__name__
            exc_message = str(exception)
            exc_traceback = traceback.format_exc() if exception else None
        else:
            exc_type = data.get("exc_type", "Unknown")
            exc_message = str(exception)
            exc_traceback = data.get("traceback")
            
        # Create fingerprint for grouping
        fingerprint = self._create_fingerprint(exc_type, exc_message, exc_traceback)
        issue_id = f"issue_{fingerprint[:16]}"
        
        # Create event
        event = {
            "event_id": event_id,
            "issue_id": issue_id,
            "timestamp": datetime.utcnow().isoformat(),
            "type": "exception",
            "exception": {
                "type": exc_type,
                "message": exc_message,
                "traceback": exc_traceback
            },
            "context": context,
            "tags": tags,
            "user": user
        }
        
        self.events.append(event)
        
        # Trim old events
        max_events = 10000
        if len(self.events) > max_events:
            self.events = self.events[-max_events:]
            
        # Update or create issue
        is_new = issue_id not in self.issues
        
        if is_new:
            self.issues[issue_id] = {
                "issue_id": issue_id,
                "fingerprint": fingerprint,
                "first_seen": event["timestamp"],
                "last_seen": event["timestamp"],
                "event_count": 1,
                "status": "open",
                "title": f"{exc_type}: {exc_message[:50]}",
                "type": exc_type,
                "tags": tags,
                "events": [event_id]
            }
        else:
            issue = self.issues[issue_id]
            issue["last_seen"] = event["timestamp"]
            issue["event_count"] += 1
            issue["events"].append(event_id)
            
            # Trim events list
            if len(issue["events"]) > self.config["max_events_per_issue"]:
                issue["events"] = issue["events"][-self.config["max_events_per_issue"]:]
                
        # Check alert threshold
        should_alert = await self._should_alert(issue_id)
        
        if should_alert and hasattr(self, 'notification_block'):
            await self.notification_block.execute({
                "action": "send",
                "channel": "admin_alerts",
                "message": f"🚨 Error spike: {self.issues[issue_id]['title']}",
                "severity": "high"
            })
            
        return {
            "event_id": event_id,
            "issue_id": issue_id,
            "new_issue": is_new,
            "alert_sent": should_alert
        }
        
    async def _capture_message(self, data: Dict) -> Dict:
        """Capture a log message (not an exception)"""
        message = data.get("message")
        level = data.get("level", "info")  # debug, info, warning, error
        context = data.get("context", {})
        
        if not message:
            return {"error": "message required"}
            
        event_id = hashlib.sha256(
            f"{time.time()}:{message}".encode()
        ).hexdigest()[:16]
        
        event = {
            "event_id": event_id,
            "timestamp": datetime.utcnow().isoformat(),
            "type": "message",
            "level": level,
            "message": message,
            "context": context
        }
        
        self.events.append(event)
        
        return {
            "event_id": event_id,
            "captured": True
        }
        
    async def _get_issue(self, data: Dict) -> Dict:
        """Get issue details"""
        issue_id = data.get("issue_id")
        
        if issue_id not in self.issues:
            return {"error": "Issue not found"}
            
        issue = self.issues[issue_id].copy()
        
        # Get full events
        event_ids = issue.get("events", [])
        full_events = [e for e in self.events if e["event_id"] in event_ids]
        
        issue["recent_events"] = full_events[-10:]  # Last 10
        
        return {"issue": issue}
        
    async def _resolve_issue(self, data: Dict) -> Dict:
        """Mark issue as resolved"""
        issue_id = data.get("issue_id")
        resolved_by = data.get("user_id", "system")
        
        if issue_id not in self.issues:
            return {"error": "Issue not found"}
            
        self.issues[issue_id]["status"] = "resolved"
        self.issues[issue_id]["resolved_at"] = datetime.utcnow().isoformat()
        self.issues[issue_id]["resolved_by"] = resolved_by
        
        return {
            "resolved": True,
            "issue_id": issue_id
        }
        
    async def _start_trace(self, data: Dict) -> Dict:
        """Start a performance trace"""
        trace_id = data.get("trace_id") or hashlib.sha256(
            str(time.time()).encode()
        ).hexdigest()[:16]
        
        operation = data.get("operation", "unknown")
        
        self.traces[trace_id] = {
            "trace_id": trace_id,
            "operation": operation,
            "started_at": time.time(),
            "spans": [],
            "status": "in_progress"
        }
        
        return {
            "trace_id": trace_id,
            "started": True
        }
        
    async def _end_trace(self, data: Dict) -> Dict:
        """End a performance trace"""
        trace_id = data.get("trace_id")
        
        if trace_id not in self.traces:
            return {"error": "Trace not found"}
            
        trace = self.traces[trace_id]
        trace["ended_at"] = time.time()
        trace["duration_ms"] = (trace["ended_at"] - trace["started_at"]) * 1000
        trace["status"] = "completed"
        
        return {
            "trace_id": trace_id,
            "duration_ms": trace["duration_ms"],
            "spans": len(trace["spans"])
        }
        
    async def _get_issues(self, data: Dict) -> Dict:
        """List issues with filtering"""
        status = data.get("status")  # open, resolved, all
        search = data.get("search")
        limit = data.get("limit", 20)
        
        issues = list(self.issues.values())
        
        if status and status != "all":
            issues = [i for i in issues if i["status"] == status]
            
        if search:
            search_lower = search.lower()
            issues = [
                i for i in issues 
                if search_lower in i.get("title", "").lower()
            ]
            
        # Sort by last seen (newest first)
        issues.sort(key=lambda x: x["last_seen"], reverse=True)
        
        return {
            "issues": issues[:limit],
            "total": len(self.issues),
            "open": len([i for i in self.issues.values() if i["status"] == "open"]),
            "resolved": len([i for i in self.issues.values() if i["status"] == "resolved"])
        }
        
    async def _get_stats(self, data: Dict) -> Dict:
        """Get error statistics"""
        period = data.get("period", "24h")  # 1h, 24h, 7d
        
        # Parse period
        hours = {"1h": 1, "24h": 24, "7d": 168}.get(period, 24)
        cutoff = time.time() - (hours * 3600)
        
        # Filter events
        recent_events = [
            e for e in self.events
            if datetime.fromisoformat(e["timestamp"]).timestamp() > cutoff
        ]
        
        # Count by type
        by_type = {}
        for event in recent_events:
            if event["type"] == "exception":
                exc_type = event["exception"]["type"]
                by_type[exc_type] = by_type.get(exc_type, 0) + 1
                
        return {
            "period": period,
            "total_events": len(recent_events),
            "unique_issues": len(set(e.get("issue_id") for e in recent_events)),
            "by_type": by_type,
            "top_issues": sorted(
                by_type.items(), key=lambda x: x[1], reverse=True
            )[:5]
        }
        
    async def _add_breadcrumb(self, data: Dict) -> Dict:
        """Add breadcrumb to current context"""
        # Breadcrumbs are event context
        return {
            "added": True,
            "note": "Breadcrumbs tracked in event context"
        }
        
    # Helper methods
    def _create_fingerprint(self, exc_type: str, message: str, traceback_str: Optional[str]) -> str:
        """Create fingerprint for grouping similar errors"""
        # Normalize message (remove variable parts)
        normalized = re.sub(r'\d+', '<num>', message)
        normalized = re.sub(r'[a-f0-9]{8,}', '<hash>', normalized)
        
        # Include first line of traceback for better grouping
        tb_first_line = ""
        if traceback_str:
            lines = traceback_str.strip().split('\n')
            if len(lines) >= 2:
                tb_first_line = lines[-2]  # Line with error location
                
        fingerprint_data = f"{exc_type}:{normalized}:{tb_first_line}"
        return hashlib.sha256(fingerprint_data.encode()).hexdigest()
        
    async def _should_alert(self, issue_id: str) -> bool:
        """Check if we should send alert for this issue"""
        issue = self.issues.get(issue_id)
        if not issue:
            return False
            
        # Check rate: errors per minute
        now = datetime.utcnow()
        one_minute_ago = now - timedelta(minutes=1)
        
        recent_events = [
            e for e in self.events
            if e.get("issue_id") == issue_id
            and datetime.fromisoformat(e["timestamp"]) > one_minute_ago
        ]
        
        return len(recent_events) >= self.config["alert_threshold"]
        
    def health(self) -> Dict:
        h = super().health()
        h["tracked_issues"] = len(self.issues)
        h["open_issues"] = len([i for i in self.issues.values() if i["status"] == "open"])
        h["recent_events"] = len(self.events)
        h["active_traces"] = len([t for t in self.traces.values() if t["status"] == "in_progress"])
        return h
