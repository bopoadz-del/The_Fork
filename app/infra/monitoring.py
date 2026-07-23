"""Platform observability utilities and provider monitoring block."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_sentry_enabled = False
_structured_logging_enabled = False

# Dead-tunnel / DNS / connect failures when calling local Ollama or cloud LLM APIs.
_LLM_TRANSPORT_MARKERS = (
    "name or service not known",
    "errno -2",
    "gaierror",
    "not reachable at",
    "connecterror",
    "connection refused",
    "failed to resolve",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "ollama not reachable",
    "ollama request timed out",
)


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log line for Render/log-drain ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        ctx_rid = request_id_ctx.get()
        if ctx_rid:
            payload["request_id"] = ctx_rid
        for key in (
            "request_id",
            "path",
            "method",
            "event",
            "block",
            "duration_ms",
            "provider",
            "failure_class",
        ):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_structured_logging() -> bool:
    """Enable JSON logging in production (or when STRUCTURED_LOGS=true)."""
    global _structured_logging_enabled
    env = os.getenv("ENV", os.getenv("ENVIRONMENT", "production")).strip().lower()
    mode = os.getenv("STRUCTURED_LOGS", "auto").strip().lower()
    use_json = mode == "true" or (mode == "auto" and env in ("prod", "production"))
    if not use_json:
        _structured_logging_enabled = False
        return False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    _structured_logging_enabled = True
    return True


def structured_logging_enabled() -> bool:
    return _structured_logging_enabled


def init_sentry() -> bool:
    """Initialize Sentry when SENTRY_DSN is set. No-op otherwise."""
    global _sentry_enabled
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        _sentry_enabled = False
        return False

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        environment=os.getenv("ENV", os.getenv("ENVIRONMENT", "production")),
        send_default_pii=False,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or 0),
    )
    _sentry_enabled = True
    return True


def sentry_enabled() -> bool:
    return _sentry_enabled


def get_request_id() -> str:
    rid = request_id_ctx.get()
    if rid:
        return rid
    rid = str(uuid.uuid4())[:12]
    request_id_ctx.set(rid)
    return rid


def current_ollama_url() -> str:
    return os.getenv("OLLAMA_URL", "http://localhost:11434")


def is_llm_transport_failure(message: str) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _LLM_TRANSPORT_MARKERS)


def capture_llm_transport_failure(
    error_message: str,
    *,
    request_id: Optional[str] = None,
    path: Optional[str] = None,
    provider: str = "ollama",
) -> Optional[str]:
    """Report dead-tunnel / DNS / connect LLM failures to Sentry with endpoint context."""
    if not is_llm_transport_failure(error_message):
        return None

    rid = request_id or get_request_id()
    endpoint_ctx = {
        "OLLAMA_URL": current_ollama_url(),
        "LOCAL_LLM_MODEL": os.getenv("LOCAL_LLM_MODEL", ""),
    }
    logger.error(
        "llm_transport_failure",
        extra={
            "request_id": rid,
            "path": path,
            "provider": provider,
            "failure_class": "llm_transport",
            "event": "llm_transport_failure",
        },
    )

    if not _sentry_enabled:
        return None

    import sentry_sdk

    with sentry_sdk.push_scope() as scope:
        scope.set_tag("failure_class", "llm_transport")
        scope.set_tag("provider", provider)
        scope.set_tag("request_id", rid)
        if path:
            scope.set_tag("http_path", path)
        scope.set_context("llm_endpoint", endpoint_ctx)
        return sentry_sdk.capture_message(
            f"LLM transport failure ({provider}): {error_message[:500]}",
            level="error",
        )


class BlockMetricsRegistry:
    """In-process rolling stats from UniversalBlock.execute() timings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[str, Any]] = {}

    def record(self, block_name: str, duration_ms: int, status: str) -> None:
        if not block_name:
            return
        with self._lock:
            entry = self._stats.setdefault(
                block_name,
                {
                    "execution_count": 0,
                    "total_ms": 0,
                    "error_count": 0,
                    "last_ms": 0,
                    "last_status": status,
                },
            )
            entry["execution_count"] += 1
            entry["total_ms"] += duration_ms
            entry["last_ms"] = duration_ms
            entry["last_status"] = status
            if status == "error":
                entry["error_count"] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            blocks: Dict[str, Dict[str, Any]] = {}
            for name, entry in self._stats.items():
                count = entry["execution_count"]
                blocks[name] = {
                    "execution_count": count,
                    "avg_ms": round(entry["total_ms"] / count, 2) if count else 0.0,
                    "last_ms": entry["last_ms"],
                    "error_count": entry["error_count"],
                    "last_status": entry["last_status"],
                }
            return {
                "blocks": blocks,
                "tracked_blocks": len(blocks),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


block_metrics = BlockMetricsRegistry()


def record_block_execution(block_name: str, duration_ms: int, status: str) -> None:
    try:
        block_metrics.record(block_name, duration_ms, status)
    except Exception:
        logger.debug("block_metrics.record failed for %s", block_name, exc_info=True)


def get_observability_health_payload() -> Dict[str, Any]:
    snap = block_metrics.snapshot()
    return {
        "observability": {
            "sentry_enabled": sentry_enabled(),
            "structured_logging": structured_logging_enabled(),
            "request_tracing": True,
        },
        "block_metrics": snap["blocks"],
    }


async def observability_middleware(request: Request, call_next) -> Response:
    """Assign/propagate request_id and emit structured access logs.

    PR #98: also bumps the Prometheus request counter so the new
    ``/metrics`` endpoint exposes survivable per-method/per-status
    request totals (the in-memory ``block_metrics`` resets on restart
    + per worker; this counter is the same per-process limitation but
    is intended as the seed for cumulative scraping).
    """
    incoming = request.headers.get("X-Request-ID", "").strip()
    rid = incoming or str(uuid.uuid4())[:12]
    token = request_id_ctx.set(rid)
    request.state.request_id = rid
    start = datetime.now(timezone.utc)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        _bump_prometheus_request_counter(request.method, response.status_code)
        return response
    except Exception:
        _bump_prometheus_request_counter(request.method, 500)
        logger.exception(
            "request_failed",
            extra={
                "request_id": rid,
                "path": request.url.path,
                "method": request.method,
                "event": "request_failed",
            },
        )
        raise
    finally:
        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(
            "request_complete",
            extra={
                "request_id": rid,
                "path": request.url.path,
                "method": request.method,
                "duration_ms": duration_ms,
                "event": "request_complete",
            },
        )
        request_id_ctx.reset(token)


# ── Prometheus counter (PR #98) ───────────────────────────────────────────
# Lazy import so the module stays importable in environments where
# prometheus-client isn't installed (e.g. a minimal CI tier). When the
# library is present, every request increments by (method, status).
_PROM_REQUESTS_TOTAL = None


def _bump_prometheus_request_counter(method: str, status: int) -> None:
    global _PROM_REQUESTS_TOTAL
    if _PROM_REQUESTS_TOTAL is None:
        try:
            from prometheus_client import Counter
            _PROM_REQUESTS_TOTAL = Counter(
                "the_fork_requests_total",
                "Total HTTP requests handled by the FastAPI app",
                ["method", "status"],
            )
        except Exception:
            return
    try:
        _PROM_REQUESTS_TOTAL.labels(method=method, status=str(status)).inc()
    except Exception:
        pass


# ── Provider monitoring block (Lego layer) ────────────────────────────────

from app.infra.lego_base import LegoBlock
from typing import Any, Dict, List, Optional
import time
import statistics
from collections import deque, defaultdict
from datetime import datetime, timedelta, timezone
import json

class MonitoringBlock(LegoBlock):
    """
    Monitoring & Provider Leaderboard Block
    Tracks reliability scores, latency, auto-routes based on performance
    """
    
    name = "monitoring"
    version = "1.0.0"
    requires = ["config", "memory"]  # Uses Memory Block for metrics storage
    layer = 2  # Monitoring layer
    tags = ["monitoring", "observability", "core"]
    default_config = {
        "track_providers": ["deepseek", "groq", "openai", "anthropic"],
        "window_size": 100,
        "prediction_threshold": 0.3
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.memory_block = None  # Wired by assembler
        
        # Provider tracking
        self.providers = {
            "deepseek": {"name": "DeepSeek", "type": "cloud", "region": "global"},
            "groq": {"name": "Groq", "type": "cloud", "region": "us"},
            "openai": {"name": "OpenAI", "type": "cloud", "region": "global"},
            "anthropic": {"name": "Anthropic", "type": "cloud", "region": "us"},
            "local_ollama": {"name": "Ollama (Local)", "type": "edge", "region": "local"}
        }
        
        # Metrics window (last 100 calls per provider)
        self.metrics_window = 100
        self.latency_history = defaultdict(lambda: deque(maxlen=self.metrics_window))
        self.error_history = defaultdict(lambda: deque(maxlen=self.metrics_window))
        self.uptime_history = defaultdict(lambda: deque(maxlen=self.metrics_window))
        
        # Reliability scores (0-100)
        self.reliability_scores = {p: 100.0 for p in self.providers}
        
        # Auto-routing threshold
        self.degraded_threshold = 70  # Below 70% = avoid
        self.critical_threshold = 40   # Below 40% = emergency only
        
        self.last_leaderboard_update = 0
        self.leaderboard_cache = None
    
    async def initialize(self):
        """Initialize monitoring"""
        print("[monitoring] Monitoring Block initialized")
        print("   Tracking:", list(self.providers.keys()))
        print("   Auto-route thresholds: Degraded <70%, Critical <40%")
        return True
    
    async def execute(self, input_data: Dict) -> Dict:
        """Monitoring operations"""
        action = input_data.get("action")
        
        if action == "record_call":
            return await self._record_call(input_data)
        elif action == "leaderboard":
            return await self._get_leaderboard()
        elif action == "provider_status":
            return await self._get_provider_status(input_data.get("provider"))
        elif action == "recommend":
            return await self._recommend_provider()
        elif action == "health_report":
            return await self._health_report()
        elif action == "predictive_failover":
            return await self._predictive_analysis()
        
        return {"error": f"Unknown action: {action}"}
    
    async def _record_call(self, data: Dict) -> Dict:
        """Record API call metrics"""
        provider = data.get("provider")
        latency_ms = data.get("latency_ms", 0)
        success = data.get("success", True)
        error_type = data.get("error_type")
        timestamp = time.time()
        
        if provider not in self.providers:
            return {"error": "Unknown provider"}
        
        # Store in memory block for persistence
        if self.memory_block:
            await self.memory_block.execute({
                "action": "set",
                "key": f"metrics:{provider}:{int(timestamp)}",
                "value": {
                    "latency": latency_ms,
                    "success": success,
                    "error": error_type,
                    "timestamp": timestamp
                },
                "ttl": 86400  # 24 hour retention
            })
        
        # Update in-memory windows
        self.latency_history[provider].append(latency_ms)
        self.error_history[provider].append(0 if success else 1)
        self.uptime_history[provider].append(1 if success else 0)
        
        # Recalculate reliability score
        await self._calculate_reliability(provider)
        
        return {"recorded": True, "provider": provider}
    
    async def _calculate_reliability(self, provider: str):
        """Calculate provider reliability score (0-100)"""
        if not self.error_history[provider]:
            return
        
        # Error rate (weight: 50%)
        error_rate = sum(self.error_history[provider]) / len(self.error_history[provider])
        
        # Latency score (weight: 30%) - optimal is <500ms
        avg_latency = statistics.mean(self.latency_history[provider]) if self.latency_history[provider] else 0
        latency_score = max(0, 100 - (avg_latency / 10))  # 1000ms = 0 score
        
        # Uptime (weight: 20%)
        uptime_rate = statistics.mean(self.uptime_history[provider]) if self.uptime_history[provider] else 1
        
        # Composite score
        reliability = (uptime_rate * 50) + (latency_score * 0.30) + ((1 - error_rate) * 20)
        self.reliability_scores[provider] = round(reliability, 2)
        
        # Invalidate leaderboard cache
        self.leaderboard_cache = None
    
    async def _get_leaderboard(self) -> Dict:
        """Get provider leaderboard - ranked by reliability"""
        # Check cache (refresh every 60s)
        if self.leaderboard_cache and (time.time() - self.last_leaderboard_update) < 60:
            return self.leaderboard_cache
        
        leaderboard = []
        
        for provider_id, info in self.providers.items():
            score = self.reliability_scores[provider_id]
            latencies = list(self.latency_history[provider_id])
            errors = list(self.error_history[provider_id])
            
            # Determine status
            if score >= 90:
                status = "excellent"
                color = "green"
            elif score >= 70:
                status = "good"
                color = "yellow"
            elif score >= 40:
                status = "degraded"
                color = "orange"
            else:
                status = "critical"
                color = "red"
            
            leaderboard.append({
                "rank": 0,  # Set later
                "provider": provider_id,
                "name": info["name"],
                "type": info["type"],
                "region": info["region"],
                "reliability_score": score,
                "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
                "error_rate_percent": round(sum(errors) / len(errors) * 100, 2) if errors else 0,
                "total_calls": len(latencies),
                "status": status,
                "color": color,
                "recommendation": "use" if score >= 70 else "avoid" if score >= 40 else "emergency_only"
            })
        
        # Sort by reliability score (descending)
        leaderboard.sort(key=lambda x: x["reliability_score"], reverse=True)
        
        # Assign ranks
        for i, entry in enumerate(leaderboard):
            entry["rank"] = i + 1
        
        # Cache result
        self.leaderboard_cache = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "leaderboard": leaderboard,
            "top_provider": leaderboard[0]["provider"] if leaderboard else None,
            "auto_route_enabled": True
        }
        self.last_leaderboard_update = time.time()
        
        return self.leaderboard_cache
    
    async def _recommend_provider(self) -> Dict:
        """AI-powered provider recommendation based on current conditions"""
        leaderboard = (await self._get_leaderboard())["leaderboard"]
        
        if not leaderboard:
            return {"recommendation": "local_ollama", "reason": "no_data"}
        
        # Find best available provider
        for entry in leaderboard:
            if entry["recommendation"] == "use":
                return {
                    "recommended": entry["provider"],
                    "confidence": entry["reliability_score"],
                    "reason": f"Best reliability at {entry['avg_latency_ms']}ms avg latency",
                    "fallback_sequence": [e["provider"] for e in leaderboard if e["recommendation"] in ["use", "avoid"]]
                }
        
        # If all degraded, use local
        return {
            "recommended": "local_ollama",
            "confidence": 100.0,
            "reason": "All cloud providers degraded - using edge fallback",
            "emergency_mode": True
        }
    
    async def _predictive_analysis(self) -> Dict:
        """Predict potential failures before they happen"""
        predictions = []
        
        for provider in self.providers:
            latencies = list(self.latency_history[provider])
            
            if len(latencies) >= 10:
                # Check for degrading trend (increasing latency)
                recent_avg = statistics.mean(latencies[-5:])
                older_avg = statistics.mean(latencies[-10:-5])
                
                if recent_avg > older_avg * 1.5:  # 50% increase
                    predictions.append({
                        "provider": provider,
                        "prediction": "latency_spike",
                        "severity": "medium",
                        "recommendation": f"Preemptively reroute {provider} traffic",
                        "confidence": 75
                    })
                
                # Check error rate trend
                recent_errors = sum(list(self.error_history[provider])[-10:])
                if recent_errors >= 3:  # 3 errors in last 10 calls
                    predictions.append({
                        "provider": provider,
                        "prediction": "impending_failure",
                        "severity": "high",
                        "recommendation": f"Switch {provider} to fallback immediately",
                        "confidence": 85
                    })
        
        return {
            "predictions": predictions,
            "preventive_actions_recommended": len(predictions) > 0,
            "analyzed_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def _health_report(self) -> Dict:
        """Full system health report"""
        lb = await self._get_leaderboard()
        pred = await self._predictive_analysis()
        
        return {
            "overall_status": "healthy" if all(e["recommendation"] == "use" for e in lb["leaderboard"][:2]) else "degraded",
            "leaderboard": lb,
            "predictions": pred,
            "failover_readiness": "ready",
            "recommendation": await self._recommend_provider()
        }
    
    async def _get_provider_status(self, provider: Optional[str]) -> Dict:
        """Get specific provider status"""
        if not provider:
            return {"providers": {p: self.reliability_scores[p] for p in self.providers}}
        
        if provider not in self.providers:
            return {"error": "Unknown provider"}
        
        return {
            "provider": provider,
            "reliability_score": self.reliability_scores[provider],
            "metrics": {
                "latency_history": list(self.latency_history[provider])[-10:],
                "error_history": list(self.error_history[provider])[-10:]
            }
        }
    
    def health(self) -> Dict:
        h = super().health()
        h["providers_tracked"] = len(self.providers)
        h["metrics_retention"] = f"{self.metrics_window} calls per provider"
        h["auto_route_enabled"] = True
        return h
