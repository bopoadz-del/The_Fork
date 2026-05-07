"""AI Core Container - Layer 2: The Brain of Cerebrum Lego OS"""

import time
from typing import Any, Dict, List
from app.core.block import BaseBlock, BlockConfig


class AICoreContainer(BaseBlock):
    """
    AI Core Container: Chat, Vector, Failover, Adaptive Router, Monitoring, Analytics
    Layer 2 - Depends on Infrastructure (L0) and Security (L1)
    """
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="ai_core",
            version="1.0",
            description="AI Core Container: Chat, Vector, Failover, Adaptive Router, Monitoring, Analytics. The brain layer.",
            requires_api_key=True,
            supported_inputs=["prompt", "query", "chain"],
            supported_outputs=["completion", "embedding", "routing", "metrics"]
        ,
            layer=2,
            tags=["ai", "core", "container"]))
        self.provider_stats = {
            "deepseek": {"latency": [], "success": 0, "failure": 0},
            "groq": {"latency": [], "success": 0, "failure": 0},
            "openai": {"latency": [], "success": 0, "failure": 0}
        }
        self.circuit_states = {p: "closed" for p in self.provider_stats.keys()}
        
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        params = params or {}
        action = params.get("action", "route")
        
        if action == "route":
            return await self._adaptive_route(input_data, params)
        elif action == "chat":
            return await self._chat_completion(input_data, params)
        elif action == "embed":
            return await self._vector_embed(input_data, params)
        elif action == "failover_status":
            return self._get_failover_status()
        elif action == "leaderboard":
            return self._get_provider_leaderboard()
        elif action == "monitor":
            return await self._monitor_call(input_data, params)
        else:
            return {"status": "error", "message": f"Unknown action: {action}"}
    
    async def _adaptive_route(self, input_data: Any, params: Dict) -> Dict:
        """Block 41: Adaptive Router - learns from history"""
        chain = params.get("chain", ["chat"])
        required_quality = params.get("quality", "standard")  # fast, standard, premium
        budget = params.get("budget_cents", 100)
        
        # Simple adaptive logic (ML-ready structure)
        scores = {}
        for provider, stats in self.provider_stats.items():
            if stats["latency"]:
                avg_latency = sum(stats["latency"][-10:]) / len(stats["latency"][-10:])
                success_rate = stats["success"] / max(stats["success"] + stats["failure"], 1)
                scores[provider] = (success_rate * 1000) / (avg_latency + 1)
            else:
                scores[provider] = 50  # Default exploration score
        
        # Circuit breaker check
        available = [p for p, state in self.circuit_states.items() if state == "closed"]
        if not available:
            available = list(self.provider_stats.keys())  # Force open if all tripped
        
        # Choose best available
        best_provider = max(available, key=lambda p: scores.get(p, 0))
        
        return {
            "status": "success",
            "action": "adaptive_route",
            "selected_provider": best_provider,
            "reason": f"Circuit: {self.circuit_states[best_provider]}, Score: {scores.get(best_provider, 0):.1f}",
            "all_scores": scores,
            "circuit_states": self.circuit_states,
            "estimated_cost": 0.14 if best_provider == "deepseek" else 0.42,
            "estimated_latency_ms": 120 if best_provider == "groq" else 350
        }
    
    async def _chat_completion(self, input_data: Any, params: Dict) -> Dict:
        """Proxy to chat block with monitoring"""
        provider = params.get("provider", "deepseek")
        prompt = input_data if isinstance(input_data, str) else params.get("prompt", "")
        
        start = time.time()
        
        # TODO: Actually call chat block here
        # Simulated response for now
        result = {
            "provider": provider,
            "completion": f"AI response via {provider}: Processed '{prompt[:50]}...'",
            "tokens_used": 150,
            "cost_cents": 14 if provider == "deepseek" else 42
        }
        
        latency = (time.time() - start) * 1000
        self._record_metric(provider, latency, success=True)
        
        return {
            "status": "success",
            "action": "chat",
            "result": result,
            "latency_ms": latency
        }
    
    async def _vector_embed(self, input_data: Any, params: Dict) -> Dict:
        """Vector embedding via Vector block"""
        texts = params.get("texts", [str(input_data)])
        
        return {
            "status": "success",
            "action": "embed",
            "dimensions": 384,
            "vectors": [[0.1] * 384 for _ in texts],  # Placeholder
            "backend": "chroma",
            "model": "all-MiniLM-L6-v2"
        }
    
    def _get_failover_status(self) -> Dict:
        """Block 40: Failover status across providers"""
        return {
            "status": "success",
            "action": "failover_status",
            "circuits": self.circuit_states,
            "recommendation": "groq" if self.circuit_states["deepseek"] == "open" else "deepseek",
            "auto_reroute_enabled": True
        }
    
    def _get_provider_leaderboard(self) -> Dict:
        """Monitoring Block: Provider performance rankings"""
        leaderboard = []
        for provider, stats in self.provider_stats.items():
            if stats["latency"]:
                avg_lat = sum(stats["latency"][-50:]) / len(stats["latency"][-50:])
                success_rate = stats["success"] / max(stats["success"] + stats["failure"], 1)
            else:
                avg_lat = 999
                success_rate = 0.5
            
            leaderboard.append({
                "provider": provider,
                "avg_latency_ms": avg_lat,
                "success_rate": success_rate,
                "circuit_state": self.circuit_states[provider],
                "score": success_rate * 100 / (avg_lat + 1)
            })
        
        leaderboard.sort(key=lambda x: x["score"], reverse=True)
        
        return {
            "status": "success",
            "action": "leaderboard",
            "rankings": leaderboard,
            "top_provider": leaderboard[0]["provider"] if leaderboard else "unknown",
            "generated_at": time.time()
        }
    
    async def _monitor_call(self, input_data: Any, params: Dict) -> Dict:
        """Record a monitored call for learning"""
        provider = params.get("provider", "deepseek")
        latency = params.get("latency_ms", 0)
        success = params.get("success", True)
        
        self._record_metric(provider, latency, success)
        
        # Circuit breaker logic: 3 failures = open circuit
        if not success:
            recent = self.provider_stats[provider]["failure"]
            if recent >= 3:
                self.circuit_states[provider] = "open"
                return {
                    "status": "circuit_opened",
                    "provider": provider,
                    "message": "Circuit opened due to 3 consecutive failures",
                    "fallback": "groq"
                }
        
        return {
            "status": "recorded",
            "provider": provider,
            "circuit_state": self.circuit_states[provider]
        }
    
    def _record_metric(self, provider: str, latency: float, success: bool):
        """Record performance metric for adaptive routing"""
        if provider in self.provider_stats:
            self.provider_stats[provider]["latency"].append(latency)
            if len(self.provider_stats[provider]["latency"]) > 100:
                self.provider_stats[provider]["latency"].pop(0)
            
            if success:
                self.provider_stats[provider]["success"] += 1
            else:
                self.provider_stats[provider]["failure"] += 1
                # Circuit breaker: 3 strikes
                if self.provider_stats[provider]["failure"] >= 3:
                    self.circuit_states[provider] = "open"
