"""Dashboard Block - Real-time UI dashboard with widgets"""
from blocks.base import LegoBlock
from typing import Dict, Any, List
import asyncio
import json
from datetime import datetime


class DashboardBlock(LegoBlock):
    """
    Dashboard Block - Aggregates data from all blocks for UI display
    
    Features:
    - Widget registry (charts, tables, metrics)
    - User-specific layouts
    - Real-time data streams via WebSocket simulation
    - Aggregates from monitoring, auth, billing blocks
    """
    name = "dashboard"
    version = "1.0.0"
    requires = ["auth", "monitoring"]
    layer = 3  # Domain layer
    tags = ["ui", "platform", "dashboard", "domain"]
    default_config = {
        "default_layout": "grid",
        "refresh_interval": 30,  # seconds
        "max_widgets": 20,
        "theme": "light"
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.widgets: Dict[str, Dict] = {}  # widget_id -> widget_config
        self.layouts: Dict[str, Dict] = {}  # user_id -> layout
        self.data_streams: Dict[str, asyncio.Task] = {}
        self.subscribers: List[callable] = []
        
    async def initialize(self) -> bool:
        """Initialize dashboard with widget registry"""
        print("📊 Dashboard Block initialized")
        
        # Register default widgets
        self._register_default_widgets()
        
        # Load layouts from memory if available
        if hasattr(self, 'memory_block') and self.memory_block:
            layouts_data = await self.memory_block.execute({
                "action": "get", 
                "key": "dashboard:layouts"
            })
            if layouts_data.get("hit"):
                self.layouts = layouts_data.get("value", {})
                print(f"   Loaded {len(self.layouts)} user layouts")
        
        # Start background data refresh
        asyncio.create_task(self._background_refresh())
        
        self.initialized = True
        return True
    
    def _register_default_widgets(self):
        """Register built-in dashboard widgets"""
        default_widgets = {
            "system_health": {
                "type": "status_grid",
                "title": "System Health",
                "data_source": "monitoring",
                "refresh": 10,
                "position": {"x": 0, "y": 0, "w": 2, "h": 1}
            },
            "provider_leaderboard": {
                "type": "chart",
                "chart_type": "bar",
                "title": "AI Provider Performance",
                "data_source": "monitoring",
                "refresh": 60,
                "position": {"x": 2, "y": 0, "w": 2, "h": 2}
            },
            "api_usage": {
                "type": "metric_cards",
                "title": "API Usage Today",
                "data_source": "auth",
                "metrics": ["requests", "errors", "latency"],
                "refresh": 30,
                "position": {"x": 0, "y": 1, "w": 2, "h": 1}
            },
            "recent_activity": {
                "type": "table",
                "title": "Recent API Calls",
                "data_source": "memory",
                "columns": ["time", "user", "block", "status"],
                "limit": 10,
                "refresh": 15,
                "position": {"x": 0, "y": 2, "w": 4, "h": 2}
            },
            "cost_overview": {
                "type": "chart",
                "chart_type": "line",
                "title": "Cost Trends",
                "data_source": "billing",
                "refresh": 300,
                "position": {"x": 0, "y": 4, "w": 2, "h": 2}
            }
        }
        
        for widget_id, config in default_widgets.items():
            self.widgets[widget_id] = {
                **config,
                "id": widget_id,
                "enabled": True,
                "created_at": datetime.utcnow().isoformat()
            }
        
        print(f"   Registered {len(self.widgets)} default widgets")
    
    async def execute(self, input_data: Dict) -> Dict:
        """Handle dashboard actions"""
        action = input_data.get("action")
        
        if action == "render":
            return await self._render_dashboard(input_data)
        elif action == "add_widget":
            return await self._add_widget(input_data)
        elif action == "remove_widget":
            return await self._remove_widget(input_data)
        elif action == "update_widget":
            return await self._update_widget(input_data)
        elif action == "get_metrics":
            return await self._get_metrics(input_data)
        elif action == "list_widgets":
            return {"widgets": list(self.widgets.values())}
        elif action == "save_layout":
            return await self._save_layout(input_data)
        elif action == "get_layout":
            return await self._get_layout(input_data)
        elif action == "subscribe_stream":
            return await self._subscribe_stream(input_data)
        elif action == "get_snapshot":
            return await self._get_snapshot(input_data)
            
        return {"error": f"Unknown action: {action}"}
    
    async def _render_dashboard(self, data: Dict) -> Dict:
        """Render dashboard for user with live data"""
        user_id = data.get("user_id", "default")
        layout = self.layouts.get(user_id, self._get_default_layout())
        
        # Fetch live data for each widget
        widgets_with_data = []
        for widget_id in layout.get("widgets", list(self.widgets.keys())):
            if widget_id in self.widgets:
                widget = self.widgets[widget_id].copy()
                widget["data"] = await self._fetch_widget_data(widget)
                widgets_with_data.append(widget)
        
        return {
            "layout": layout,
            "widgets": widgets_with_data,
            "theme": self.config.get("theme", "light"),
            "refresh_interval": self.config.get("refresh_interval", 30),
            "generated_at": datetime.utcnow().isoformat()
        }
    
    async def _fetch_widget_data(self, widget: Dict) -> Dict:
        """Fetch live data for a widget from its source"""
        source = widget.get("data_source")
        
        try:
            if source == "monitoring" and hasattr(self, 'monitoring_block'):
                if widget["type"] == "chart" and "leaderboard" in widget["title"].lower():
                    return await self.monitoring_block.execute({"action": "leaderboard"})
                else:
                    return await self.monitoring_block.execute({"action": "health_report"})
                    
            elif source == "auth" and hasattr(self, 'auth_block'):
                return await self.auth_block.execute({
                    "action": "get_usage",
                    "key": "*",
                    "window": "24h"
                })
                
            elif source == "memory" and hasattr(self, 'memory_block'):
                return await self.memory_block.execute({"action": "stats"})
                
        except Exception as e:
            return {"error": str(e), "source": source}
        
        return {"status": "no_data", "source": source}
    
    async def _add_widget(self, data: Dict) -> Dict:
        """Add custom widget to dashboard"""
        widget_id = data.get("widget_id", f"custom_{len(self.widgets)}")
        
        if len(self.widgets) >= self.config.get("max_widgets", 20):
            return {"error": "Maximum widgets reached"}
        
        self.widgets[widget_id] = {
            "id": widget_id,
            "type": data.get("type", "metric"),
            "title": data.get("title", "New Widget"),
            "data_source": data.get("data_source"),
            "config": data.get("config", {}),
            "position": data.get("position", {"x": 0, "y": 0, "w": 1, "h": 1}),
            "enabled": True,
            "created_at": datetime.utcnow().isoformat()
        }
        
        return {"added": True, "widget_id": widget_id, "total": len(self.widgets)}
    
    async def _remove_widget(self, data: Dict) -> Dict:
        """Remove widget from dashboard"""
        widget_id = data.get("widget_id")
        if widget_id in self.widgets:
            del self.widgets[widget_id]
            return {"removed": True, "widget_id": widget_id}
        return {"error": "Widget not found"}
    
    async def _update_widget(self, data: Dict) -> Dict:
        """Update widget configuration"""
        widget_id = data.get("widget_id")
        if widget_id not in self.widgets:
            return {"error": "Widget not found"}
        
        updates = data.get("updates", {})
        self.widgets[widget_id].update(updates)
        
        return {"updated": True, "widget_id": widget_id}
    
    async def _get_metrics(self, data: Dict) -> Dict:
        """Get aggregated metrics from monitoring"""
        metrics = {}
        
        if hasattr(self, 'monitoring_block'):
            metrics["providers"] = await self.monitoring_block.execute({
                "action": "leaderboard"
            })
            metrics["health"] = await self.monitoring_block.execute({
                "action": "health_report"
            })
        
        if hasattr(self, 'auth_block'):
            metrics["usage"] = await self.auth_block.execute({
                "action": "get_usage",
                "key": "*"
            })
        
        return {
            "metrics": metrics,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def _save_layout(self, data: Dict) -> Dict:
        """Save user-specific layout"""
        user_id = data.get("user_id", "default")
        layout = data.get("layout", {})
        
        self.layouts[user_id] = {
            **layout,
            "saved_at": datetime.utcnow().isoformat()
        }
        
        # Persist to memory if available
        if hasattr(self, 'memory_block') and self.memory_block:
            await self.memory_block.execute({
                "action": "set",
                "key": "dashboard:layouts",
                "value": self.layouts,
                "ttl": 86400 * 7  # 7 days
            })
        
        return {"saved": True, "user_id": user_id}
    
    async def _get_layout(self, data: Dict) -> Dict:
        """Get user layout"""
        user_id = data.get("user_id", "default")
        return {
            "layout": self.layouts.get(user_id, self._get_default_layout()),
            "user_id": user_id
        }
    
    def _get_default_layout(self) -> Dict:
        """Get default layout configuration"""
        return {
            "name": "default",
            "type": self.config.get("default_layout", "grid"),
            "widgets": list(self.widgets.keys())[:6],  # First 6 widgets
            "columns": 4,
            "row_height": 100
        }
    
    async def _subscribe_stream(self, data: Dict) -> Dict:
        """Subscribe to real-time dashboard updates"""
        callback = data.get("callback")
        if callback:
            self.subscribers.append(callback)
        return {"subscribed": True, "subscribers": len(self.subscribers)}
    
    async def _get_snapshot(self, data: Dict) -> Dict:
        """Get full dashboard snapshot for export"""
        return {
            "widgets": self.widgets,
            "layouts": self.layouts,
            "config": self.config,
            "generated_at": datetime.utcnow().isoformat()
        }
    
    async def _background_refresh(self):
        """Background task to refresh data periodically"""
        while True:
            try:
                await asyncio.sleep(self.config.get("refresh_interval", 30))
                
                # Notify subscribers of refresh
                for subscriber in self.subscribers[:]:
                    try:
                        if asyncio.iscoroutinefunction(subscriber):
                            asyncio.create_task(subscriber({"type": "refresh"}))
                    except Exception:
                        self.subscribers.remove(subscriber)
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Dashboard refresh error: {e}")
    
    def health(self) -> Dict:
        """Dashboard health status"""
        h = super().health()
        h["widgets"] = len(self.widgets)
        h["layouts"] = len(self.layouts)
        h["subscribers"] = len(self.subscribers)
        h["theme"] = self.config.get("theme", "light")
        return h
