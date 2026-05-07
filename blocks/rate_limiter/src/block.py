"""Rate Limiter Block - Advanced rate limiting beyond Auth block

Features:
- Sliding window, token bucket, leaky bucket algorithms
- Per-endpoint, per-IP, per-User, per-Team limits
- Burst handling and custom limits for premium users
"""

from blocks.base import LegoBlock
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import time
from enum import Enum
import asyncio


class RateLimitStrategy(Enum):
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    LEAKY_BUCKET = "leaky_bucket"
    FIXED_WINDOW = "fixed_window"


class RateLimiterBlock(LegoBlock):
    """
    Advanced rate limiting beyond Auth block.
    Per-endpoint, per-IP, per-team, burst handling.
    """
    name = "rate_limiter"
    version = "1.0.0"
    requires = ["memory", "database"]
    layer = 1  # Security layer
    tags = ["security", "protection", "infra", "rate_limiting"]
    
    default_config = {
        "strategy": "sliding_window",  # sliding_window, token_bucket, leaky_bucket
        "default_limit": 1000,  # requests per window
        "default_window": 3600,  # seconds (1 hour)
        "burst_allowance": 10,  # burst over limit
        "cleanup_interval": 300  # cleanup expired counters every 5 min
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.counters: Dict[str, Dict] = {}  # key -> counter data
        self.buckets: Dict[str, Dict] = {}  # token bucket storage
        self.custom_limits: Dict[str, Dict] = {}  # key -> custom limit config
        
    async def initialize(self) -> bool:
        """Initialize rate limiter"""
        print("⏱️  Rate Limiter Block initializing...")
        print(f"   Strategy: {self.config['strategy']}")
        print(f"   Default: {self.config['default_limit']} req/{self.config['default_window']}s")
        
        # Start cleanup task
        asyncio.create_task(self._cleanup_expired())
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute rate limiting actions"""
        action = input_data.get("action")
        
        actions = {
            "check_limit": self._check_limit,
            "record_hit": self._record_hit,
            "set_custom_limit": self._set_custom_limit,
            "get_usage_stats": self._usage_stats,
            "reset_limit": self._reset_limit,
            "get_limit_info": self._get_limit_info
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _check_limit(self, data: Dict) -> Dict:
        """Check if request is within rate limit"""
        key = data.get("key")  # user_id or IP or team_id
        resource = data.get("resource", "default")  # endpoint name
        cost = data.get("cost", 1)  # Some requests cost more
        
        if not key:
            return {"error": "key required (user_id, IP, or team_id)"}
            
        # Build composite key
        counter_key = f"{key}:{resource}"
        
        # Get limit config
        limit_config = self.custom_limits.get(key, {
            "limit": self.config["default_limit"],
            "window": self.config["default_window"],
            "burst": self.config["burst_allowance"]
        })
        
        strategy = self.config["strategy"]
        
        if strategy == "sliding_window":
            return await self._check_sliding_window(counter_key, cost, limit_config)
        elif strategy == "token_bucket":
            return await self._check_token_bucket(counter_key, cost, limit_config)
        elif strategy == "leaky_bucket":
            return await self._check_leaky_bucket(counter_key, cost, limit_config)
        else:
            return await self._check_fixed_window(counter_key, cost, limit_config)
            
    async def _check_sliding_window(self, key: str, cost: int, config: Dict) -> Dict:
        """Sliding window rate limiting"""
        now = time.time()
        window = config["window"]
        limit = config["limit"]
        burst = config.get("burst", 0)
        
        # Get or create counter
        if key not in self.counters:
            self.counters[key] = {
                "requests": [],
                "window_start": now
            }
            
        counter = self.counters[key]
        
        # Remove old requests outside window
        cutoff = now - window
        counter["requests"] = [r for r in counter["requests"] if r > cutoff]
        
        # Count current requests in window
        current_count = len(counter["requests"])
        effective_limit = limit + burst
        
        allowed = (current_count + cost) <= effective_limit
        remaining = max(0, effective_limit - current_count - cost)
        reset_time = int(now + window)
        
        return {
            "allowed": allowed,
            "limit": limit,
            "burst": burst,
            "remaining": remaining,
            "reset_at": reset_time,
            "retry_after": 0 if allowed else int(window - (now - counter["requests"][0])) if counter["requests"] else window,
            "strategy": "sliding_window"
        }
        
    async def _check_token_bucket(self, key: str, cost: int, config: Dict) -> Dict:
        """Token bucket rate limiting"""
        now = time.time()
        limit = config["limit"]
        window = config["window"]
        
        # Tokens per second
        refill_rate = limit / window
        
        # Get or create bucket
        if key not in self.buckets:
            self.buckets[key] = {
                "tokens": limit,
                "last_update": now
            }
            
        bucket = self.buckets[key]
        
        # Refill tokens
        elapsed = now - bucket["last_update"]
        bucket["tokens"] = min(limit, bucket["tokens"] + elapsed * refill_rate)
        bucket["last_update"] = now
        
        # Check if enough tokens
        allowed = bucket["tokens"] >= cost
        
        if allowed:
            bucket["tokens"] -= cost
            
        remaining = int(bucket["tokens"])
        
        # Calculate time to get enough tokens
        tokens_needed = cost - bucket["tokens"] if not allowed else 0
        retry_after = int(tokens_needed / refill_rate) if tokens_needed > 0 else 0
        
        return {
            "allowed": allowed,
            "limit": limit,
            "remaining": remaining,
            "reset_at": int(now + (limit - bucket["tokens"]) / refill_rate),
            "retry_after": retry_after,
            "strategy": "token_bucket"
        }
        
    async def _check_leaky_bucket(self, key: str, cost: int, config: Dict) -> Dict:
        """Leaky bucket rate limiting"""
        # Similar to token bucket but queue-based
        # For simplicity, use token bucket logic
        return await self._check_token_bucket(key, cost, config)
        
    async def _check_fixed_window(self, key: str, cost: int, config: Dict) -> Dict:
        """Fixed window rate limiting"""
        now = time.time()
        window = config["window"]
        limit = config["limit"]
        
        # Current window
        window_key = f"{key}:{int(now / window)}"
        
        if window_key not in self.counters:
            self.counters[window_key] = {
                "count": 0,
                "window_start": int(now / window) * window
            }
            
        counter = self.counters[window_key]
        
        allowed = (counter["count"] + cost) <= limit
        
        if allowed:
            counter["count"] += cost
            
        remaining = max(0, limit - counter["count"])
        reset_time = counter["window_start"] + window
        
        return {
            "allowed": allowed,
            "limit": limit,
            "remaining": remaining,
            "reset_at": reset_time,
            "retry_after": max(0, reset_time - int(now)) if not allowed else 0,
            "strategy": "fixed_window"
        }
        
    async def _record_hit(self, data: Dict) -> Dict:
        """Record a request hit for rate limiting"""
        key = data.get("key")
        resource = data.get("resource", "default")
        timestamp = data.get("timestamp", time.time())
        
        if not key:
            return {"error": "key required"}
            
        counter_key = f"{key}:{resource}"
        
        # Add to sliding window counter
        if counter_key not in self.counters:
            self.counters[counter_key] = {
                "requests": [],
                "window_start": timestamp
            }
            
        self.counters[counter_key]["requests"].append(timestamp)
        
        return {
            "recorded": True,
            "key": counter_key,
            "timestamp": timestamp
        }
        
    async def _set_custom_limit(self, data: Dict) -> Dict:
        """Set custom rate limit for a key (e.g., premium users)"""
        key = data.get("key")
        limit = data.get("limit")
        window = data.get("window")
        burst = data.get("burst", 0)
        
        if not key or limit is None:
            return {"error": "key and limit required"}
            
        self.custom_limits[key] = {
            "limit": limit,
            "window": window or self.config["default_window"],
            "burst": burst,
            "set_at": datetime.utcnow().isoformat()
        }
        
        return {
            "set": True,
            "key": key,
            "limit": limit,
            "window": window,
            "burst": burst
        }
        
    async def _usage_stats(self, data: Dict) -> Dict:
        """Get usage statistics"""
        key = data.get("key")
        
        if key:
            # Stats for specific key
            counters = {k: v for k, v in self.counters.items() if k.startswith(f"{key}:")}
        else:
            # Global stats
            counters = self.counters
            
        total_requests = sum(
            len(c.get("requests", [])) for c in counters.values()
        )
        
        return {
            "tracked_keys": len(counters),
            "total_requests_tracked": total_requests,
            "custom_limits_set": len(self.custom_limits),
            "strategy": self.config["strategy"]
        }
        
    async def _reset_limit(self, data: Dict) -> Dict:
        """Reset rate limit counter for a key"""
        key = data.get("key")
        resource = data.get("resource")
        
        if resource:
            counter_key = f"{key}:{resource}"
            if counter_key in self.counters:
                del self.counters[counter_key]
            if counter_key in self.buckets:
                del self.buckets[counter_key]
        else:
            # Reset all for key
            for k in list(self.counters.keys()):
                if k.startswith(f"{key}:"):
                    del self.counters[k]
            for k in list(self.buckets.keys()):
                if k.startswith(f"{key}:"):
                    del self.buckets[k]
                    
        return {
            "reset": True,
            "key": key,
            "resource": resource
        }
        
    async def _get_limit_info(self, data: Dict) -> Dict:
        """Get current limit information for a key"""
        key = data.get("key")
        resource = data.get("resource", "default")
        
        counter_key = f"{key}:{resource}"
        
        # Check if custom limit exists
        custom = self.custom_limits.get(key)
        
        # Get current counter status
        check_result = await self._check_limit({
            "key": key,
            "resource": resource
        })
        
        return {
            "key": key,
            "resource": resource,
            "has_custom_limit": custom is not None,
            "custom_limit": custom,
            "current_status": check_result
        }
        
    async def _cleanup_expired(self):
        """Background task to clean up expired counters"""
        while True:
            await asyncio.sleep(self.config["cleanup_interval"])
            
            now = time.time()
            window = self.config["default_window"]
            cutoff = now - (window * 2)  # Keep 2x window for safety
            
            # Clean old counters
            for key in list(self.counters.keys()):
                counter = self.counters[key]
                if isinstance(counter.get("requests"), list):
                    counter["requests"] = [r for r in counter["requests"] if r > cutoff]
                    if not counter["requests"]:
                        del self.counters[key]
                elif counter.get("window_start", 0) < cutoff:
                    del self.counters[key]
                    
    def health(self) -> Dict:
        h = super().health()
        h["strategy"] = self.config["strategy"]
        h["tracked_counters"] = len(self.counters)
        h["custom_limits"] = len(self.custom_limits)
        h["token_buckets"] = len(self.buckets)
        return h
