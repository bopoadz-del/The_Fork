"""Discovery Block - AI-powered block recommendation engine

Suggests blocks based on user's current stack and goals.
Uses vector similarity and usage patterns for recommendations.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import asyncio
from dataclasses import dataclass
from datetime import datetime
import json


@dataclass
class BlockProfile:
    """Profile of a block for discovery"""
    block_id: str
    name: str
    description: str
    tags: List[str]
    layer: int
    requires: List[str]
    complements: List[str]  # Blocks that work well with this
    use_cases: List[str]    # Text descriptions of use cases
    popularity_score: float
    avg_rating: float
    install_count: int


class DiscoveryBlock(LegoBlock):
    """
    AI-powered block recommendation engine.
    Suggests blocks based on user's current stack and goals.
    """
    name = "discovery"
    version = "1.0.0"
    requires = ["vector", "analytics"]
    layer = 4
    tags = ["store", "ai", "recommendation", "discovery"]
    
    default_config = {
        "recommendation_model": "embedding_similarity",  # or "collaborative", "hybrid"
        "max_suggestions": 5,
        "min_similarity_score": 0.6,
        "boost_popular": True,
        "boost_rated": True,
        "include_installed": False
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.block_profiles: Dict[str, BlockProfile] = {}
        self.user_stacks: Dict[str, List[str]] = {}  # user_id -> installed blocks
        self.usage_patterns: List[Dict] = []  # Aggregated usage patterns
        
    async def initialize(self) -> bool:
        """Initialize discovery engine"""
        print("🔍 Discovery Block initializing...")
        print(f"   Model: {self.config['recommendation_model']}")
        print(f"   Max suggestions: {self.config['max_suggestions']}")
        
        # Index built-in blocks
        await self._index_builtin_blocks()
        
        # TODO: Load from vector database
        # TODO: Load usage patterns from analytics
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute discovery actions"""
        action = input_data.get("action")
        
        actions = {
            "recommend_for_project": self._recommend_stack,
            "find_alternative": self._find_alternatives,
            "search_blocks": self._semantic_search,
            "trending": self._trending_blocks,
            "get_compatible": self._get_compatible,
            "index_block": self._index_block,
            "get_categories": self._get_categories,
            "smart_stack": self._smart_stack_builder
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _index_builtin_blocks(self):
        """Index known blocks into discovery"""
        builtin_blocks = [
            BlockProfile(
                block_id="chat",
                name="Chat",
                description="AI chat with multi-provider fallback",
                tags=["ai", "text", "conversation"],
                layer=2,
                requires=[],
                complements=["memory", "vector", "webhook"],
                use_cases=["customer support", "content generation", "coding assistant"],
                popularity_score=0.95,
                avg_rating=4.5,
                install_count=10000
            ),
            BlockProfile(
                block_id="pdf",
                name="PDF Processor",
                description="Extract text and data from PDFs",
                tags=["document", "extraction", "pdf"],
                layer=2,
                requires=[],
                complements=["ocr", "chat", "storage"],
                use_cases=["document analysis", "invoice processing", "form extraction"],
                popularity_score=0.88,
                avg_rating=4.3,
                install_count=7500
            ),
            BlockProfile(
                block_id="vector",
                name="Vector Store",
                description="Semantic search with embeddings",
                tags=["search", "ai", "embeddings"],
                layer=1,
                requires=[],
                complements=["chat", "memory", "discovery"],
                use_cases=["semantic search", "knowledge base", "recommendations"],
                popularity_score=0.82,
                avg_rating=4.4,
                install_count=6000
            ),
            BlockProfile(
                block_id="storage",
                name="Storage",
                description="Multi-provider file storage",
                tags=["files", "cloud", "storage"],
                layer=1,
                requires=[],
                complements=["pdf", "image", "ocr"],
                use_cases=["file uploads", "document storage", "backups"],
                popularity_score=0.90,
                avg_rating=4.2,
                install_count=8500
            ),
            BlockProfile(
                block_id="webhook",
                name="Webhook",
                description="HTTP callbacks and API integrations",
                tags=["integration", "http", "api"],
                layer=2,
                requires=[],
                complements=["workflow", "chat", "notification"],
                use_cases=["notifications", "external integrations", "event handling"],
                popularity_score=0.85,
                avg_rating=4.1,
                install_count=7000
            ),
            BlockProfile(
                block_id="workflow",
                name="Workflow",
                description="Multi-step automation chains",
                tags=["automation", "orchestration", "pipeline"],
                layer=3,
                requires=[],
                complements=["webhook", "chat", "database"],
                use_cases=["automated pipelines", "business processes", "scheduled tasks"],
                popularity_score=0.78,
                avg_rating=4.0,
                install_count=5500
            ),
            BlockProfile(
                block_id="database",
                name="Database",
                description="Structured data storage with ORM",
                tags=["data", "storage", "sql"],
                layer=1,
                requires=[],
                complements=["auth", "billing", "analytics"],
                use_cases=["user data", "transactions", "metadata storage"],
                popularity_score=0.92,
                avg_rating=4.3,
                install_count=9000
            ),
            BlockProfile(
                block_id="auth",
                name="Auth",
                description="Multi-provider authentication",
                tags=["security", "users", "authentication"],
                layer=0,
                requires=[],
                complements=["database", "team", "billing"],
                use_cases=["user login", "api keys", "oauth integration"],
                popularity_score=0.93,
                avg_rating=4.4,
                install_count=9500
            ),
            BlockProfile(
                block_id="analytics",
                name="Analytics",
                description="Time-series metrics and insights",
                tags=["metrics", "monitoring", "insights"],
                layer=2,
                requires=["database"],
                complements=["monitoring", "dashboard", "billing"],
                use_cases=["usage tracking", "performance metrics", "cost analysis"],
                popularity_score=0.75,
                avg_rating=4.2,
                install_count=5000
            ),
            BlockProfile(
                block_id="sandbox",
                name="Sandbox",
                description="Secure code execution environment",
                tags=["security", "execution", "isolation"],
                layer=1,
                requires=[],
                complements=["code", "validation", "secrets"],
                use_cases=["safe code execution", "testing", "validation"],
                popularity_score=0.70,
                avg_rating=4.0,
                install_count=4000
            ),
            BlockProfile(
                block_id="dashboard",
                name="Dashboard",
                description="Real-time UI dashboards",
                tags=["ui", "visualization", "real-time"],
                layer=3,
                requires=["config", "memory"],
                complements=["analytics", "monitoring", "container"],
                use_cases=["admin panels", "metrics display", "status pages"],
                popularity_score=0.80,
                avg_rating=4.3,
                install_count=6200
            ),
            BlockProfile(
                block_id="secrets",
                name="Secrets",
                description="Encrypted credential storage",
                tags=["security", "encryption", "vault"],
                layer=0,
                requires=[],
                complements=["auth", "sandbox", "database"],
                use_cases=["api keys", "passwords", "certificates"],
                popularity_score=0.85,
                avg_rating=4.5,
                install_count=7000
            )
        ]
        
        for profile in builtin_blocks:
            self.block_profiles[profile.block_id] = profile
            
        print(f"   ✓ Indexed {len(builtin_blocks)} blocks")
        
    async def _recommend_stack(self, data: Dict) -> Dict:
        """Recommend blocks based on current stack and goal"""
        current_stack = data.get("current_stack", [])  # ["chat", "pdf"]
        goal = data.get("goal", "")  # "I want to analyze construction documents"
        user_id = data.get("user_id")
        
        if not current_stack and not goal:
            return {"error": "Provide current_stack or goal for recommendations"}
            
        scores = {}
        
        for block_id, profile in self.block_profiles.items():
            # Skip already installed
            if block_id in current_stack and not self.config["include_installed"]:
                continue
                
            score = 0.0
            reasons = []
            
            # 1. Complementarity score - does it work well with current stack?
            for installed in current_stack:
                if installed in profile.complements:
                    score += 0.3
                    reasons.append(f"Complements {installed}")
                if block_id in self.block_profiles.get(installed, BlockProfile(
                    "", "", "", [], 0, [], [], [], 0, 0, 0
                )).complements:
                    score += 0.2
                    reasons.append(f"{installed} works well with this")
                    
            # 2. Goal similarity - semantic match with use cases
            if goal:
                goal_lower = goal.lower()
                for use_case in profile.use_cases:
                    # Simple keyword matching (TODO: use embeddings)
                    keywords = use_case.lower().split()
                    matches = sum(1 for kw in keywords if kw in goal_lower)
                    if matches > 0:
                        score += 0.25 * (matches / len(keywords))
                        reasons.append(f"Matches goal: {use_case}")
                        
            # 3. Popularity boost
            if self.config["boost_popular"]:
                score += profile.popularity_score * 0.1
                
            # 4. Rating boost
            if self.config["boost_rated"]:
                score += (profile.avg_rating / 5.0) * 0.1
                
            # 5. Layer preference (prefer same layer blocks)
            if current_stack:
                current_layers = [
                    self.block_profiles[s].layer for s in current_stack 
                    if s in self.block_profiles
                ]
                if current_layers and profile.layer in current_layers:
                    score += 0.1
                    
            if score > 0:
                scores[block_id] = {
                    "score": min(score, 1.0),
                    "profile": profile,
                    "reasons": reasons[:3]  # Top 3 reasons
                }
                
        # Sort by score
        sorted_blocks = sorted(
            scores.items(), 
            key=lambda x: x[1]["score"], 
            reverse=True
        )
        
        recommendations = []
        for block_id, info in sorted_blocks[:self.config["max_suggestions"]]:
            recommendations.append({
                "block_id": block_id,
                "name": info["profile"].name,
                "description": info["profile"].description,
                "tags": info["profile"].tags,
                "score": round(info["score"], 2),
                "reasons": info["reasons"],
                "rating": info["profile"].avg_rating,
                "installs": info["profile"].install_count
            })
            
        return {
            "recommendations": recommendations,
            "based_on": {
                "current_stack": current_stack,
                "goal": goal
            },
            "model": self.config["recommendation_model"]
        }
        
    async def _find_alternatives(self, data: Dict) -> Dict:
        """Find alternative blocks for a given block"""
        block_id = data.get("block_id")
        
        if block_id not in self.block_profiles:
            return {"error": f"Block '{block_id}' not found"}
            
        target = self.block_profiles[block_id]
        
        alternatives = []
        for bid, profile in self.block_profiles.items():
            if bid == block_id:
                continue
                
            # Similar tags
            tag_overlap = len(set(target.tags) & set(profile.tags))
            if tag_overlap >= 2 or target.layer == profile.layer:
                similarity = tag_overlap / max(len(target.tags), len(profile.tags))
                alternatives.append({
                    "block_id": bid,
                    "name": profile.name,
                    "description": profile.description,
                    "similarity": round(similarity, 2),
                    "rating": profile.avg_rating,
                    "tags": profile.tags
                })
                
        alternatives.sort(key=lambda x: x["similarity"], reverse=True)
        
        return {
            "for_block": block_id,
            "alternatives": alternatives[:5],
            "count": len(alternatives)
        }
        
    async def _semantic_search(self, data: Dict) -> Dict:
        """Search blocks by semantic meaning"""
        query = data.get("query", "")
        filters = data.get("filters", {})
        
        if not query:
            return {"error": "Query required"}
            
        results = []
        query_lower = query.lower()
        query_terms = query_lower.split()
        
        for block_id, profile in self.block_profiles.items():
            # Apply filters
            if "layer" in filters and profile.layer != filters["layer"]:
                continue
            if "tag" in filters and filters["tag"] not in profile.tags:
                continue
                
            # Calculate match score
            score = 0.0
            
            # Name match
            if any(term in profile.name.lower() for term in query_terms):
                score += 0.4
                
            # Description match
            if any(term in profile.description.lower() for term in query_terms):
                score += 0.3
                
            # Tag match
            for tag in profile.tags:
                if any(term in tag for term in query_terms):
                    score += 0.2
                    
            # Use case match
            for use_case in profile.use_cases:
                if any(term in use_case.lower() for term in query_terms):
                    score += 0.1
                    
            if score > 0.1:
                results.append({
                    "block_id": block_id,
                    "name": profile.name,
                    "description": profile.description,
                    "tags": profile.tags,
                    "layer": profile.layer,
                    "score": round(score, 2),
                    "rating": profile.avg_rating
                })
                
        results.sort(key=lambda x: x["score"], reverse=True)
        
        return {
            "query": query,
            "results": results[:10],
            "count": len(results)
        }
        
    async def _trending_blocks(self, data: Dict) -> Dict:
        """Get trending/popular blocks"""
        category = data.get("category")  # Optional filter
        limit = data.get("limit", 10)
        
        blocks = []
        for block_id, profile in self.block_profiles.items():
            if category and category not in profile.tags:
                continue
                
            # Trending score combines popularity and rating
            trending_score = (
                profile.popularity_score * 0.6 +
                (profile.avg_rating / 5.0) * 0.4
            )
            
            blocks.append({
                "block_id": block_id,
                "name": profile.name,
                "description": profile.description,
                "trending_score": round(trending_score, 2),
                "installs": profile.install_count,
                "rating": profile.avg_rating,
                "tags": profile.tags
            })
            
        blocks.sort(key=lambda x: x["trending_score"], reverse=True)
        
        return {
            "trending": blocks[:limit],
            "category": category or "all"
        }
        
    async def _get_compatible(self, data: Dict) -> Dict:
        """Get blocks compatible with a given block"""
        block_id = data.get("block_id")
        
        if block_id not in self.block_profiles:
            return {"error": f"Block '{block_id}' not found"}
            
        profile = self.block_profiles[block_id]
        
        compatible = []
        for bid, p in self.block_profiles.items():
            if bid == block_id:
                continue
                
            # Check mutual complements
            if bid in profile.complements or block_id in p.complements:
                compatible.append({
                    "block_id": bid,
                    "name": p.name,
                    "compatibility": "high",
                    "reason": "Directly compatible"
                })
            # Check shared tags
            elif len(set(profile.tags) & set(p.tags)) >= 2:
                compatible.append({
                    "block_id": bid,
                    "name": p.name,
                    "compatibility": "medium",
                    "reason": "Similar functionality"
                })
                
        return {
            "block_id": block_id,
            "compatible_with": compatible,
            "count": len(compatible)
        }
        
    async def _index_block(self, data: Dict) -> Dict:
        """Index a new block for discovery"""
        profile_data = data.get("profile", {})
        
        profile = BlockProfile(
            block_id=profile_data.get("block_id"),
            name=profile_data.get("name"),
            description=profile_data.get("description", ""),
            tags=profile_data.get("tags", []),
            layer=profile_data.get("layer", 2),
            requires=profile_data.get("requires", []),
            complements=profile_data.get("complements", []),
            use_cases=profile_data.get("use_cases", []),
            popularity_score=profile_data.get("popularity_score", 0.5),
            avg_rating=profile_data.get("avg_rating", 4.0),
            install_count=profile_data.get("install_count", 0)
        )
        
        self.block_profiles[profile.block_id] = profile
        
        # TODO: Index in vector database for semantic search
        
        return {
            "indexed": True,
            "block_id": profile.block_id
        }
        
    async def _get_categories(self, data: Dict) -> Dict:
        """Get all block categories/tags"""
        tags = set()
        layers = set()
        
        for profile in self.block_profiles.values():
            tags.update(profile.tags)
            layers.add(profile.layer)
            
        return {
            "tags": sorted(list(tags)),
            "layers": sorted(list(layers)),
            "total_blocks": len(self.block_profiles)
        }
        
    async def _smart_stack_builder(self, data: Dict) -> Dict:
        """Build an optimal block stack for a use case"""
        use_case = data.get("use_case", "")
        budget = data.get("budget", "medium")  # small, medium, large
        
        if not use_case:
            return {"error": "Use case description required"}
            
        # Map use cases to recommended stacks
        stack_templates = {
            "saas": ["auth", "database", "billing", "team", "webhook", "analytics"],
            "ai_chatbot": ["auth", "chat", "memory", "vector", "webhook"],
            "document_processor": ["auth", "storage", "pdf", "ocr", "chat", "database"],
            "ecommerce": ["auth", "database", "billing", "storage", "webhook", "analytics"],
            "api_platform": ["auth", "rate_limit", "webhook", "monitoring", "database"],
            "data_pipeline": ["database", "queue", "workflow", "analytics", "storage"]
        }
        
        # Simple keyword matching for template selection
        use_case_lower = use_case.lower()
        selected_stack = []
        
        for template_name, blocks in stack_templates.items():
            if template_name.replace("_", " ") in use_case_lower:
                selected_stack = blocks
                break
                
        # If no template match, use discovery
        if not selected_stack:
            rec_result = await self._recommend_stack({
                "current_stack": [],
                "goal": use_case
            })
            selected_stack = [r["block_id"] for r in rec_result.get("recommendations", [])[:5]]
            
        # Filter by budget
        if budget == "small":
            selected_stack = selected_stack[:3]  # Minimal stack
        elif budget == "large":
            # Add monitoring and failover
            if "monitoring" not in selected_stack:
                selected_stack.append("monitoring")
            if "failover" not in selected_stack:
                selected_stack.append("failover")
                
        # Build detailed stack info
        stack_details = []
        for block_id in selected_stack:
            if block_id in self.block_profiles:
                p = self.block_profiles[block_id]
                stack_details.append({
                    "block_id": block_id,
                    "name": p.name,
                    "layer": p.layer,
                    "purpose": p.use_cases[0] if p.use_cases else "Core functionality"
                })
                
        return {
            "use_case": use_case,
            "budget": budget,
            "recommended_stack": stack_details,
            "estimated_complexity": self._estimate_complexity(selected_stack),
            "template_matched": bool(any(t in use_case_lower for t in stack_templates))
        }
        
    def _estimate_complexity(self, stack: List[str]) -> str:
        """Estimate stack complexity"""
        layers = []
        for block_id in stack:
            if block_id in self.block_profiles:
                layers.append(self.block_profiles[block_id].layer)
                
        if not layers:
            return "unknown"
            
        max_layer = max(layers)
        if max_layer <= 1:
            return "simple"
        elif max_layer <= 3:
            return "moderate"
        else:
            return "complex"
            
    def health(self) -> Dict:
        h = super().health()
        h["indexed_blocks"] = len(self.block_profiles)
        h["usage_patterns_loaded"] = len(self.usage_patterns)
        h["model"] = self.config["recommendation_model"]
        return h
