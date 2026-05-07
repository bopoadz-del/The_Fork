"""Security Container - Fortress walls for the Platform

Provides: Auth, Secrets, Sandbox, Audit, Rate Limiter
"""

import os
import hashlib
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from app.core.block import BaseBlock, BlockConfig


class SecurityContainer(BaseBlock):
    """
    Security Container - Authentication, secrets, sandboxing, audit
    
    The fortress walls of the Lego OS.
    """
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="security",
            version="1.0.0",
            description="Security services: Auth, Secrets, Sandbox, Audit, Rate Limiter",
            requires_api_key=False,
            supported_inputs=["auth", "secrets", "sandbox", "audit", "rate_limit"],
            supported_outputs=["authenticated", "secured", "audited", "rate_limited"]
        ,
            layer=1,
            tags=["security", "container"],
            requires=["config"]))
        
        # Auth storage
        self.api_keys: Dict[str, Dict] = {}
        self.sessions: Dict[str, Dict] = {}
        
        # Secrets vault
        self.secrets: Dict[str, str] = {}
        
        # Rate limiting
        self.rate_counters: Dict[str, Dict] = {}
        
        # Audit log
        self.audit_log: list = []
        
        # Sandbox registry
        self.sandbox_policies: Dict[str, Dict] = {}

    def _is_development_mode(self) -> bool:
        """Allow development-only shortcuts only outside production."""
        environment = os.getenv("ENV", os.getenv("ENVIRONMENT", "production")).strip().lower()
        return environment in {"dev", "development", "local", "test", "testing"}
        
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Main entry for security operations"""
        params = params or {}
        action = params.get("action", "status")
        
        if action == "auth":
            return await self._authenticate(params)
        elif action == "create_key":
            return await self._create_api_key(params)
        elif action == "revoke_key":
            return await self._revoke_key(params)
        elif action == "store_secret":
            return await self._store_secret(params)
        elif action == "get_secret":
            return await self._get_secret(params)
        elif action == "check_rate":
            return await self._check_rate_limit(params)
        elif action == "audit":
            return await self._audit_event(params)
        elif action == "sandbox_check":
            return await self._sandbox_check(params)
        elif action == "health":
            return self._health_check()
        else:
            return {"error": f"Unknown action: {action}"}
    
    # ==================== AUTH ====================
    
    async def _authenticate(self, params: Dict) -> Dict:
        """Validate API key"""
        api_key = params.get("api_key")
        
        if not api_key:
            return {"authenticated": False, "error": "No API key provided"}
        
        # Check dev key (only in development, never production)
        # In production, require real API keys from environment or database
        dev_key = os.getenv("CB_DEV_KEY", "").strip()
        if dev_key and self._is_development_mode() and api_key == dev_key:
            return {
                "authenticated": True,
                "key_id": "dev",
                "role": "admin",
                "rate_limit": 1000,
                "warning": "Development key - do not use in production"
            }
        
        # Check stored keys
        if api_key in self.api_keys:
            key_data = self.api_keys[api_key]
            
            # Check expiry
            if key_data.get("expires_at"):
                expiry = datetime.fromisoformat(key_data["expires_at"])
                if datetime.utcnow() > expiry:
                    return {"authenticated": False, "error": "API key expired"}
            
            # Update last used
            key_data["last_used"] = datetime.utcnow().isoformat()
            key_data["use_count"] = key_data.get("use_count", 0) + 1
            
            return {
                "authenticated": True,
                "key_id": key_data.get("id"),
                "role": key_data.get("role", "user"),
                "rate_limit": key_data.get("rate_limit", 100)
            }
        
        return {"authenticated": False, "error": "Invalid API key"}
    
    async def _create_api_key(self, params: Dict) -> Dict:
        """Create new API key"""
        owner = params.get("owner", "anonymous")
        role = params.get("role", "user")
        rate_limit = params.get("rate_limit", 100)
        expires_days = params.get("expires_days", 30)
        
        # Generate key
        key_hash = hashlib.sha256(f"{owner}:{time.time()}".encode()).hexdigest()[:24]
        api_key = f"cb_{key_hash}"
        
        self.api_keys[api_key] = {
            "id": key_hash,
            "owner": owner,
            "role": role,
            "rate_limit": rate_limit,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(days=expires_days)).isoformat(),
            "use_count": 0,
            "last_used": None
        }
        
        return {
            "created": True,
            "api_key": api_key,
            "role": role,
            "rate_limit": rate_limit,
            "expires_in_days": expires_days
        }
    
    async def _revoke_key(self, params: Dict) -> Dict:
        """Revoke an API key"""
        api_key = params.get("api_key")
        
        if api_key in self.api_keys:
            del self.api_keys[api_key]
            return {"revoked": True, "key_id": api_key[:8] + "..."}
        
        return {"error": "Key not found"}
    
    # ==================== SECRETS ====================
    
    async def _store_secret(self, params: Dict) -> Dict:
        """Store encrypted secret"""
        key = params.get("key")
        value = params.get("value")
        
        if not key or not value:
            return {"error": "Key and value required"}
        
        # Simple "encryption" (use real encryption in production)
        encrypted = hashlib.sha256(f"salt:{value}".encode()).hexdigest()
        self.secrets[key] = encrypted
        
        return {
            "stored": True,
            "key": key,
            "encrypted": True
        }
    
    async def _get_secret(self, params: Dict) -> Dict:
        """Retrieve secret"""
        key = params.get("key")
        
        if key in self.secrets:
            return {
                "found": True,
                "key": key,
                "value": "[ENCRYPTED]"  # Don't return actual value
            }
        
        return {"error": "Secret not found"}
    
    # ==================== RATE LIMITING ====================
    
    async def _check_rate_limit(self, params: Dict) -> Dict:
        """Check if request is within rate limit"""
        key = params.get("key", "default")
        limit = params.get("limit", 100)
        window = params.get("window", 3600)  # 1 hour
        
        now = time.time()
        
        if key not in self.rate_counters:
            self.rate_counters[key] = {
                "count": 0,
                "window_start": now
            }
        
        counter = self.rate_counters[key]
        
        # Reset window if expired
        if now - counter["window_start"] > window:
            counter["count"] = 0
            counter["window_start"] = now
        
        # Check limit
        if counter["count"] >= limit:
            reset_at = counter["window_start"] + window
            return {
                "allowed": False,
                "limit": limit,
                "remaining": 0,
                "reset_at": reset_at,
                "retry_after": int(reset_at - now)
            }
        
        # Allow and increment
        counter["count"] += 1
        
        return {
            "allowed": True,
            "limit": limit,
            "remaining": limit - counter["count"],
            "reset_at": counter["window_start"] + window
        }
    
    # ==================== AUDIT ====================
    
    async def _audit_event(self, params: Dict) -> Dict:
        """Log audit event"""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": params.get("event_action", "unknown"),
            "user": params.get("user_id", "anonymous"),
            "resource": params.get("resource", "unknown"),
            "success": params.get("success", True),
            "details": params.get("details", {})
        }
        
        self.audit_log.append(event)
        
        # Trim log if too large
        if len(self.audit_log) > 10000:
            self.audit_log = self.audit_log[-5000:]
        
        return {"logged": True, "event_id": len(self.audit_log)}
    
    # ==================== SANDBOX ====================
    
    async def _sandbox_check(self, params: Dict) -> Dict:
        """Check if code is sandbox-safe"""
        code = params.get("code", "")
        
        # Basic safety checks
        dangerous = ["exec(", "eval(", "__import__", "os.system", "subprocess"]
        violations = [d for d in dangerous if d in code]
        
        if violations:
            return {
                "safe": False,
                "violations": violations,
                "message": "Code contains dangerous operations"
            }
        
        return {
            "safe": True,
            "checks_passed": len(dangerous),
            "message": "Code passed sandbox checks"
        }
    
    # ==================== HEALTH ====================
    
    def _health_check(self) -> Dict:
        """Container health status"""
        return {
            "status": "healthy",
            "services": {
                "auth": len(self.api_keys),
                "secrets": len(self.secrets),
                "rate_counters": len(self.rate_counters),
                "audit_events": len(self.audit_log)
            },
            "version": "1.0.0"
        }
