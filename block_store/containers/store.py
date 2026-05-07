"""Store Container - Block Store marketplace"""

import time
from typing import Any, Dict
from app.core.universal_base import UniversalContainer


class StoreContainer(UniversalContainer):
    """
    Store Container: Discovery, Reviews, Payments, Validation (Lego Tax)
    """
    
    name = "store"
    version = "1.0"
    description = "Block Store: Discovery, Reviews, Payments, Validation (20% platform fee - Lego Tax)"
    layer = 4  # Integration
    tags = ["integration", "container", "marketplace"]
    requires = []
    
    PLATFORM_FEE = 0.20  # 20% Lego Tax

    ui_schema = {
        "input": {
            "type": "text",
            "accept": None,
            "placeholder": "Search blocks or describe what you want to build...",
            "multiline": False
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "blocks", "type": "json", "label": "Blocks Found"},
                {"name": "total", "type": "number", "label": "Total Results"}
            ]
        },
        "quick_actions": [
            {"icon": "🔍", "label": "Discover Blocks", "prompt": '{"action":"discover","tag":"ai"}'},
            {"icon": "📦", "label": "Publish Block", "prompt": '{"action":"publish","name":"my-block","price_cents":0}'},
            {"icon": "📊", "label": "Platform Stats", "prompt": '{"action":"platform_stats"}'},
            {"icon": "⭐", "label": "Top Rated", "prompt": '{"action":"discover","sort":"rating","limit":10}'}
        ]
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.blocks = {}
        self.reviews = {}
        self.purchases = []
    
    async def route(self, action: str, input_data: Any, params: Dict) -> Dict:
        if action == "publish":
            return await self.publish(input_data, params)
        elif action == "discover":
            return await self.discover(params)
        elif action == "review":
            return await self.review(params)
        elif action == "purchase":
            return await self.purchase(params)
        elif action == "platform_stats":
            return await self.platform_stats()
        elif action == "health_check":
            return await self.health_check()
        else:
            return {"error": f"Unknown action: {action}"}
    
    async def publish(self, code: str, params: Dict) -> Dict:
        """Publish a block to store"""
        # Validation checks
        checks = {
            "has_init": "__init__" in code,
            "has_process": "async def process" in code,
            "no_hardcoded_secrets": "password" not in code.lower()
        }
        score = sum(checks.values()) / len(checks)
        
        block_id = params.get("name", f"block_{int(time.time())}")
        
        if score >= 0.8:
            self.blocks[block_id] = {
                "name": block_id,
                "author": params.get("author", "anonymous"),
                "price_cents": params.get("price_cents", 0),
                "validation_score": score,
                "published_at": time.time()
            }
        
        return {
            "status": "success",
            "published": score >= 0.8,
            "block_id": block_id,
            "validation_score": score,
            "checks": checks
        }
    
    async def discover(self, params: Dict) -> Dict:
        """Discover blocks with filters"""
        tag_filter = params.get("tag")
        search = params.get("search", "").lower()
        
        results = list(self.blocks.values())
        
        if search:
            results = [b for b in results if search in b.get("name", "").lower()]
        
        return {
            "status": "success",
            "total": len(results),
            "blocks": results[:20]  # Limit results
        }
    
    async def review(self, params: Dict) -> Dict:
        """Submit a review"""
        block_id = params.get("block_id")
        rating = params.get("rating", 5)
        
        if block_id not in self.reviews:
            self.reviews[block_id] = []
        
        self.reviews[block_id].append({
            "rating": rating,
            "comment": params.get("comment", ""),
            "timestamp": time.time()
        })
        
        return {"status": "success", "review_submitted": True}
    
    async def purchase(self, params: Dict) -> Dict:
        """Process purchase with Lego Tax"""
        price_cents = params.get("price_cents", 0)
        
        platform_fee = int(price_cents * self.PLATFORM_FEE)
        creator_earns = price_cents - platform_fee
        
        self.purchases.append({
            "block_id": params.get("block_id"),
            "price_cents": price_cents,
            "platform_fee": platform_fee,
            "creator_earns": creator_earns,
            "timestamp": time.time()
        })
        
        return {
            "status": "success",
            "price": f"${price_cents/100:.2f}",
            "platform_fee": f"${platform_fee/100:.2f}",
            "creator_earns": f"${creator_earns/100:.2f}",
            "lego_tax_rate": "20%"
        }
    
    async def platform_stats(self) -> Dict:
        """Get platform statistics"""
        total_revenue = sum(p["platform_fee"] for p in self.purchases)
        
        return {
            "status": "success",
            "total_blocks": len(self.blocks),
            "published_blocks": len(self.blocks),
            "total_reviews": sum(len(r) for r in self.reviews.values()),
            "total_purchases": len(self.purchases),
            "platform_revenue_cents": total_revenue,
            "lego_tax_rate": "20%"
        }
    
    async def health_check(self) -> Dict:
        return {
            "status": "healthy",
            "container": self.name,
            "capabilities": ["publish", "discover", "review", "purchase", "platform_stats"],
            "blocks_listed": len(self.blocks)
        }
