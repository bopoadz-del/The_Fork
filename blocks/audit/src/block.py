"""Audit Block - Immutable compliance logging with blockchain-style integrity"""
from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import time
import hashlib
import json
from datetime import datetime


class AuditBlock(LegoBlock):
    """
    Audit Block - Immutable audit trail for compliance
    
    Features:
    - Append-only log (no deletes/updates)
    - Blockchain-style hash chaining for integrity
    - Tamper detection
    - Compliance exports (SOC2, GDPR, etc.)
    - Event categorization (auth, data, system)
    """
    name = "audit"
    version = "1.0.0"
    requires = ["database", "auth"]
    layer = 1  # Security layer
    tags = ["security", "compliance", "audit", "enterprise"]
    default_config = {
        "hash_algorithm": "sha256",
        "chain_verification": True,
        "retention_days": 2555,  # 7 years
        "immutable": True,  # Cannot delete/modify
        "categories": ["auth", "data_access", "system", "admin"]
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.last_hash = "0" * 64  # Genesis hash
        self.event_count = 0
        
    async def initialize(self) -> bool:
        """Initialize audit log table and load last hash"""
        print("📋 Audit Block initialized")
        print(f"   Immutable: {self.config.get('immutable', True)}")
        print(f"   Retention: {self.config.get('retention_days', 2555)} days")
        
        # Create audit table if using database
        if hasattr(self, 'database_block') and self.database_block:
            await self._create_audit_table()
            
            # Load last hash for chain continuity
            last_event = await self._get_last_event()
            if last_event:
                self.last_hash = last_event.get("integrity_hash", self.last_hash)
                self.event_count = last_event.get("sequence", 0)
                print(f"   Chain continuity: {self.event_count} events")
        
        self.initialized = True
        return True
    
    async def _create_audit_table(self):
        """Create audit log table with integrity constraints"""
        try:
            await self.database_block.execute({
                "action": "create_table",
                "table": "audit_log",
                "schema": {
                    "id": "TEXT PRIMARY KEY",
                    "timestamp": "REAL NOT NULL",
                    "sequence": "INTEGER NOT NULL UNIQUE",
                    "category": "TEXT NOT NULL",
                    "user_id": "TEXT",
                    "action": "TEXT NOT NULL",
                    "resource": "TEXT",
                    "details": "TEXT",  # JSON
                    "ip_hash": "TEXT",
                    "previous_hash": "TEXT NOT NULL",
                    "integrity_hash": "TEXT NOT NULL"
                }
            })
            
            # Create indexes
            await self.database_block.execute({
                "action": "query",
                "query": "CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp)"
            })
            await self.database_block.execute({
                "action": "query",
                "query": "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)"
            })
            await self.database_block.execute({
                "action": "query",
                "query": "CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(category)"
            })
            
        except Exception as e:
            print(f"   Table may exist: {e}")
    
    async def execute(self, input_data: Dict) -> Dict:
        """Handle audit actions"""
        action = input_data.get("action")
        
        if action == "log":
            return await self._log_event(input_data)
        elif action == "query":
            return await self._query_logs(input_data)
        elif action == "export":
            return await self._export_compliance(input_data)
        elif action == "verify_chain":
            return await self._verify_chain(input_data)
        elif action == "get_stats":
            return await self._get_stats(input_data)
        elif action == "tamper_check":
            return await self._tamper_check(input_data)
            
        return {"error": f"Unknown action: {action}"}
    
    async def _log_event(self, data: Dict) -> Dict:
        """
        Log immutable audit event with hash chaining
        
        The integrity_hash creates a blockchain-style chain:
        hash(event_data + previous_hash)
        
        Any tampering breaks the chain.
        """
        self.event_count += 1
        sequence = self.event_count
        
        # Build event
        timestamp = time.time()
        event_data = {
            "id": self._generate_id(sequence, timestamp),
            "timestamp": timestamp,
            "sequence": sequence,
            "category": data.get("category", "system"),
            "user_id": data.get("user_id"),
            "action": data.get("event_action", "unknown"),
            "resource": data.get("resource", "*"),
            "details": json.dumps(data.get("details", {})),
            "ip_hash": self._hash_ip(data.get("ip", "")),
            "session_id": data.get("session_id"),
            "previous_hash": self.last_hash
        }
        
        # Calculate integrity hash (blockchain-style)
        integrity_hash = self._calculate_hash(event_data)
        event_data["integrity_hash"] = integrity_hash
        
        # Store
        if hasattr(self, 'database_block') and self.database_block:
            try:
                await self.database_block.execute({
                    "action": "insert",
                    "table": "audit_log",
                    "data": event_data
                })
            except Exception as e:
                return {"error": f"Failed to store audit: {str(e)}"}
        
        # Update chain
        self.last_hash = integrity_hash
        
        # Also log to memory for hot queries
        if hasattr(self, 'memory_block') and self.memory_block:
            await self.memory_block.execute({
                "action": "set",
                "key": f"audit:recent:{sequence}",
                "value": event_data,
                "ttl": 3600  # 1 hour hot cache
            })
        
        return {
            "logged": True,
            "event_id": event_data["id"],
            "sequence": sequence,
            "integrity_hash": integrity_hash[:16] + "...",
            "immutable": True
        }
    
    def _calculate_hash(self, event_data: Dict) -> str:
        """Calculate blockchain-style integrity hash"""
        # Hash of: event content + previous hash
        content = json.dumps({
            "sequence": event_data["sequence"],
            "timestamp": event_data["timestamp"],
            "category": event_data["category"],
            "user_id": event_data["user_id"],
            "action": event_data["action"],
            "resource": event_data["resource"],
            "details": event_data["details"],
            "previous_hash": event_data["previous_hash"]
        }, sort_keys=True)
        
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _generate_id(self, sequence: int, timestamp: float) -> str:
        """Generate unique event ID"""
        return hashlib.sha256(
            f"{sequence}:{timestamp}:{self.last_hash}".encode()
        ).hexdigest()[:16]
    
    def _hash_ip(self, ip: str) -> str:
        """One-way hash of IP for privacy"""
        if not ip:
            return ""
        return hashlib.sha256(f"audit:{ip}".encode()).hexdigest()[:16]
    
    async def _query_logs(self, data: Dict) -> Dict:
        """Query audit logs (read-only, no modifications allowed)"""
        filters = {
            "user_id": data.get("user_id"),
            "category": data.get("category"),
            "resource": data.get("resource"),
            "action": data.get("action"),
            "since": data.get("since"),  # timestamp
            "until": data.get("until")
        }
        
        limit = data.get("limit", 100)
        offset = data.get("offset", 0)
        
        if hasattr(self, 'database_block') and self.database_block:
            # Build query
            conditions = ["1=1"]
            params = []
            
            if filters["user_id"]:
                conditions.append("user_id = ?")
                params.append(filters["user_id"])
            if filters["category"]:
                conditions.append("category = ?")
                params.append(filters["category"])
            if filters["resource"]:
                conditions.append("resource = ?")
                params.append(filters["resource"])
            if filters["since"]:
                conditions.append("timestamp >= ?")
                params.append(filters["since"])
            if filters["until"]:
                conditions.append("timestamp <= ?")
                params.append(filters["until"])
            
            where_clause = " AND ".join(conditions)
            query = f"""
                SELECT * FROM audit_log 
                WHERE {where_clause}
                ORDER BY sequence DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            
            result = await self.database_block.execute({
                "action": "query",
                "query": query,
                "params": params
            })
            
            return {
                "events": result.get("rows", []),
                "count": len(result.get("rows", [])),
                "filters": {k: v for k, v in filters.items() if v},
                "immutable": True
            }
        
        return {"events": [], "count": 0, "error": "Database not available"}
    
    async def _verify_chain(self, data: Dict) -> Dict:
        """Verify integrity of entire audit chain"""
        if not hasattr(self, 'database_block') or not self.database_block:
            return {"error": "Database not available"}
        
        # Get all events in order
        result = await self.database_block.execute({
            "action": "query",
            "query": "SELECT * FROM audit_log ORDER BY sequence ASC"
        })
        
        events = result.get("rows", [])
        violations = []
        
        for i, event in enumerate(events):
            # Verify hash
            calculated = self._calculate_hash(event)
            stored = event.get("integrity_hash")
            
            if calculated != stored:
                violations.append({
                    "sequence": event["sequence"],
                    "error": "hash_mismatch",
                    "stored": stored[:16],
                    "calculated": calculated[:16]
                })
            
            # Verify chain continuity (except first)
            if i > 0:
                prev_hash = events[i-1].get("integrity_hash")
                current_prev = event.get("previous_hash")
                if prev_hash != current_prev:
                    violations.append({
                        "sequence": event["sequence"],
                        "error": "chain_broken",
                        "expected_prev": prev_hash[:16],
                        "actual_prev": current_prev[:16]
                    })
        
        return {
            "verified": len(violations) == 0,
            "total_events": len(events),
            "violations": violations,
            "integrity": "compromised" if violations else "intact"
        }
    
    async def _export_compliance(self, data: Dict) -> Dict:
        """Export audit logs for compliance (SOC2, GDPR, etc.)"""
        standard = data.get("standard", "generic")  # soc2, gdpr, hipaa
        since = data.get("since", time.time() - 86400 * 30)  # 30 days default
        
        # Query relevant events
        logs = await self._query_logs({
            "since": since,
            "category": data.get("category"),
            "limit": 10000
        })
        
        events = logs.get("events", [])
        
        # Format for standard
        if standard == "soc2":
            export = self._format_soc2(events)
        elif standard == "gdpr":
            export = self._format_gdpr(events)
        else:
            export = events
        
        # Generate integrity proof
        chain_verification = await self._verify_chain({})
        
        return {
            "standard": standard,
            "export": export,
            "count": len(events),
            "period": {
                "from": datetime.fromtimestamp(since).isoformat(),
                "to": datetime.now().isoformat()
            },
            "integrity": chain_verification,
            "generated_at": datetime.now().isoformat()
        }
    
    def _format_soc2(self, events: List[Dict]) -> List[Dict]:
        """Format for SOC2 compliance"""
        return [{
            "timestamp": e["timestamp"],
            "user": e["user_id"],
            "action": e["action"],
            "resource": e["resource"],
            "cc": self._map_to_cc(e["category"])  # Common Criteria
        } for e in events]
    
    def _format_gdpr(self, events: List[Dict]) -> List[Dict]:
        """Format for GDPR compliance"""
        return [{
            "timestamp": e["timestamp"],
            "data_subject": e["user_id"],
            "processing_activity": e["action"],
            "legal_basis": "legitimate_interest",  # Could be customized
            "data_categories": [e["resource"]]
        } for e in events]
    
    def _map_to_cc(self, category: str) -> str:
        """Map category to Common Criteria"""
        mapping = {
            "auth": "CC6.1",  # Logical access
            "data_access": "CC6.3",  # Access removal
            "system": "CC7.2",  # System monitoring
            "admin": "CC6.2"  # Privileged access
        }
        return mapping.get(category, "CC7.1")
    
    async def _tamper_check(self, data: Dict) -> Dict:
        """Quick tamper detection check"""
        verification = await self._verify_chain({})
        return {
            "tampered": not verification["verified"],
            "confidence": "high" if verification["total_events"] > 0 else "none",
            "details": verification
        }
    
    async def _get_stats(self, data: Dict) -> Dict:
        """Get audit statistics"""
        return {
            "total_events": self.event_count,
            "chain_head": self.last_hash[:16] + "...",
            "retention_days": self.config.get("retention_days", 2555),
            "immutable": self.config.get("immutable", True),
            "categories": self.config.get("categories", [])
        }
    
    async def _get_last_event(self) -> Optional[Dict]:
        """Get last event for chain continuity"""
        if not hasattr(self, 'database_block') or not self.database_block:
            return None
        
        result = await self.database_block.execute({
            "action": "query",
            "query": "SELECT * FROM audit_log ORDER BY sequence DESC LIMIT 1"
        })
        
        rows = result.get("rows", [])
        return rows[0] if rows else None
    
    def health(self) -> Dict:
        """Audit block health"""
        h = super().health()
        h["events_logged"] = self.event_count
        h["chain_head"] = self.last_hash[:16] + "..."
        h["immutable"] = self.config.get("immutable", True)
        return h
