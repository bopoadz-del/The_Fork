"""Adaptive Router Block - ML-based provider selection

Features:
- Learns from historical performance
- Time-based pattern recognition
- Provider scoring and selection
- A/B testing and exploration
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
from collections import deque, defaultdict
from datetime import datetime
import time
import random
import asyncio


class AdaptiveRouterBlock(LegoBlock):
    """
    ML-based provider selection.
    Learns from history: "DeepSeek slow on Tuesdays, use Groq instead"
    """
    name = "adaptive_router"
    version = "1.0.0"
    requires = ["analytics", "monitoring", "failover"]
    layer = 2  # Core layer
    tags = ["ai", "optimization", "smart", "routing"]
    
    default_config = {
        "learning_enabled": True,
        "history_window": 100,  # last N requests to learn from
        "exploration_rate": 0.1,  # 10% try new providers
        "min_samples": 10,  # Min samples before trusting score
        "time_aware": True,  # Consider time of day
        "quality_threshold": 0.8  # Min quality score to select
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        # Performance history: provider -> list of results
        self.history: Dict[str, List[Dict]] = defaultdict(list)
        # Provider scores: provider -> score data
        self.provider_scores: Dict[str, Dict] = {}
        # Time patterns: (hour, day) -> provider performance
        self.time_patterns: Dict[tuple, Dict[str, float]] = {}
        # A/B test assignments
        self.ab_assignments: Dict[str, str] = {}
        
    async def initialize(self) -> bool:
        """Initialize adaptive router"""
        print("🧠 Adaptive Router Block initializing...")
        print(f"   Learning: {self.config['learning_enabled']}")
        print(f"   Exploration: {self.config['exploration_rate']}")
        print(f"   History window: {self.config['history_window']}")
        
        # Load historical data from analytics
        await self._load_historical_data()
        
        # Start background learning
        if self.config["learning_enabled"]:
            asyncio.create_task(self._background_learning())
            
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute adaptive routing actions"""
        action = input_data.get("action")
        
        actions = {
            "select_provider": self._select_provider,
            "record_result": self._record_result,
            "get_recommendation": self._get_recommendation,
            "forecast_issues": self._forecast,
            "get_scores": self._get_scores,
            "reset_scores": self._reset_scores,
            "ab_test": self._ab_test,
            "explain_choice": self._explain_choice
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _select_provider(self, data: Dict) -> Dict:
        """Select best provider for a task"""
        task_type = data.get("task")  # "chat", "embed", "image"
        required_quality = data.get("quality", "standard")  # fast, standard, premium
        budget = data.get("budget_cents")  # Cost constraint
        exclude = data.get("exclude", [])  # Providers to exclude
        
        # Get available providers for task
        providers = self._get_providers_for_task(task_type)
        providers = [p for p in providers if p not in exclude]
        
        if not providers:
            return {"error": "No providers available for this task"}
            
        # Exploration: sometimes try non-optimal providers
        if random.random() < self.config["exploration_rate"]:
            selected = random.choice(providers)
            reason = "exploration"
            confidence = 0.5
        else:
            # Exploitation: pick best provider
            scores = []
            
            for provider in providers:
                score_data = self._calculate_provider_score(
                    provider, task_type, required_quality, budget
                )
                scores.append((provider, score_data))
                
            # Sort by score
            scores.sort(key=lambda x: x[1]["score"], reverse=True)
            
            selected = scores[0][0]
            confidence = scores[0][1]["confidence"]
            reason = scores[0][1]["reason"]
            
        # Get estimated metrics
        estimates = self._estimate_metrics(selected, task_type)
        
        return {
            "selected_provider": selected,
            "confidence": round(confidence, 2),
            "reason": reason,
            "estimated_latency_ms": estimates.get("latency"),
            "estimated_cost_per_1k": estimates.get("cost"),
            "quality_score": estimates.get("quality"),
            "fallback_recommended": self._get_fallback(selected, providers)
        }
        
    async def _record_result(self, data: Dict) -> Dict:
        """Record result for learning feedback loop"""
        provider = data.get("provider")
        task_type = data.get("task_type")
        success = data.get("success", True)
        latency_ms = data.get("latency_ms")
        cost = data.get("cost")
        error = data.get("error")
        
        if not provider:
            return {"error": "provider required"}
            
        # Create result record
        result = {
            "timestamp": time.time(),
            "provider": provider,
            "task_type": task_type,
            "success": success,
            "latency_ms": latency_ms,
            "cost": cost,
            "error": error,
            "hour": datetime.utcnow().hour,
            "day": datetime.utcnow().weekday()
        }
        
        # Add to history
        self.history[provider].append(result)
        
        # Trim history
        max_history = self.config["history_window"]
        if len(self.history[provider]) > max_history:
            self.history[provider] = self.history[provider][-max_history:]
            
        # Update scores
        self._update_provider_score(provider)
        
        # Update time patterns
        if self.config["time_aware"]:
            self._update_time_patterns(result)
            
        return {
            "recorded": True,
            "provider": provider,
            "samples_count": len(self.history[provider])
        }
        
    async def _get_recommendation(self, data: Dict) -> Dict:
        """Get routing recommendation with explanation"""
        task_type = data.get("task")
        
        selection = await self._select_provider(data)
        explanation = await self._explain_choice({
            "provider": selection["selected_provider"],
            "task": task_type
        })
        
        return {
            **selection,
            "explanation": explanation.get("explanation")
        }
        
    async def _forecast(self, data: Dict) -> Dict:
        """Predict problems before they happen"""
        hours_ahead = data.get("hours", 1)
        
        # Look at time patterns
        future_time = datetime.utcnow().hour + hours_ahead
        day = datetime.utcnow().weekday()
        
        predictions = []
        
        for provider, history in self.history.items():
            if len(history) < self.config["min_samples"]:
                continue
                
            # Check historical performance at this time
            time_matches = [
                h for h in history 
                if abs(h["hour"] - (future_time % 24)) <= 1
            ]
            
            if time_matches:
                failure_rate = sum(1 for h in time_matches if not h["success"]) / len(time_matches)
                avg_latency = sum(h["latency_ms"] for h in time_matches if h["latency_ms"]) / len(time_matches)
                
                if failure_rate > 0.1 or avg_latency > 2000:
                    predictions.append({
                        "provider": provider,
                        "predicted_failure_rate": round(failure_rate, 2),
                        "predicted_latency_ms": round(avg_latency, 0),
                        "risk_level": "high" if failure_rate > 0.3 else "medium",
                        "confidence": min(len(time_matches) / 20, 1.0)
                    })
                    
        predictions.sort(key=lambda x: x["predicted_failure_rate"], reverse=True)
        
        return {
            "forecast_time": f"+{hours_ahead}h",
            "predictions": predictions[:5],
            "recommendation": "Consider fallback providers" if predictions else "No issues predicted"
        }
        
    async def _get_scores(self, data: Dict) -> Dict:
        """Get current provider scores"""
        provider = data.get("provider")
        
        if provider:
            return {
                "provider": provider,
                "score": self.provider_scores.get(provider, {}),
                "samples": len(self.history.get(provider, []))
            }
        else:
            return {
                "providers": {
                    p: {
                        "score": s.get("score", 0),
                        "samples": len(self.history.get(p, []))
                    }
                    for p, s in self.provider_scores.items()
                }
            }
            
    async def _reset_scores(self, data: Dict) -> Dict:
        """Reset learning scores"""
        provider = data.get("provider")
        
        if provider:
            self.provider_scores[provider] = {}
            self.history[provider] = []
            return {"reset": True, "provider": provider}
        else:
            self.provider_scores = {}
            self.history = defaultdict(list)
            return {"reset": True, "all": True}
            
    async def _ab_test(self, data: Dict) -> Dict:
        """A/B test provider assignment"""
        user_id = data.get("user_id")
        test_name = data.get("test_name", "default")
        variants = data.get("variants", ["A", "B"])
        
        # Consistent assignment based on user_id hash
        if user_id:
            import hashlib
            hash_val = int(hashlib.sha256(f"{user_id}:{test_name}".encode()).hexdigest(), 16)
            variant = variants[hash_val % len(variants)]
        else:
            variant = random.choice(variants)
            
        assignment_id = f"{test_name}:{variant}"
        self.ab_assignments[user_id or "anonymous"] = assignment_id
        
        return {
            "assigned_variant": variant,
            "test_name": test_name,
            "user_id": user_id,
            "assignment_id": assignment_id
        }
        
    async def _explain_choice(self, data: Dict) -> Dict:
        """Explain why a provider was selected"""
        provider = data.get("provider")
        
        if not provider or provider not in self.provider_scores:
            return {
                "explanation": "No historical data available for this provider",
                "provider": provider
            }
            
        score_data = self.provider_scores[provider]
        history = self.history.get(provider, [])
        
        # Build explanation
        factors = []
        
        if score_data.get("success_rate", 1.0) > 0.95:
            factors.append("High reliability (95%+ success rate)")
            
        if score_data.get("avg_latency", 9999) < 500:
            factors.append("Low latency (<500ms average)")
            
        if score_data.get("cost_efficiency", 0) > 0.8:
            factors.append("Cost efficient")
            
        # Time pattern
        hour = datetime.utcnow().hour
        time_pattern = self.time_patterns.get((hour, datetime.utcnow().weekday()), {})
        if time_pattern.get(provider, 1.0) < 0.9:
            factors.append(f"Historically slow at {hour}:00")
            
        return {
            "provider": provider,
            "explanation": {
                "score": round(score_data.get("score", 0), 2),
                "factors": factors,
                "samples": len(history),
                "last_used": history[-1]["timestamp"] if history else None
            }
        }
        
    # Helper methods
    def _get_providers_for_task(self, task_type: str) -> List[str]:
        """Get providers that support a task type"""
        # Default providers
        providers = {
            "chat": ["deepseek", "groq", "openai", "anthropic"],
            "embed": ["openai", "cohere", "local"],
            "image": ["openai", "stability", "midjourney"]
        }
        
        return providers.get(task_type, ["deepseek"])
        
    def _calculate_provider_score(
        self, 
        provider: str, 
        task_type: str, 
        quality: str, 
        budget: Optional[float]
    ) -> Dict:
        """Calculate composite score for provider"""
        history = self.history.get(provider, [])
        
        if len(history) < self.config["min_samples"]:
            return {
                "score": 0.5,  # Neutral score for unknown
                "confidence": 0.3,
                "reason": "insufficient_data"
            }
            
        # Calculate metrics
        success_rate = sum(1 for h in history if h["success"]) / len(history)
        latencies = [h["latency_ms"] for h in history if h["latency_ms"]]
        avg_latency = sum(latencies) / len(latencies) if latencies else 9999
        
        # Cost efficiency (normalized)
        costs = [h["cost"] for h in history if h["cost"]]
        avg_cost = sum(costs) / len(costs) if costs else 0.01
        
        # Base score from success rate
        score = success_rate * 0.5
        
        # Adjust for latency (lower is better)
        latency_score = max(0, 1 - (avg_latency / 5000))  # 5s = 0 score
        score += latency_score * 0.3
        
        # Adjust for cost if budget constrained
        if budget:
            cost_score = max(0, 1 - (avg_cost / (budget / 1000)))
            score += cost_score * 0.2
            
        # Time-based adjustment
        if self.config["time_aware"]:
            hour = datetime.utcnow().hour
            day = datetime.utcnow().weekday()
            time_factor = self.time_patterns.get((hour, day), {}).get(provider, 1.0)
            score *= time_factor
            
        # Quality preference
        if quality == "fast":
            score = latency_score * 0.7 + success_rate * 0.3
        elif quality == "premium":
            score = success_rate * 0.8 + latency_score * 0.2
            
        return {
            "score": round(score, 3),
            "confidence": min(len(history) / 100, 1.0),
            "reason": f"success_rate={success_rate:.2f}, latency={avg_latency:.0f}ms",
            "metrics": {
                "success_rate": round(success_rate, 2),
                "avg_latency_ms": round(avg_latency, 0),
                "avg_cost": round(avg_cost, 4)
            }
        }
        
    def _update_provider_score(self, provider: str):
        """Update cached score for provider"""
        score_data = self._calculate_provider_score(
            provider, "chat", "standard", None
        )
        self.provider_scores[provider] = score_data
        
    def _update_time_patterns(self, result: Dict):
        """Update time-based performance patterns"""
        hour = result["hour"]
        day = result["day"]
        provider = result["provider"]
        
        key = (hour, day)
        
        if key not in self.time_patterns:
            self.time_patterns[key] = {}
            
        # Calculate multiplier (1.0 = normal, <1.0 = worse)
        if result["success"]:
            latency_factor = max(0.5, 1 - (result.get("latency_ms", 1000) / 2000))
            self.time_patterns[key][provider] = (
                self.time_patterns[key].get(provider, 1.0) * 0.9 + latency_factor * 0.1
            )
        else:
            # Failure reduces score
            self.time_patterns[key][provider] = (
                self.time_patterns[key].get(provider, 1.0) * 0.8
            )
            
    def _estimate_metrics(self, provider: str, task_type: str) -> Dict:
        """Estimate metrics for provider"""
        history = self.history.get(provider, [])
        
        if not history:
            return {"latency": 500, "cost": 0.001, "quality": 0.8}
            
        latencies = [h["latency_ms"] for h in history if h["latency_ms"]]
        costs = [h["cost"] for h in history if h["cost"]]
        
        return {
            "latency": round(sum(latencies) / len(latencies), 0) if latencies else 500,
            "cost": round(sum(costs) / len(costs), 4) if costs else 0.001,
            "quality": 0.95 if provider in ["openai", "anthropic"] else 0.85
        }
        
    def _get_fallback(self, primary: str, providers: List[str]) -> Optional[str]:
        """Get recommended fallback provider"""
        others = [p for p in providers if p != primary]
        if not others:
            return None
            
        # Pick second best
        scores = [(p, self.provider_scores.get(p, {}).get("score", 0)) for p in others]
        scores.sort(key=lambda x: x[1], reverse=True)
        
        return scores[0][0] if scores else None
        
    async def _load_historical_data(self):
        """Load historical data from analytics"""
        if hasattr(self, 'analytics_block') and self.analytics_block:
            # TODO: Load from analytics
            pass
            
    async def _background_learning(self):
        """Background learning task"""
        while True:
            await asyncio.sleep(60)  # Every minute
            
            if self.config["learning_enabled"]:
                # Recalculate all scores
                for provider in self.history:
                    self._update_provider_score(provider)
                    
    def health(self) -> Dict:
        h = super().health()
        h["providers_tracked"] = len(self.history)
        h["total_samples"] = sum(len(v) for v in self.history.values())
        h["time_patterns_learned"] = len(self.time_patterns)
        h["learning_enabled"] = self.config["learning_enabled"]
        return h
