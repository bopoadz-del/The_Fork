"""API Key Management System for Cerebrum SaaS.

Features:
- API key generation and validation
- Usage tracking (requests, tokens, timestamps)
- Rate limiting per key
- Stripe integration for billing
- Tier-based access control
"""

import os
import secrets
import hashlib
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum
import sqlite3
import json

import httpx
from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


class Tier(Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


TIER_LIMITS = {
    Tier.FREE: {
        "requests_per_month": 1000,
        "tokens_per_month": 100000,
        "rate_limit_per_minute": 20,
        "blocks_allowed": ["chat", "pdf", "ocr", "voice", "translate"],
    },
    Tier.PRO: {
        "requests_per_month": 50000,
        "tokens_per_month": 5000000,
        "rate_limit_per_minute": 120,
        "blocks_allowed": "all",
    },
    Tier.ENTERPRISE: {
        "requests_per_month": float('inf'),
        "tokens_per_month": float('inf'),
        "rate_limit_per_minute": 1000,
        "blocks_allowed": "all",
    },
}


class APIKeyManager:
    """Manages API keys, usage tracking, and billing."""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(
            os.getenv("DATA_DIR", "/app/data"), 
            "api_keys.db"
        )
        self._init_db()
        self._rate_limit_cache: Dict[str, List[datetime]] = {}
        self.stripe_secret = os.getenv("STRIPE_SECRET_KEY")
    
    def _init_db(self):
        """Initialize SQLite database with required tables."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # API keys table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                key_hash TEXT UNIQUE NOT NULL,
                key_prefix TEXT NOT NULL,
                name TEXT,
                email TEXT,
                tier TEXT DEFAULT 'free',
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                last_used_at TIMESTAMP,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                metadata TEXT
            )
        """)
        
        # Usage tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_id TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                block_name TEXT,
                tokens_used INTEGER DEFAULT 0,
                request_size_bytes INTEGER DEFAULT 0,
                response_time_ms INTEGER,
                status_code INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (key_id) REFERENCES api_keys(id)
            )
        """)
        
        # Monthly usage aggregation
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monthly_usage (
                key_id TEXT NOT NULL,
                year_month TEXT NOT NULL,
                request_count INTEGER DEFAULT 0,
                token_count INTEGER DEFAULT 0,
                PRIMARY KEY (key_id, year_month),
                FOREIGN KEY (key_id) REFERENCES api_keys(id)
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_key ON usage_logs(key_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_logs(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_key_hash ON api_keys(key_hash)")
        
        conn.commit()
        conn.close()
    
    def _hash_key(self, key: str) -> str:
        """Hash an API key for storage."""
        return hashlib.sha256(key.encode()).hexdigest()
    
    def generate_key(self, name: Optional[str] = None, email: Optional[str] = None, 
                     tier: Tier = Tier.FREE) -> Dict[str, str]:
        """Generate a new API key."""
        # Generate key with prefix
        raw_key = secrets.token_urlsafe(32)
        key_id = secrets.token_hex(16)
        prefix = "cb_"
        full_key = f"{prefix}{raw_key}"
        
        # Hash for storage
        key_hash = self._hash_key(full_key)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO api_keys (id, key_hash, key_prefix, name, email, tier, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (key_id, key_hash, prefix, name, email, tier.value, 
              json.dumps({"generated_by": "api"})))
        
        conn.commit()
        conn.close()
        
        return {
            "key": full_key,
            "key_id": key_id,
            "prefix": prefix,
            "tier": tier.value,
            "message": "Save this key - it won't be shown again!"
        }
    
    async def validate_key(self, key: str) -> Optional[Dict[str, Any]]:
        """Validate an API key and return key data if valid."""
        if not key.startswith("cb_"):
            return None
        
        key_hash = self._hash_key(key)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, email, tier, active, created_at, expires_at, stripe_customer_id
            FROM api_keys WHERE key_hash = ?
        """, (key_hash,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        key_id, name, email, tier_str, active, created, expires, stripe_id = row
        
        if not active:
            return None
        
        if expires and datetime.fromisoformat(expires) < datetime.utcnow():
            return None
        
        return {
            "id": key_id,
            "name": name,
            "email": email,
            "tier": Tier(tier_str),
            "created_at": created,
            "stripe_customer_id": stripe_id,
        }
    
    async def check_rate_limit(self, key_id: str, tier: Tier) -> bool:
        """Check if key is within rate limits. Returns True if allowed."""
        limits = TIER_LIMITS[tier]
        max_requests = limits["rate_limit_per_minute"]
        
        now = datetime.utcnow()
        window_start = now - timedelta(minutes=1)
        
        # Clean old entries and add current request
        if key_id not in self._rate_limit_cache:
            self._rate_limit_cache[key_id] = []
        
        self._rate_limit_cache[key_id] = [
            t for t in self._rate_limit_cache[key_id] if t > window_start
        ]
        
        if len(self._rate_limit_cache[key_id]) >= max_requests:
            return False
        
        self._rate_limit_cache[key_id].append(now)
        return True
    
    async def check_usage_limits(self, key_id: str, tier: Tier, 
                                  tokens: int = 0) -> Dict[str, Any]:
        """Check if key is within monthly usage limits."""
        limits = TIER_LIMITS[tier]
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        year_month = datetime.utcnow().strftime("%Y-%m")
        
        cursor.execute("""
            SELECT request_count, token_count FROM monthly_usage
            WHERE key_id = ? AND year_month = ?
        """, (key_id, year_month))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            current_requests, current_tokens = row
        else:
            current_requests, current_tokens = 0, 0
        
        max_requests = limits["requests_per_month"]
        max_tokens = limits["tokens_per_month"]
        
        result = {
            "allowed": True,
            "requests_used": current_requests,
            "requests_limit": max_requests if max_requests != float('inf') else -1,
            "tokens_used": current_tokens,
            "tokens_limit": max_tokens if max_tokens != float('inf') else -1,
        }
        
        if max_requests != float('inf') and current_requests >= max_requests:
            result["allowed"] = False
            result["reason"] = "Monthly request limit exceeded"
        elif max_tokens != float('inf') and current_tokens + tokens >= max_tokens:
            result["allowed"] = False
            result["reason"] = "Monthly token limit exceeded"
        
        return result
    
    async def log_usage(self, key_id: str, endpoint: str, 
                        block_name: Optional[str] = None,
                        tokens: int = 0, 
                        response_time_ms: int = 0,
                        status_code: int = 200,
                        request_size: int = 0):
        """Log API usage for a key."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Log detailed usage
        cursor.execute("""
            INSERT INTO usage_logs 
            (key_id, endpoint, block_name, tokens_used, request_size_bytes, 
             response_time_ms, status_code)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (key_id, endpoint, block_name, tokens, request_size, 
              response_time_ms, status_code))
        
        # Update monthly aggregation
        year_month = datetime.utcnow().strftime("%Y-%m")
        
        cursor.execute("""
            INSERT INTO monthly_usage (key_id, year_month, request_count, token_count)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(key_id, year_month) DO UPDATE SET
                request_count = request_count + 1,
                token_count = token_count + excluded.token_count
        """, (key_id, year_month, tokens))
        
        # Update last used
        cursor.execute("""
            UPDATE api_keys SET last_used_at = ? WHERE id = ?
        """, (datetime.utcnow().isoformat(), key_id))
        
        conn.commit()
        conn.close()
    
    async def create_stripe_customer(self, key_id: str, email: str) -> Optional[str]:
        """Create a Stripe customer for billing."""
        if not self.stripe_secret:
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.stripe.com/v1/customers",
                    headers={"Authorization": f"Bearer {self.stripe_secret}"},
                    data={"email": email, "metadata": {"api_key_id": key_id}}
                )
                data = response.json()
                customer_id = data.get("id")
                
                if customer_id:
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE api_keys SET stripe_customer_id = ? WHERE id = ?",
                        (customer_id, key_id)
                    )
                    conn.commit()
                    conn.close()
                
                return customer_id
        except Exception as e:
            print(f"Stripe customer creation failed: {e}")
            return None
    
    async def upgrade_tier(self, key_id: str, new_tier: Tier, 
                           stripe_subscription_id: Optional[str] = None) -> bool:
        """Upgrade a key to a new tier."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE api_keys 
            SET tier = ?, stripe_subscription_id = ?
            WHERE id = ?
        """, (new_tier.value, stripe_subscription_id, key_id))
        
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        
        return success
    
    def get_usage_stats(self, key_id: str, days: int = 30) -> Dict[str, Any]:
        """Get usage statistics for a key."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        # Total requests
        cursor.execute("""
            SELECT COUNT(*), SUM(tokens_used), AVG(response_time_ms)
            FROM usage_logs WHERE key_id = ? AND timestamp > ?
        """, (key_id, since))
        
        total_row = cursor.fetchone()
        
        # Endpoint breakdown
        cursor.execute("""
            SELECT endpoint, COUNT(*) as count
            FROM usage_logs WHERE key_id = ? AND timestamp > ?
            GROUP BY endpoint ORDER BY count DESC
        """, (key_id, since))
        
        endpoints = [{"endpoint": row[0], "count": row[1]} 
                     for row in cursor.fetchall()]
        
        # Daily breakdown
        cursor.execute("""
            SELECT date(timestamp) as day, COUNT(*) as requests, SUM(tokens_used) as tokens
            FROM usage_logs WHERE key_id = ? AND timestamp > ?
            GROUP BY day ORDER BY day DESC
        """, (key_id, since))
        
        daily = [{"date": row[0], "requests": row[1], "tokens": row[2]} 
                 for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "total_requests": total_row[0] or 0,
            "total_tokens": total_row[1] or 0,
            "avg_response_time_ms": total_row[2] or 0,
            "endpoints": endpoints,
            "daily_breakdown": daily,
        }
    
    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("UPDATE api_keys SET active = 0 WHERE id = ?", (key_id,))
        
        conn.commit()
        success = cursor.rowcount > 0
        conn.close()
        
        return success
    
    def list_keys(self, email: Optional[str] = None) -> List[Dict[str, Any]]:
        """List API keys, optionally filtered by email."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if email:
            cursor.execute("""
                SELECT id, name, email, tier, active, created_at, last_used_at
                FROM api_keys WHERE email = ?
            """, (email,))
        else:
            cursor.execute("""
                SELECT id, name, email, tier, active, created_at, last_used_at
                FROM api_keys
            """)
        
        keys = []
        for row in cursor.fetchall():
            keys.append({
                "id": row[0],
                "name": row[1],
                "email": row[2],
                "tier": row[3],
                "active": bool(row[4]),
                "created_at": row[5],
                "last_used_at": row[6],
            })
        
        conn.close()
        return keys


# Global instance
_key_manager: Optional[APIKeyManager] = None


def get_key_manager() -> APIKeyManager:
    """Get or create the global API key manager."""
    global _key_manager
    if _key_manager is None:
        _key_manager = APIKeyManager()
    return _key_manager


# FastAPI dependency
security = HTTPBearer(auto_error=False)


async def require_api_key(credentials: HTTPAuthorizationCredentials = security) -> Dict[str, Any]:
    """FastAPI dependency to require and validate API key."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    key = credentials.credentials
    manager = get_key_manager()
    
    key_data = await manager.validate_key(key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    
    # Check rate limit
    if not await manager.check_rate_limit(key_data["id"], key_data["tier"]):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    # Check usage limits
    usage = await manager.check_usage_limits(key_data["id"], key_data["tier"])
    if not usage["allowed"]:
        raise HTTPException(status_code=429, detail=usage["reason"])
    
    return key_data


async def optional_api_key(credentials: HTTPAuthorizationCredentials = security) -> Optional[Dict[str, Any]]:
    """FastAPI dependency for optional API key validation."""
    if not credentials:
        return None
    
    key = credentials.credentials
    manager = get_key_manager()
    return await manager.validate_key(key)
