"""AI Core Container - Adaptive routing, failover, leaderboard"""

import time
from typing import Any, Dict
from app.core.universal_base import UniversalContainer


class AICoreContainer(UniversalContainer):
    """
    AI Core Container: Adaptive routing, provider failover, performance leaderboard
    """
    
    name = "ai_core"
    version = "1.0"
    description = "AI Core: Adaptive Router, Failover, Leaderboard"
    layer = 2  # AI Core
    tags = ["ai", "core", "container", "routing"]
    requires = []

    ui_schema = {
        "input": {
            "type": "json",
            "accept": None,
            "placeholder": '{"action": "route", "quality": "fast"}',
            "multiline": False
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "rankings", "type": "json", "label": "Provider Rankings"},
                {"name": "top_provider", "type": "text", "label": "Best Provider"}
            ]
        },
        "quick_actions": [
            {"icon": "🏆", "label": "Leaderboard", "prompt": '{"action":"leaderboard"}'},
            {"icon": "🚀", "label": "Best Route", "prompt": '{"action":"route","quality":"fast"}'},
            {"icon": "⚡", "label": "Failover Status", "prompt": '{"action":"failover_status"}'}
        ]
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.providers = ["deepseek", "groq", "openai"]
        self.provider_stats = {p: {"latency": [], "success": 0, "failure": 0} for p in self.providers}
        self.circuit_states = {p: "closed" for p in self.providers}
    
    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        if action == "leaderboard":
            return await self.leaderboard()
        elif action == "route":
            return await self.route_request(params)
        elif action == "failover_status":
            return await self.failover_status()
        elif action == "health_check":
            return await self.health_check()
        else:
            return {"error": f"Unknown action: {action}"}
    
    async def leaderboard(self) -> Dict:
        """Get provider rankings"""
        rankings = []
        for provider, stats in self.provider_stats.items():
            total = stats["success"] + stats["failure"]
            success_rate = stats["success"] / max(total, 1)
            avg_latency = sum(stats["latency"]) / max(len(stats["latency"]), 1)
            
            # Score: 50% success rate, 30% latency, 20% circuit state
            circuit_bonus = 1.0 if self.circuit_states[provider] == "closed" else 0.5
            score = (success_rate * 0.5 + (1 - min(avg_latency/1000, 1)) * 0.3 + circuit_bonus * 0.2)
            
            rankings.append({
                "provider": provider,
                "score": round(score, 2),
                "avg_latency_ms": int(avg_latency) if stats["latency"] else 999,
                "success_rate": round(success_rate, 2),
                "circuit_state": self.circuit_states[provider]
            })
        
        rankings.sort(key=lambda x: x["score"], reverse=True)
        
        return {
            "status": "success",
            "rankings": rankings,
            "top_provider": rankings[0]["provider"] if rankings else "deepseek"
        }
    
    async def route_request(self, params: Dict) -> Dict:
        """Select best provider for request"""
        quality = params.get("quality", "balanced")  # fast, balanced, quality
        
        # Get current leaderboard
        lb = await self.leaderboard()
        rankings = lb.get("rankings", [])
        
        # Filter available (circuit closed)
        available = [r for r in rankings if r["circuit_state"] == "closed"]
        if not available:
            available = rankings  # Fallback to all
        
        if quality == "fast":
            selected = min(available, key=lambda x: x["avg_latency_ms"])
        else:
            selected = max(available, key=lambda x: x["score"])
        
        return {
            "status": "success",
            "selected_provider": selected["provider"],
            "estimated_cost": 0.14 if selected["provider"] == "deepseek" else 0.59,
            "quality_mode": quality
        }
    
    async def failover_status(self) -> Dict:
        """Get circuit breaker status"""
        return {
            "status": "success",
            "circuits": self.circuit_states,
            "provider_stats": self.provider_stats
        }
    
    async def health_check(self) -> Dict:
        return {
            "status": "healthy",
            "container": self.name,
            "capabilities": ["leaderboard", "route", "failover_status"],
            "providers": self.providers
        }
