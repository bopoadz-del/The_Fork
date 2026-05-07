"""Block Store Container - The "Lego Tax" Marketplace Engine

Handles:
- Discovery: Block recommendations and search
- Review: Ratings and community reviews
- Payment Split: 20% platform fee, creator payouts
- Version: Semantic versioning for blocks
- Validation: Quality checks and certification
- Billing: Payment processing (Stripe-ready)

Usage:
    store = StoreContainer()
    await store.publish_block(block_data, creator_id)
"""

import hashlib
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
from app.core.block import BaseBlock, BlockConfig


class StoreContainer(BaseBlock):
    """
    Block Store Container - The Cerebrum Marketplace
    
    Third-party developers publish blocks here.
    Platform takes 20% fee on all sales.
    """
    
    def __init__(self):
        super().__init__(BlockConfig(
            name="store",
            version="1.0.0",
            description="Block Store: Discovery, Reviews, Payments, Validation (20% platform fee - Lego Tax)",
            layer=4,
            tags=["integration", "marketplace", "container"],
            requires_api_key=False,
            supported_inputs=["publish", "discover", "review", "purchase"],
            supported_outputs=["published", "discovered", "reviewed", "purchased"]
        ))
        
        # In-memory storage (use database in production)
        self.blocks: Dict[str, Dict] = {}  # block_id -> block info
        self.reviews: Dict[str, List[Dict]] = {}  # block_id -> reviews
        self.creators: Dict[str, Dict] = {}  # creator_id -> creator info
        self.purchases: List[Dict] = []  # purchase history
        
        # Platform config
        self.platform_fee_percent = 20  # The "Lego Tax"
        self.min_price_cents = 100  # $1.00 minimum
        
    async def process(self, input_data: Any, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Main entry point for store operations"""
        params = params or {}
        action = params.get("action", "discover")
        
        if action == "publish":
            return await self._publish_block(params)
        elif action == "discover":
            return await self._discover_blocks(params)
        elif action == "review":
            return await self._submit_review(params)
        elif action == "purchase":
            return await self._purchase_block(params)
        elif action == "validate":
            return await self._validate_block(params)
        elif action == "get_block":
            return await self._get_block(params)
        elif action == "creator_dashboard":
            return await self._creator_dashboard(params)
        elif action == "platform_stats":
            return await self._platform_stats()
        else:
            return {"error": f"Unknown action: {action}"}
    
    # ==================== PUBLISH ====================
    
    async def _publish_block(self, params: Dict) -> Dict:
        """Publish a new block to the store"""
        block_data = params.get("block_data", {})
        creator_id = params.get("creator_id", "anonymous")
        
        # Required fields
        name = block_data.get("name")
        version = block_data.get("version", "1.0.0")
        code = block_data.get("code")
        price_cents = block_data.get("price_cents", 0)  # 0 = free
        
        if not name or not code:
            return {"error": "Block name and code are required"}
        
        # Generate block ID
        block_id = f"{name}@{version}"
        
        # Check if already exists
        if block_id in self.blocks:
            return {"error": f"Block {block_id} already exists"}
        
        # Validate block quality
        validation = await self._validate_quality(code)
        if not validation.get("passed"):
            return {
                "error": "Block validation failed",
                "validation": validation
            }
        
        # Register creator if new
        if creator_id not in self.creators:
            self.creators[creator_id] = {
                "id": creator_id,
                "joined_at": datetime.utcnow().isoformat(),
                "total_earnings_cents": 0,
                "blocks_published": []
            }
        
        # Store block
        self.blocks[block_id] = {
            "id": block_id,
            "name": name,
            "version": version,
            "creator_id": creator_id,
            "description": block_data.get("description", ""),
            "code": code,
            "price_cents": price_cents,
            "tags": block_data.get("tags", []),
            "published_at": datetime.utcnow().isoformat(),
            "downloads": 0,
            "rating_avg": 0.0,
            "rating_count": 0,
            "validation_score": validation.get("score", 0),
            "status": "published"  # published, deprecated, removed
        }
        
        self.creators[creator_id]["blocks_published"].append(block_id)
        
        return {
            "published": True,
            "block_id": block_id,
            "validation_score": validation.get("score"),
            "estimated_creators_cut": f"{100 - self.platform_fee_percent}%"
        }
    
    async def _validate_quality(self, code: str) -> Dict:
        """Validate block code quality (placeholder for real validation)"""
        checks = {
            "has_init": "def __init__" in code or "__init__" in code,
            "has_process": "async def process" in code or "def process" in code,
            "no_hardcoded_secrets": "password" not in code.lower() and "secret" not in code.lower(),
            "has_docstring": '"""' in code or "'''" in code,
        }
        
        score = sum(checks.values()) / len(checks)
        
        return {
            "passed": score >= 0.75,
            "score": round(score, 2),
            "checks": checks
        }
    
    # ==================== DISCOVERY ====================
    
    async def _discover_blocks(self, params: Dict) -> Dict:
        """Discover/recommend blocks"""
        query = params.get("query", "").lower()
        tags = params.get("tags", [])
        sort_by = params.get("sort", "popular")  # popular, newest, rating
        limit = params.get("limit", 10)
        
        results = []
        
        for block_id, block in self.blocks.items():
            # Filter by query
            if query and query not in block["name"].lower() and query not in block["description"].lower():
                continue
            
            # Filter by tags
            if tags and not any(t in block.get("tags", []) for t in tags):
                continue
            
            results.append(block)
        
        # Sort
        if sort_by == "popular":
            results.sort(key=lambda x: x["downloads"], reverse=True)
        elif sort_by == "newest":
            results.sort(key=lambda x: x["published_at"], reverse=True)
        elif sort_by == "rating":
            results.sort(key=lambda x: x["rating_avg"], reverse=True)
        
        return {
            "blocks": results[:limit],
            "total": len(results),
            "query": query,
            "sort": sort_by
        }
    
    # ==================== REVIEWS ====================
    
    async def _submit_review(self, params: Dict) -> Dict:
        """Submit a review for a block"""
        block_id = params.get("block_id")
        user_id = params.get("user_id", "anonymous")
        rating = params.get("rating")  # 1-5
        comment = params.get("comment", "")
        
        if not block_id or block_id not in self.blocks:
            return {"error": "Block not found"}
        
        if not rating or not (1 <= rating <= 5):
            return {"error": "Rating must be 1-5"}
        
        review = {
            "user_id": user_id,
            "rating": rating,
            "comment": comment,
            "created_at": datetime.utcnow().isoformat()
        }
        
        if block_id not in self.reviews:
            self.reviews[block_id] = []
        
        self.reviews[block_id].append(review)
        
        # Update block rating
        block = self.blocks[block_id]
        all_ratings = [r["rating"] for r in self.reviews[block_id]]
        block["rating_avg"] = round(sum(all_ratings) / len(all_ratings), 1)
        block["rating_count"] = len(all_ratings)
        
        return {
            "reviewed": True,
            "block_id": block_id,
            "new_rating": block["rating_avg"],
            "total_reviews": block["rating_count"]
        }
    
    # ==================== PURCHASE ====================
    
    async def _purchase_block(self, params: Dict) -> Dict:
        """Purchase a block (handles payment split)"""
        block_id = params.get("block_id")
        buyer_id = params.get("buyer_id", "anonymous")
        
        if not block_id or block_id not in self.blocks:
            return {"error": "Block not found"}
        
        block = self.blocks[block_id]
        price_cents = block["price_cents"]
        
        # Free block
        if price_cents == 0:
            block["downloads"] += 1
            return {
                "purchased": True,
                "block_id": block_id,
                "price": "free",
                "message": "Free block downloaded"
            }
        
        # Calculate split
        platform_fee = int(price_cents * self.platform_fee_percent / 100)
        creator_earns = price_cents - platform_fee
        
        # Record purchase
        purchase = {
            "block_id": block_id,
            "buyer_id": buyer_id,
            "creator_id": block["creator_id"],
            "price_cents": price_cents,
            "platform_fee_cents": platform_fee,
            "creator_earns_cents": creator_earns,
            "purchased_at": datetime.utcnow().isoformat()
        }
        
        self.purchases.append(purchase)
        block["downloads"] += 1
        
        # Update creator earnings
        creator = self.creators.get(block["creator_id"])
        if creator:
            creator["total_earnings_cents"] += creator_earns
        
        return {
            "purchased": True,
            "block_id": block_id,
            "price": f"${price_cents / 100:.2f}",
            "split": {
                "total": f"${price_cents / 100:.2f}",
                "platform_fee": f"${platform_fee / 100:.2f}",
                "creator_earns": f"${creator_earns / 100:.2f}"
            }
        }
    
    # ==================== OTHER ACTIONS ====================
    
    async def _get_block(self, params: Dict) -> Dict:
        """Get block details"""
        block_id = params.get("block_id")
        
        if not block_id or block_id not in self.blocks:
            return {"error": "Block not found"}
        
        block = self.blocks[block_id]
        block_reviews = self.reviews.get(block_id, [])
        
        return {
            "block": block,
            "reviews": block_reviews[-5:],  # Last 5 reviews
            "total_reviews": len(block_reviews)
        }
    
    async def _creator_dashboard(self, params: Dict) -> Dict:
        """Get creator dashboard stats"""
        creator_id = params.get("creator_id")
        
        if not creator_id or creator_id not in self.creators:
            return {"error": "Creator not found"}
        
        creator = self.creators[creator_id]
        
        # Get block stats
        blocks = [self.blocks[bid] for bid in creator.get("blocks_published", [])]
        total_downloads = sum(b["downloads"] for b in blocks)
        
        return {
            "creator_id": creator_id,
            "total_earnings": f"${creator['total_earnings_cents'] / 100:.2f}",
            "blocks_published": len(blocks),
            "total_downloads": total_downloads,
            "avg_rating": round(sum(b["rating_avg"] for b in blocks) / len(blocks), 1) if blocks else 0,
            "blocks": blocks
        }
    
    async def _platform_stats(self) -> Dict:
        """Get platform-wide stats"""
        total_sales = sum(p["price_cents"] for p in self.purchases)
        platform_revenue = sum(p["platform_fee_cents"] for p in self.purchases)
        
        return {
            "total_blocks": len(self.blocks),
            "total_creators": len(self.creators),
            "total_purchases": len(self.purchases),
            "total_sales": f"${total_sales / 100:.2f}",
            "platform_revenue": f"${platform_revenue / 100:.2f}",
            "platform_fee_percent": self.platform_fee_percent,
            "top_blocks": sorted(
                self.blocks.values(),
                key=lambda x: x["downloads"],
                reverse=True
            )[:5]
        }
