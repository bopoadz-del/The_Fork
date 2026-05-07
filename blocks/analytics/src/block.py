"""Analytics Block - Metrics aggregation, reporting, and predictive analysis"""
from blocks.base import LegoBlock
from typing import Dict, Any, List
from collections import defaultdict, deque
import time
import statistics
import asyncio
from datetime import datetime, timedelta


class AnalyticsBlock(LegoBlock):
    """
    Analytics Block - Time-series metrics and predictive analysis
    
    Features:
    - Time-series data collection
    - Provider performance tracking
    - Usage analytics
    - Predictive failure detection
    - Custom dashboards/reporting
    - Cost analysis
    """
    name = "analytics"
    version = "1.0.0"
    requires = ["database", "monitoring"]
    layer = 2  # Core layer
    tags = ["metrics", "reporting", "analytics", "platform"]
    default_config = {
        "retention_hours": 168,  # 7 days
        "aggregation_interval": 300,  # 5 minutes
        "prediction_window": 10,  # samples for prediction
        "cost_per_1k_requests": {
            "deepseek": 0.14,
            "groq": 0.59,
            "openai": 2.50
        }
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.time_series = defaultdict(lambda: deque(maxlen=10000))
        self.aggregations = defaultdict(dict)
        self.predictions = {}
        
    async def initialize(self) -> bool:
        """Initialize analytics with time-series storage"""
        print("📈 Analytics Block initialized")
        print(f"   Retention: {self.config.get('retention_hours', 168)} hours")
        print(f"   Aggregation: {self.config.get('aggregation_interval', 300)} seconds")
        
        # Start background aggregation
        asyncio.create_task(self._background_aggregation())
        
        self.initialized = True
        return True
    
    async def execute(self, input_data: Dict) -> Dict:
        """Handle analytics actions"""
        action = input_data.get("action")
        
        if action == "track_event":
            return await self._track_event(input_data)
        elif action == "leaderboard":
            return await self._provider_leaderboard(input_data)
        elif action == "usage_report":
            return await self._usage_analytics(input_data)
        elif action == "predict_failure":
            return await self._predictive_analysis(input_data)
        elif action == "cost_analysis":
            return await self._cost_analysis(input_data)
        elif action == "get_metrics":
            return await self._get_metrics(input_data)
        elif action == "compare_providers":
            return await self._compare_providers(input_data)
            
        return {"error": f"Unknown action: {action}"}
    
    async def _track_event(self, data: Dict) -> Dict:
        """Track time-series event"""
        metric_name = data.get("metric")
        value = data.get("value")
        tags = data.get("tags", {})
        timestamp = data.get("timestamp", time.time())
        
        if not metric_name or value is None:
            return {"error": "metric and value required"}
        
        event = {
            "timestamp": timestamp,
            "value": value,
            "tags": tags
        }
        
        self.time_series[metric_name].append(event)
        
        # Also store in memory if available
        if hasattr(self, 'memory_block') and self.memory_block:
            await self.memory_block.execute({
                "action": "set",
                "key": f"analytics:{metric_name}:{int(timestamp)}",
                "value": event,
                "ttl": self.config.get("retention_hours", 168) * 3600
            })
        
        return {"tracked": True, "metric": metric_name, "timestamp": timestamp}
    
    async def _provider_leaderboard(self, data: Dict) -> Dict:
        """Generate provider performance leaderboard"""
        # Get data from monitoring block
        monitoring_data = {}
        if hasattr(self, 'monitoring_block') and self.monitoring_block:
            monitoring_data = await self.monitoring_block.execute({
                "action": "leaderboard"
            })
        
        providers = monitoring_data.get("leaderboard", [])
        
        # Enrich with cost data
        cost_map = self.config.get("cost_per_1k_requests", {})
        
        enriched = []
        for provider in providers:
            name = provider.get("name", "unknown").lower()
            cost = cost_map.get(name, 0)
            
            # Calculate value score (reliability / cost)
            reliability = provider.get("reliability_score", 0)
            value_score = reliability / (cost + 0.01)  # Avoid div by zero
            
            enriched.append({
                **provider,
                "cost_per_1k": cost,
                "value_score": round(value_score, 2),
                "recommendation": self._get_recommendation(reliability, cost)
            })
        
        # Sort by value score
        enriched.sort(key=lambda x: x["value_score"], reverse=True)
        
        return {
            "leaderboard": enriched,
            "generated_at": datetime.utcnow().isoformat(),
            "criteria": "value_score (reliability / cost)"
        }
    
    def _get_recommendation(self, reliability: float, cost: float) -> str:
        """Get recommendation based on reliability and cost"""
        if reliability > 95 and cost < 0.50:
            return "🏆 Best Value - Use as default"
        elif reliability > 90:
            return "✅ Reliable - Good for production"
        elif cost < 0.30:
            return "💰 Budget - Use for non-critical"
        else:
            return "⚠️ Expensive - Use sparingly"
    
    async def _usage_analytics(self, data: Dict) -> Dict:
        """Generate usage analytics report"""
        period = data.get("period", "24h")
        
        # Parse period
        hours = self._parse_period(period)
        since = time.time() - (hours * 3600)
        
        # Aggregate metrics
        metrics = {}
        for metric_name, events in self.time_series.items():
            recent = [e for e in events if e["timestamp"] > since]
            if recent:
                values = [e["value"] for e in recent]
                metrics[metric_name] = {
                    "count": len(values),
                    "sum": sum(values),
                    "avg": round(statistics.mean(values), 2),
                    "min": min(values),
                    "max": max(values),
                    "p95": round(self._percentile(values, 95), 2) if len(values) > 1 else values[0]
                }
        
        # Get from monitoring
        health_data = {}
        if hasattr(self, 'monitoring_block') and self.monitoring_block:
            health_data = await self.monitoring_block.execute({
                "action": "health_report"
            })
        
        return {
            "period": period,
            "metrics": metrics,
            "health": health_data,
            "generated_at": datetime.utcnow().isoformat()
        }
    
    async def _predictive_analysis(self, data: Dict) -> Dict:
        """Predict failures based on trend analysis"""
        provider = data.get("provider", "deepseek")
        window = data.get("window", self.config.get("prediction_window", 10))
        
        # Get recent latency/error data
        metric_name = f"{provider}_latency"
        events = list(self.time_series.get(metric_name, []))
        
        if len(events) < window:
            return {"error": f"Not enough data for {provider} (need {window}, have {len(events)})"}
        
        # Simple linear regression for trend
        recent = events[-window:]
        values = [e["value"] for e in recent]
        
        # Calculate trend
        trend = self._calculate_trend(values)
        
        # Predict next value
        next_value = values[-1] + trend
        
        # Calculate confidence
        volatility = statistics.stdev(values) if len(values) > 1 else 0
        confidence = max(0, 1 - (volatility / (statistics.mean(values) + 0.001)))
        
        # Determine risk level
        threshold = data.get("threshold", 1000)  # ms
        risk = "low"
        if next_value > threshold * 1.5:
            risk = "critical"
        elif next_value > threshold:
            risk = "high"
        elif trend > 0:
            risk = "medium"
        
        prediction = {
            "provider": provider,
            "current_avg": round(statistics.mean(values), 2),
            "predicted_next": round(next_value, 2),
            "trend": "increasing" if trend > 0 else "decreasing",
            "risk_level": risk,
            "confidence": round(confidence, 2),
            "recommendation": self._get_prediction_recommendation(risk, provider)
        }
        
        self.predictions[provider] = prediction
        
        return prediction
    
    def _calculate_trend(self, values: List[float]) -> float:
        """Simple trend calculation"""
        if len(values) < 2:
            return 0
        # Average change
        changes = [values[i] - values[i-1] for i in range(1, len(values))]
        return statistics.mean(changes)
    
    def _get_prediction_recommendation(self, risk: str, provider: str) -> str:
        """Get recommendation based on prediction"""
        if risk == "critical":
            return f"🚨 Switch from {provider} immediately - predicted failure"
        elif risk == "high":
            return f"⚠️  Consider failover from {provider}"
        elif risk == "medium":
            return f"👀 Monitor {provider} closely"
        else:
            return f"✅ {provider} is stable"
    
    async def _cost_analysis(self, data: Dict) -> Dict:
        """Analyze costs across providers"""
        period = data.get("period", "30d")
        hours = self._parse_period(period)
        
        # Get usage counts per provider
        provider_usage = defaultdict(int)
        
        # From time series
        for metric_name, events in self.time_series.items():
            if "request" in metric_name:
                provider = metric_name.split("_")[0]
                recent = [e for e in events if e["timestamp"] > time.time() - hours * 3600]
                provider_usage[provider] += len(recent)
        
        # Calculate costs
        cost_map = self.config.get("cost_per_1k_requests", {})
        breakdown = []
        total_cost = 0
        
        for provider, count in provider_usage.items():
            cost_per_1k = cost_map.get(provider, 0)
            cost = (count / 1000) * cost_per_1k
            total_cost += cost
            
            breakdown.append({
                "provider": provider,
                "requests": count,
                "cost_per_1k": cost_per_1k,
                "total_cost": round(cost, 4)
            })
        
        # Sort by cost
        breakdown.sort(key=lambda x: x["total_cost"], reverse=True)
        
        return {
            "period": period,
            "total_cost_usd": round(total_cost, 4),
            "breakdown": breakdown,
            "projected_monthly": round(total_cost * (720 / hours), 2),  # 720 hours in 30 days
            "generated_at": datetime.utcnow().isoformat()
        }
    
    async def _get_metrics(self, data: Dict) -> Dict:
        """Get raw metrics"""
        metric_name = data.get("metric")
        since = data.get("since", time.time() - 3600)  # 1 hour default
        
        if metric_name:
            events = [e for e in self.time_series.get(metric_name, []) 
                     if e["timestamp"] > since]
            return {
                "metric": metric_name,
                "count": len(events),
                "data": events
            }
        
        # Return all metrics summary
        return {
            "metrics": list(self.time_series.keys()),
            "counts": {k: len(v) for k, v in self.time_series.items()}
        }
    
    async def _compare_providers(self, data: Dict) -> Dict:
        """Compare providers across multiple dimensions"""
        providers = data.get("providers", ["deepseek", "groq", "openai"])
        metrics = ["latency", "reliability", "cost"]
        
        comparison = {}
        for provider in providers:
            provider_data = {}
            
            # Get data from monitoring
            if hasattr(self, 'monitoring_block') and self.monitoring_block:
                health = await self.monitoring_block.execute({
                    "action": "health_report"
                })
                provider_data["health"] = health
            
            # Get cost
            cost_map = self.config.get("cost_per_1k_requests", {})
            provider_data["cost_per_1k"] = cost_map.get(provider.lower(), 0)
            
            comparison[provider] = provider_data
        
        return {
            "comparison": comparison,
            "metrics_considered": metrics,
            "winner": self._determine_winner(comparison)
        }
    
    def _determine_winner(self, comparison: Dict) -> str:
        """Determine best provider based on comparison"""
        scores = {}
        for provider, data in comparison.items():
            score = 0
            # Lower cost is better
            cost = data.get("cost_per_1k", 10)
            score += (10 - cost) * 10  # Cost score
            scores[provider] = score
        
        if scores:
            return max(scores, key=scores.get)
        return "unknown"
    
    def _parse_period(self, period: str) -> int:
        """Parse period string to hours"""
        if period.endswith("h"):
            return int(period[:-1])
        elif period.endswith("d"):
            return int(period[:-1]) * 24
        elif period.endswith("m"):
            return int(period[:-1]) * 24 * 30
        return 24  # default 24h
    
    def _percentile(self, values: List[float], p: float) -> float:
        """Calculate percentile"""
        sorted_vals = sorted(values)
        k = (len(sorted_vals) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_vals) else f
        return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])
    
    async def _background_aggregation(self):
        """Background task to aggregate metrics"""
        while True:
            try:
                await asyncio.sleep(self.config.get("aggregation_interval", 300))
                
                # Aggregate old data
                cutoff = time.time() - (self.config.get("retention_hours", 168) * 3600)
                
                for metric_name in list(self.time_series.keys()):
                    queue = self.time_series[metric_name]
                    # Remove old events (deque handles this automatically with maxlen)
                    
                    # Calculate aggregates
                    if queue:
                        values = [e["value"] for e in queue]
                        self.aggregations[metric_name] = {
                            "count": len(values),
                            "avg": statistics.mean(values),
                            "last_updated": time.time()
                        }
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Analytics aggregation error: {e}")
    
    def health(self) -> Dict:
        """Analytics health"""
        h = super().health()
        h["metrics_tracked"] = len(self.time_series)
        h["total_events"] = sum(len(v) for v in self.time_series.values())
        h["aggregations"] = len(self.aggregations)
        return h
