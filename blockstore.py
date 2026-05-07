#!/usr/bin/env python3
"""
Block Store CLI - Publish and manage blocks in the marketplace

Usage:
    python blockstore.py publish --name my_block --code block.py --price 500
    python blockstore.py discover --query "pdf"
    python blockstore.py stats
"""

import asyncio
import argparse
import json
from pathlib import Path

# Add to path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from app.containers.store import StoreContainer


async def main():
    parser = argparse.ArgumentParser(description="Cerebrum Block Store")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Publish command
    pub = subparsers.add_parser("publish", help="Publish a block")
    pub.add_argument("--name", required=True, help="Block name")
    pub.add_argument("--code", required=True, help="Path to block code file")
    pub.add_argument("--description", default="", help="Block description")
    pub.add_argument("--price", type=int, default=0, help="Price in cents (0=free)")
    pub.add_argument("--creator", default="anonymous", help="Creator ID")
    pub.add_argument("--tags", nargs="+", default=[], help="Tags")
    
    # Discover command
    disc = subparsers.add_parser("discover", help="Discover blocks")
    disc.add_argument("--query", default="", help="Search query")
    disc.add_argument("--tags", nargs="+", help="Filter by tags")
    disc.add_argument("--limit", type=int, default=10, help="Max results")
    
    # Review command
    rev = subparsers.add_parser("review", help="Review a block")
    rev.add_argument("--block", required=True, help="Block ID")
    rev.add_argument("--rating", type=int, required=True, help="Rating 1-5")
    rev.add_argument("--comment", default="", help="Review comment")
    rev.add_argument("--user", default="anonymous", help="User ID")
    
    # Stats command
    subparsers.add_parser("stats", help="Platform stats")
    
    # Creator dashboard
    dash = subparsers.add_parser("dashboard", help="Creator dashboard")
    dash.add_argument("--creator", required=True, help="Creator ID")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Initialize store
    store = StoreContainer()
    
    if args.command == "publish":
        # Read code file
        code_path = Path(args.code)
        if not code_path.exists():
            print(f"❌ Code file not found: {args.code}")
            return
        
        code = code_path.read_text()
        
        result = await store.process(None, {
            "action": "publish",
            "block_data": {
                "name": args.name,
                "code": code,
                "description": args.description,
                "price_cents": args.price,
                "tags": args.tags
            },
            "creator_id": args.creator
        })
        
        if result.get("published"):
            print(f"✅ Block published: {result['block_id']}")
            print(f"   Validation score: {result.get('validation_score', 0)}")
            print(f"   Creator cut: {result.get('estimated_creators_cut', '80%')}")
        else:
            print(f"❌ Failed: {result.get('error')}")
    
    elif args.command == "discover":
        result = await store.process(None, {
            "action": "discover",
            "query": args.query,
            "tags": args.tags or [],
            "limit": args.limit
        })
        
        blocks = result.get("blocks", [])
        print(f"\n📦 Found {len(blocks)} blocks:\n")
        
        for b in blocks:
            price = "Free" if b["price_cents"] == 0 else f"${b['price_cents']/100:.2f}"
            rating = f"⭐ {b['rating_avg']}" if b['rating_count'] > 0 else "No ratings"
            print(f"  {b['id']}")
            print(f"     {b['description'][:60]}...")
            print(f"     Price: {price} | {rating} | {b['downloads']} downloads")
            print()
    
    elif args.command == "review":
        result = await store.process(None, {
            "action": "review",
            "block_id": args.block,
            "rating": args.rating,
            "comment": args.comment,
            "user_id": args.user
        })
        
        if result.get("reviewed"):
            print(f"✅ Review submitted")
            print(f"   New rating: {result['new_rating']}/5")
            print(f"   Total reviews: {result['total_reviews']}")
        else:
            print(f"❌ Failed: {result.get('error')}")
    
    elif args.command == "stats":
        result = await store.process(None, {"action": "platform_stats"})
        
        print("\n📊 Platform Stats:\n")
        print(f"   Total blocks: {result['total_blocks']}")
        print(f"   Total creators: {result['total_creators']}")
        print(f"   Total purchases: {result['total_purchases']}")
        print(f"   Total sales: {result['total_sales']}")
        print(f"   Platform revenue: {result['platform_revenue']}")
        print(f"   Platform fee: {result['platform_fee_percent']}%")
        
        print("\n   Top Blocks:")
        for b in result.get("top_blocks", [])[:5]:
            print(f"      {b['id']}: {b['downloads']} downloads")
    
    elif args.command == "dashboard":
        result = await store.process(None, {
            "action": "creator_dashboard",
            "creator_id": args.creator
        })
        
        if "error" in result:
            print(f"❌ {result['error']}")
            return
        
        print(f"\n👤 Creator Dashboard: {result['creator_id']}\n")
        print(f"   Total earnings: {result['total_earnings']}")
        print(f"   Blocks published: {result['blocks_published']}")
        print(f"   Total downloads: {result['total_downloads']}")
        print(f"   Average rating: {result['avg_rating']}/5")
        
        if result.get("blocks"):
            print("\n   Your Blocks:")
            for b in result["blocks"]:
                print(f"      {b['id']}: ${b['price_cents']/100:.2f}, {b['downloads']} downloads")


if __name__ == "__main__":
    asyncio.run(main())
