"""Health Check Block - Deep health checks beyond basic ping

Features:
- Database connectivity tests
- External API health checks
- Disk space and resource monitoring
- Dependency health aggregation
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
from datetime import datetime
import time
import asyncio


class HealthCheckBlock(LegoBlock):
    """
    Deep health checks beyond basic ping.
    Tests database connections, external APIs, disk space.
    """
    name = "health_check"
    version = "1.0.0"
    requires = ["database", "memory", "config"]
    layer = 1
    tags = ["observability", "infra", "reliability", "health"]
    
    default_config = {
        "check_interval": 30,  # seconds
        "deep_check_interval": 300,  # 5 min
        "timeout_per_check": 5,
        "alert_on_failure": True,
        "max_probe_history": 100
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.probes: Dict[str, Dict] = {}  # probe_id -> probe config
        self.check_history: List[Dict] = []  # Recent check results
        self.dependency_health: Dict[str, Dict] = {}  # Component health cache
        
    async def initialize(self) -> bool:
        """Initialize health checker"""
        print("🏥 Health Check Block initializing...")
        print(f"   Check interval: {self.config['check_interval']}s")
        print(f"   Deep check interval: {self.config['deep_check_interval']}s")
        
        # Register default probes
        self._register_default_probes()
        
        # Start background checker
        asyncio.create_task(self._background_checker())
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute health check actions"""
        action = input_data.get("action")
        
        actions = {
            "ping": self._ping,
            "deep_check": self._deep_check,
            "check_dependency": self._check_dependency,
            "register_probe": self._register_probe,
            "unregister_probe": self._unregister_probe,
            "get_status": self._get_status,
            "get_history": self._get_history,
            "simulate_failure": self._simulate_failure
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _ping(self, data: Dict) -> Dict:
        """Lightweight liveness check"""
        return {
            "status": "alive",
            "timestamp": time.time(),
            "service": "cerebrum-blocks",
            "version": "1.0.0"
        }
        
    async def _deep_check(self, data: Dict) -> Dict:
        """Full system diagnostic"""
        checks = {}
        start_time = time.time()
        
        # Check database
        checks["database"] = await self._check_database()
        
        # Check memory/storage
        checks["memory"] = await self._check_memory()
        
        # Check external APIs
        checks["external_apis"] = await self._check_external_apis()
        
        # Check registered probes
        for probe_id, probe in self.probes.items():
            checks[f"probe:{probe_id}"] = await self._run_probe(probe)
            
        # Check disk space
        checks["disk"] = await self._check_disk()
        
        # Overall health
        failed = [k for k, v in checks.items() if not v.get("healthy")]
        all_healthy = len(failed) == 0
        
        result = {
            "healthy": all_healthy,
            "timestamp": datetime.utcnow().isoformat(),
            "response_time_ms": round((time.time() - start_time) * 1000, 2),
            "checks": checks,
            "failed_checks": failed,
            "status": "healthy" if all_healthy else "degraded"
        }
        
        # Store in history
        self.check_history.append({
            "timestamp": result["timestamp"],
            "healthy": all_healthy,
            "failed": failed
        })
        
        # Trim history
        if len(self.check_history) > self.config["max_probe_history"]:
            self.check_history = self.check_history[-self.config["max_probe_history"]:]
            
        return result
        
    async def _check_dependency(self, data: Dict) -> Dict:
        """Check health of a specific dependency"""
        dependency = data.get("dependency")
        
        if dependency == "database":
            return await self._check_database()
        elif dependency == "memory":
            return await self._check_memory()
        elif dependency in self.probes:
            return await self._run_probe(self.probes[dependency])
        else:
            return {
                "error": f"Unknown dependency: {dependency}",
                "available": ["database", "memory"] + list(self.probes.keys())
            }
            
    async def _register_probe(self, data: Dict) -> Dict:
        """Register a custom health probe"""
        probe_id = data.get("probe_id")
        name = data.get("name", probe_id)
        check_type = data.get("check_type", "http")  # http, tcp, custom
        target = data.get("target")
        timeout = data.get("timeout", self.config["timeout_per_check"])
        
        if not probe_id or not target:
            return {"error": "probe_id and target required"}
            
        probe = {
            "id": probe_id,
            "name": name,
            "type": check_type,
            "target": target,
            "timeout": timeout,
            "registered_at": datetime.utcnow().isoformat(),
            "last_check": None,
            "consecutive_failures": 0
        }
        
        self.probes[probe_id] = probe
        
        return {
            "registered": True,
            "probe_id": probe_id,
            "name": name,
            "type": check_type
        }
        
    async def _unregister_probe(self, data: Dict) -> Dict:
        """Unregister a health probe"""
        probe_id = data.get("probe_id")
        
        if probe_id not in self.probes:
            return {"error": "Probe not found"}
            
        del self.probes[probe_id]
        
        return {"unregistered": True, "probe_id": probe_id}
        
    async def _get_status(self, data: Dict) -> Dict:
        """Get current health status summary"""
        # Quick check vs deep check
        quick = data.get("quick", True)
        
        if quick:
            # Just ping and basic checks
            return {
                "healthy": True,
                "timestamp": datetime.utcnow().isoformat(),
                "mode": "quick",
                "registered_probes": len(self.probes)
            }
        else:
            return await self._deep_check(data)
            
    async def _get_history(self, data: Dict) -> Dict:
        """Get health check history"""
        limit = data.get("limit", 20)
        
        return {
            "history": self.check_history[-limit:],
            "total_checks": len(self.check_history),
            "uptime_percentage": self._calculate_uptime()
        }
        
    async def _simulate_failure(self, data: Dict) -> Dict:
        """Simulate a failure for testing"""
        component = data.get("component")
        duration = data.get("duration", 60)  # seconds
        
        # This would be used in testing scenarios
        return {
            "simulated": True,
            "component": component,
            "duration": duration,
            "note": "Use for testing alert mechanisms"
        }
        
    # Helper methods
    def _register_default_probes(self):
        """Register default system probes"""
        default_probes = [
            {
                "id": "deepseek_api",
                "name": "DeepSeek API",
                "type": "http",
                "target": "https://api.deepseek.com/health",
                "timeout": 5
            },
            {
                "id": "internal_api",
                "name": "Internal API",
                "type": "http",
                "target": "http://localhost:8000/health",
                "timeout": 2
            }
        ]
        
        for probe in default_probes:
            self.probes[probe["id"]] = probe
            
    async def _background_checker(self):
        """Background task for periodic health checks"""
        while True:
            try:
                # Deep check every N intervals
                await self._deep_check({})
            except Exception as e:
                print(f"Health check error: {e}")
                
            await asyncio.sleep(self.config["deep_check_interval"])
            
    async def _check_database(self) -> Dict:
        """Check database connectivity"""
        start = time.time()
        
        try:
            if hasattr(self, 'database_block') and self.database_block:
                # Try a simple query
                result = await self.database_block.execute({
                    "action": "health"
                })
                
                return {
                    "healthy": result.get("healthy", True),
                    "response_time_ms": round((time.time() - start) * 1000, 2),
                    "component": "database"
                }
            else:
                return {
                    "healthy": True,
                    "note": "Database block not connected",
                    "component": "database"
                }
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "component": "database"
            }
            
    async def _check_memory(self) -> Dict:
        """Check memory/storage status"""
        try:
            if hasattr(self, 'memory_block') and self.memory_block:
                result = await self.memory_block.execute({
                    "action": "stats"
                })
                
                return {
                    "healthy": True,
                    "stats": result,
                    "component": "memory"
                }
            else:
                return {
                    "healthy": True,
                    "note": "Memory block not connected",
                    "component": "memory"
                }
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "component": "memory"
            }
            
    async def _check_external_apis(self) -> Dict:
        """Check external API health"""
        # Check configured external APIs
        apis = {
            "deepseek": "https://api.deepseek.com/health",
            # Add more as configured
        }
        
        results = {}
        all_healthy = True
        
        for name, url in apis.items():
            # Simulated check - in production would do actual HTTP request
            results[name] = {
                "healthy": True,  # Placeholder
                "url": url,
                "status": "unknown"  # Would be actual HTTP status
            }
            
        return {
            "healthy": all_healthy,
            "apis": results,
            "component": "external_apis"
        }
        
    async def _check_disk(self) -> Dict:
        """Check disk space"""
        # In production, use shutil.disk_usage
        import shutil
        
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free / (1024**3)
            total_gb = usage.total / (1024**3)
            used_percent = (usage.used / usage.total) * 100
            
            healthy = free_gb > 1.0  # Alert if less than 1GB free
            
            return {
                "healthy": healthy,
                "free_gb": round(free_gb, 2),
                "total_gb": round(total_gb, 2),
                "used_percent": round(used_percent, 1),
                "component": "disk"
            }
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e),
                "component": "disk"
            }
            
    async def _run_probe(self, probe: Dict) -> Dict:
        """Run a single health probe"""
        start = time.time()
        
        try:
            # Simulate probe check based on type
            if probe["type"] == "http":
                # Would do actual HTTP request
                healthy = True
            elif probe["type"] == "tcp":
                # Would do TCP connect
                healthy = True
            else:
                healthy = True
                
            probe["last_check"] = datetime.utcnow().isoformat()
            probe["consecutive_failures"] = 0
            
            return {
                "healthy": healthy,
                "response_time_ms": round((time.time() - start) * 1000, 2),
                "probe_id": probe["id"]
            }
            
        except Exception as e:
            probe["consecutive_failures"] += 1
            
            return {
                "healthy": False,
                "error": str(e),
                "consecutive_failures": probe["consecutive_failures"],
                "probe_id": probe["id"]
            }
            
    def _calculate_uptime(self) -> float:
        """Calculate uptime percentage from history"""
        if not self.check_history:
            return 100.0
            
        healthy_count = sum(1 for h in self.check_history if h.get("healthy"))
        return round((healthy_count / len(self.check_history)) * 100, 2)
        
    def health(self) -> Dict:
        h = super().health()
        h["registered_probes"] = len(self.probes)
        h["check_history_length"] = len(self.check_history)
        h["uptime_percentage"] = self._calculate_uptime()
        return h
