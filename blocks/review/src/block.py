"""Review Block - Ratings and reviews for Block Store

Community-driven quality assurance with moderation,
verified purchases, and aggregate ratings.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
from datetime import datetime
import hashlib
import re


class ReviewBlock(LegoBlock):
    """
    Ratings, reviews, and quality assurance for Block Store.
    Community-driven block vetting.
    """
    name = "review"
    version = "1.0.0"
    requires = ["database", "auth", "team"]
    layer = 4
    tags = ["store", "community", "quality", "ratings"]
    
    default_config = {
        "min_reviews_for_public": 3,
        "trusted_reviewer_threshold": 10,
        "max_review_length": 5000,
        "require_verified": True,
        "auto_moderate": True,
        "flag_threshold": 3  # Flags before manual review
    }
    
    # Simple spam indicators (in production, use ML)
    SPAM_PATTERNS = [
        r"\b(buy now|click here|visit my|check out my)\b",
        r"\b(viagra|cialis|casino|lottery)\b",
        r"(.)\1{10,}",  # Repeated characters
        r"https?://\S{100,}",  # Very long URLs
    ]
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.reviews: Dict[str, List[Dict]] = {}  # block_id -> reviews
        self.ratings: Dict[str, Dict] = {}  # block_id -> aggregate stats
        self.user_reviews: Dict[str, List[str]] = {}  # user_id -> review_ids
        self.flags: Dict[str, List[Dict]] = {}  # review_id -> flags
        self.verified_purchases: set = set()  # (user_id, block_id) pairs
        
    async def initialize(self) -> bool:
        """Initialize review system"""
        print("⭐ Review Block initializing...")
        print(f"   Min reviews for public: {self.config['min_reviews_for_public']}")
        print(f"   Auto-moderate: {self.config['auto_moderate']}")
        
        # TODO: Create database tables
        # - reviews: id, block_id, user_id, rating, text, created_at, status
        # - ratings: block_id, avg_rating, total_reviews, distribution
        # - verified_purchases: user_id, block_id, verified_at
        # - review_flags: review_id, flagged_by, reason, created_at
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute review actions"""
        action = input_data.get("action")
        
        actions = {
            "submit_review": self._submit_review,
            "get_rating": self._get_aggregate_rating,
            "get_reviews": self._get_reviews,
            "flag_review": self._flag_review,
            "verify_purchase": self._verify_purchase,
            "moderate": self._moderate_review,
            "update_review": self._update_review,
            "delete_review": self._delete_review,
            "get_user_reviews": self._get_user_reviews,
            "get_pending_moderation": self._get_pending_moderation
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _submit_review(self, data: Dict) -> Dict:
        """Submit a new review"""
        block_id = data.get("block_id")
        user_id = data.get("user_id")
        rating = data.get("rating")  # 1-5
        review_text = data.get("review_text", "")
        
        # Validation
        if not block_id or not user_id or rating is None:
            return {"error": "block_id, user_id, and rating required"}
            
        if not (1 <= rating <= 5):
            return {"error": "Rating must be 1-5"}
            
        if len(review_text) > self.config["max_review_length"]:
            return {"error": f"Review too long (max {self.config['max_review_length']} chars)"}
            
        # Check for existing review
        existing = self._get_user_review_for_block(user_id, block_id)
        if existing:
            return {"error": "Already reviewed this block. Use update_review."}
            
        # Check verified purchase
        verified = (user_id, block_id) in self.verified_purchases
        if self.config["require_verified"] and not verified:
            return {"error": "Verified purchase required to review"}
            
        # Auto-moderation
        moderation_status = "approved"
        moderation_flags = []
        
        if self.config["auto_moderate"]:
            is_spam, spam_reasons = self._check_spam(review_text)
            if is_spam:
                moderation_status = "flagged"
                moderation_flags = spam_reasons
                
        # Create review
        review_id = f"rev_{hashlib.sha256(f'{user_id}:{block_id}:{datetime.utcnow()}'.encode()).hexdigest()[:16]}"
        
        review = {
            "id": review_id,
            "block_id": block_id,
            "user_id": user_id,
            "rating": rating,
            "review_text": review_text,
            "verified_purchase": verified,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": None,
            "status": moderation_status,
            "flags": moderation_flags,
            "helpful_count": 0,
            "not_helpful_count": 0
        }
        
        # Store
        if block_id not in self.reviews:
            self.reviews[block_id] = []
        self.reviews[block_id].append(review)
        
        if user_id not in self.user_reviews:
            self.user_reviews[user_id] = []
        self.user_reviews[user_id].append(review_id)
        
        # Update aggregate rating
        await self._update_aggregate_rating(block_id)
        
        print(f"   ✓ Review submitted: {review_id[:8]}... (rating: {rating})")
        
        return {
            "submitted": True,
            "review_id": review_id,
            "pending_moderation": moderation_status == "flagged",
            "verified": verified
        }
        
    async def _get_aggregate_rating(self, data: Dict) -> Dict:
        """Get aggregate rating for a block"""
        block_id = data.get("block_id")
        
        if block_id not in self.ratings:
            # Calculate on the fly
            await self._update_aggregate_rating(block_id)
            
        rating = self.ratings.get(block_id, {
            "average": 0.0,
            "total": 0,
            "distribution": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        })
        
        # Only show if enough reviews
        show_rating = rating.get("total", 0) >= self.config["min_reviews_for_public"]
        
        return {
            "block_id": block_id,
            "average_rating": round(rating.get("average", 0), 1) if show_rating else None,
            "total_reviews": rating.get("total", 0),
            "distribution": rating.get("distribution", {}) if show_rating else None,
            "show_rating": show_rating,
            "verified_reviews": rating.get("verified_count", 0)
        }
        
    async def _get_reviews(self, data: Dict) -> Dict:
        """Get reviews for a block"""
        block_id = data.get("block_id")
        filter_verified = data.get("verified_only", False)
        sort_by = data.get("sort", "newest")  # newest, highest, lowest, helpful
        limit = data.get("limit", 10)
        offset = data.get("offset", 0)
        
        reviews = self.reviews.get(block_id, [])
        
        # Filter
        if filter_verified:
            reviews = [r for r in reviews if r["verified_purchase"]]
            
        # Only show approved
        reviews = [r for r in reviews if r["status"] == "approved"]
        
        # Sort
        if sort_by == "newest":
            reviews.sort(key=lambda x: x["created_at"], reverse=True)
        elif sort_by == "highest":
            reviews.sort(key=lambda x: x["rating"], reverse=True)
        elif sort_by == "lowest":
            reviews.sort(key=lambda x: x["rating"])
        elif sort_by == "helpful":
            reviews.sort(
                key=lambda x: x["helpful_count"] - x["not_helpful_count"], 
                reverse=True
            )
            
        paginated = reviews[offset:offset + limit]
        
        # Sanitize user IDs (show partial only)
        for r in paginated:
            r["user_display"] = self._anonymize_user(r["user_id"])
            
        return {
            "block_id": block_id,
            "reviews": paginated,
            "total": len(reviews),
            "limit": limit,
            "offset": offset
        }
        
    async def _flag_review(self, data: Dict) -> Dict:
        """Flag a review for moderation"""
        review_id = data.get("review_id")
        user_id = data.get("user_id")
        reason = data.get("reason", "inappropriate")
        
        # Find review
        review = self._find_review_by_id(review_id)
        if not review:
            return {"error": "Review not found"}
            
        # Can't flag own review
        if review["user_id"] == user_id:
            return {"error": "Cannot flag your own review"}
            
        # Check if already flagged by this user
        existing_flags = self.flags.get(review_id, [])
        if any(f["flagged_by"] == user_id for f in existing_flags):
            return {"error": "Already flagged this review"}
            
        # Add flag
        flag = {
            "review_id": review_id,
            "flagged_by": user_id,
            "reason": reason,
            "created_at": datetime.utcnow().isoformat()
        }
        
        if review_id not in self.flags:
            self.flags[review_id] = []
        self.flags[review_id].append(flag)
        
        # Auto-hide if threshold reached
        if len(self.flags[review_id]) >= self.config["flag_threshold"]:
            review["status"] = "under_review"
            print(f"   ⚠️ Review {review_id[:8]}... auto-flagged for moderation")
            
        return {
            "flagged": True,
            "review_id": review_id,
            "total_flags": len(self.flags[review_id])
        }
        
    async def _verify_purchase(self, data: Dict) -> Dict:
        """Mark a user-block pair as verified purchase"""
        user_id = data.get("user_id")
        block_id = data.get("block_id")
        
        # TODO: Verify actual purchase/usage in billing system
        
        key = (user_id, block_id)
        self.verified_purchases.add(key)
        
        return {
            "verified": True,
            "user_id": user_id,
            "block_id": block_id
        }
        
    async def _moderate_review(self, data: Dict) -> Dict:
        """Admin action: moderate a review"""
        review_id = data.get("review_id")
        admin_id = data.get("admin_id")
        action = data.get("moderation_action")  # approve, reject, hide
        reason = data.get("reason", "")
        
        review = self._find_review_by_id(review_id)
        if not review:
            return {"error": "Review not found"}
            
        if action == "approve":
            review["status"] = "approved"
            review["moderation_note"] = reason
        elif action == "reject":
            review["status"] = "rejected"
            review["moderation_note"] = reason
        elif action == "hide":
            review["status"] = "hidden"
            review["moderation_note"] = reason
        else:
            return {"error": f"Unknown moderation action: {action}"}
            
        review["moderated_at"] = datetime.utcnow().isoformat()
        review["moderated_by"] = admin_id
        
        # Update aggregate if changed
        await self._update_aggregate_rating(review["block_id"])
        
        return {
            "moderated": True,
            "review_id": review_id,
            "new_status": review["status"]
        }
        
    async def _update_review(self, data: Dict) -> Dict:
        """Update an existing review"""
        review_id = data.get("review_id")
        user_id = data.get("user_id")
        rating = data.get("rating")
        review_text = data.get("review_text")
        
        review = self._find_review_by_id(review_id)
        if not review:
            return {"error": "Review not found"}
            
        if review["user_id"] != user_id:
            return {"error": "Can only update your own reviews"}
            
        # Update fields
        if rating is not None:
            review["rating"] = rating
        if review_text is not None:
            review["review_text"] = review_text
            
        review["updated_at"] = datetime.utcnow().isoformat()
        
        # Re-moderate if text changed
        if review_text and self.config["auto_moderate"]:
            is_spam, spam_reasons = self._check_spam(review_text)
            if is_spam:
                review["status"] = "flagged"
                review["flags"] = spam_reasons
                
        # Update aggregate
        await self._update_aggregate_rating(review["block_id"])
        
        return {
            "updated": True,
            "review_id": review_id
        }
        
    async def _delete_review(self, data: Dict) -> Dict:
        """Delete a review"""
        review_id = data.get("review_id")
        user_id = data.get("user_id")
        is_admin = data.get("is_admin", False)
        
        review = self._find_review_by_id(review_id)
        if not review:
            return {"error": "Review not found"}
            
        if review["user_id"] != user_id and not is_admin:
            return {"error": "Permission denied"}
            
        # Remove from block reviews
        block_id = review["block_id"]
        self.reviews[block_id] = [
            r for r in self.reviews.get(block_id, []) 
            if r["id"] != review_id
        ]
        
        # Remove from user reviews
        self.user_reviews[user_id] = [
            rid for rid in self.user_reviews.get(user_id, [])
            if rid != review_id
        ]
        
        # Update aggregate
        await self._update_aggregate_rating(block_id)
        
        return {
            "deleted": True,
            "review_id": review_id
        }
        
    async def _get_user_reviews(self, data: Dict) -> Dict:
        """Get all reviews by a user"""
        user_id = data.get("user_id")
        
        review_ids = self.user_reviews.get(user_id, [])
        reviews = []
        
        for rid in review_ids:
            review = self._find_review_by_id(rid)
            if review:
                reviews.append(review)
                
        return {
            "user_id": user_id,
            "reviews": reviews,
            "count": len(reviews),
            "trusted_status": len(reviews) >= self.config["trusted_reviewer_threshold"]
        }
        
    async def _get_pending_moderation(self, data: Dict) -> Dict:
        """Get reviews pending moderation (admin)"""
        pending = []
        
        for block_reviews in self.reviews.values():
            for review in block_reviews:
                if review["status"] in ["flagged", "under_review"]:
                    pending.append(review)
                    
        pending.sort(key=lambda x: x["created_at"])
        
        return {
            "pending": pending,
            "count": len(pending)
        }
        
    # Helper methods
    def _check_spam(self, text: str) -> tuple:
        """Check if review contains spam"""
        if not text:
            return False, []
            
        text_lower = text.lower()
        flags = []
        
        for pattern in self.SPAM_PATTERNS:
            if re.search(pattern, text_lower):
                flags.append(f"Matched pattern: {pattern[:30]}...")
                
        # Check for excessive caps
        caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
        if caps_ratio > 0.7 and len(text) > 20:
            flags.append("Excessive capitalization")
            
        # Check for repeated words
        words = text_lower.split()
        if len(words) > 10:
            word_counts = {}
            for word in words:
                word_counts[word] = word_counts.get(word, 0) + 1
            if any(count > len(words) * 0.3 for count in word_counts.values()):
                flags.append("Repeated words/spam pattern")
                
        return len(flags) > 0, flags
        
    def _find_review_by_id(self, review_id: str) -> Optional[Dict]:
        """Find review by ID across all blocks"""
        for block_reviews in self.reviews.values():
            for review in block_reviews:
                if review["id"] == review_id:
                    return review
        return None
        
    def _get_user_review_for_block(self, user_id: str, block_id: str) -> Optional[Dict]:
        """Check if user has reviewed block"""
        for review in self.reviews.get(block_id, []):
            if review["user_id"] == user_id:
                return review
        return None
        
    def _anonymize_user(self, user_id: str) -> str:
        """Create anonymous display name"""
        # Hash and take first 8 chars
        hashed = hashlib.sha256(user_id.encode()).hexdigest()[:8]
        return f"user_{hashed}"
        
    async def _update_aggregate_rating(self, block_id: str):
        """Update aggregate rating for a block"""
        reviews = self.reviews.get(block_id, [])
        approved = [r for r in reviews if r["status"] == "approved"]
        
        if not approved:
            self.ratings[block_id] = {
                "average": 0.0,
                "total": 0,
                "distribution": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
                "verified_count": 0
            }
            return
            
        ratings = [r["rating"] for r in approved]
        distribution = {i: ratings.count(i) for i in range(1, 6)}
        verified_count = sum(1 for r in approved if r["verified_purchase"])
        
        self.ratings[block_id] = {
            "average": sum(ratings) / len(ratings),
            "total": len(approved),
            "distribution": distribution,
            "verified_count": verified_count
        }
        
    def health(self) -> Dict:
        h = super().health()
        h["total_reviews"] = sum(len(r) for r in self.reviews.values())
        h["blocks_reviewed"] = len(self.reviews)
        h["verified_purchases"] = len(self.verified_purchases)
        h["pending_moderation"] = sum(
            1 for block_reviews in self.reviews.values()
            for r in block_reviews if r["status"] in ["flagged", "under_review"]
        )
        return h
