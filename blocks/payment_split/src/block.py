"""Payment Split Block - Revenue sharing for Block Store

Handles payouts to block creators, platform fees, referrals,
and financial reporting for marketplace transactions.
"""

from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from enum import Enum
import hashlib


class PayoutStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    HELD = "held"  # Disputed/on hold


class PaymentSplitBlock(LegoBlock):
    """
    Revenue sharing for Block Store.
    Handles payouts to block creators, platform fees, referrals.
    """
    name = "payment_split"
    version = "1.0.0"
    requires = ["billing", "team", "database"]
    layer = 5
    tags = ["store", "payments", "marketplace", "revenue"]
    
    default_config = {
        "platform_fee_percent": 20,  # Platform takes 20%
        "referral_bonus_percent": 5,
        "payout_schedule": "monthly",  # daily, weekly, monthly
        "minimum_payout_cents": 5000,  # $50 minimum
        "hold_period_days": 14,  # Hold earnings for refunds
        "stripe_connect_enabled": True
    }
    
    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        self.creators: Dict[str, Dict] = {}  # creator_id -> creator info
        self.transactions: List[Dict] = []  # All marketplace transactions
        self.payouts: Dict[str, List[Dict]] = {}  # creator_id -> payouts
        self.balances: Dict[str, int] = {}  # creator_id -> balance in cents
        self.earnings: Dict[str, Dict] = {}  # creator_id -> earnings by period
        
    async def initialize(self) -> bool:
        """Initialize payment split system"""
        print("💰 Payment Split Block initializing...")
        print(f"   Platform fee: {self.config['platform_fee_percent']}%")
        print(f"   Payout schedule: {self.config['payout_schedule']}")
        print(f"   Minimum payout: ${self.config['minimum_payout_cents'] / 100:.2f}")
        
        # TODO: Connect to Stripe Connect
        # TODO: Setup payout schedules (cron jobs)
        # TODO: Load creator balances from database
        
        self.initialized = True
        return True
        
    async def execute(self, input_data: Dict) -> Dict:
        """Execute payment split actions"""
        action = input_data.get("action")
        
        actions = {
            "calculate_split": self._calculate_split,
            "process_sale": self._process_sale,
            "process_payout": self._process_payout,
            "register_creator": self._register_creator,
            "update_creator": self._update_creator,
            "revenue_report": self._revenue_report,
            "creator_dashboard": self._creator_dashboard,
            "transfer_ownership": self._transfer_ownership,
            "get_transaction": self._get_transaction,
            "list_transactions": self._list_transactions,
            "hold_funds": self._hold_funds,
            "release_hold": self._release_hold
        }
        
        if action in actions:
            return await actions[action](input_data)
            
        return {"error": f"Unknown action: {action}", "available": list(actions.keys())}
        
    async def _calculate_split(self, data: Dict) -> Dict:
        """Calculate revenue split for a transaction"""
        amount_cents = data.get("amount_cents")  # $100 = 10000
        block_creator_id = data.get("creator_id")
        referrer_id = data.get("referrer_id")
        block_id = data.get("block_id")
        
        if not amount_cents or not block_creator_id:
            return {"error": "amount_cents and creator_id required"}
            
        # Calculate splits
        platform_fee = int(amount_cents * self.config["platform_fee_percent"] / 100)
        creator_amount = amount_cents - platform_fee
        referral_amount = 0
        
        # Apply referral bonus
        if referrer_id and referrer_id != block_creator_id:
            referral_amount = int(amount_cents * self.config["referral_bonus_percent"] / 100)
            creator_amount -= referral_amount
            
        return {
            "gross_amount_cents": amount_cents,
            "gross_amount": f"${amount_cents / 100:.2f}",
            "platform_fee_cents": platform_fee,
            "platform_fee": f"${platform_fee / 100:.2f}",
            "platform_fee_percent": self.config["platform_fee_percent"],
            "creator_earns_cents": creator_amount,
            "creator_earns": f"${creator_amount / 100:.2f}",
            "referral_bonus_cents": referral_amount,
            "referral_bonus": f"${referral_amount / 100:.2f}",
            "referrer_id": referrer_id if referral_amount > 0 else None
        }
        
    async def _process_sale(self, data: Dict) -> Dict:
        """Process a marketplace sale and distribute earnings"""
        amount_cents = data.get("amount_cents")
        block_id = data.get("block_id")
        creator_id = data.get("creator_id")
        buyer_id = data.get("buyer_id")
        referrer_id = data.get("referrer_id")
        
        if not all([amount_cents, block_id, creator_id]):
            return {"error": "amount_cents, block_id, creator_id required"}
            
        # Calculate split
        split = await self._calculate_split({
            "amount_cents": amount_cents,
            "creator_id": creator_id,
            "referrer_id": referrer_id
        })
        
        # Create transaction record
        transaction_id = f"txn_{hashlib.sha256(f'{block_id}:{buyer_id}:{datetime.utcnow()}'.encode()).hexdigest()[:16]}"
        
        transaction = {
            "id": transaction_id,
            "type": "sale",
            "block_id": block_id,
            "buyer_id": buyer_id,
            "creator_id": creator_id,
            "referrer_id": referrer_id,
            "gross_amount_cents": amount_cents,
            "platform_fee_cents": split["platform_fee_cents"],
            "creator_earnings_cents": split["creator_earns_cents"],
            "referral_amount_cents": split["referral_bonus_cents"],
            "created_at": datetime.utcnow().isoformat(),
            "status": "completed",
            "hold_until": (datetime.utcnow() + timedelta(
                days=self.config["hold_period_days"]
            )).isoformat(),
            "released": False
        }
        
        self.transactions.append(transaction)
        
        # Update pending balance (held until hold period)
        if creator_id not in self.balances:
            self.balances[creator_id] = 0
            
        # Track earnings by period
        period_key = datetime.utcnow().strftime("%Y-%m")
        if creator_id not in self.earnings:
            self.earnings[creator_id] = {}
        if period_key not in self.earnings[creator_id]:
            self.earnings[creator_id][period_key] = {
                "sales": 0,
                "earnings_cents": 0,
                "transactions": []
            }
            
        self.earnings[creator_id][period_key]["sales"] += 1
        self.earnings[creator_id][period_key]["earnings_cents"] += split["creator_earns_cents"]
        self.earnings[creator_id][period_key]["transactions"].append(transaction_id)
        
        # Add to referrer if applicable
        if referrer_id and split["referral_bonus_cents"] > 0:
            if referrer_id not in self.balances:
                self.balances[referrer_id] = 0
            if referrer_id not in self.earnings:
                self.earnings[referrer_id] = {}
            if period_key not in self.earnings[referrer_id]:
                self.earnings[referrer_id][period_key] = {
                    "referrals": 0,
                    "referral_earnings_cents": 0
                }
            self.earnings[referrer_id][period_key]["referrals"] += 1
            self.earnings[referrer_id][period_key]["referral_earnings_cents"] += split["referral_bonus_cents"]
        
        print(f"   ✓ Sale processed: ${amount_cents/100:.2f} - Creator earns ${split['creator_earns']/100:.2f}")
        
        return {
            "transaction_id": transaction_id,
            "processed": True,
            "split": split,
            "hold_until": transaction["hold_until"]
        }
        
    async def _process_payout(self, data: Dict) -> Dict:
        """Process a payout to a creator"""
        creator_id = data.get("creator_id")
        force_amount = data.get("amount_cents")  # Optional: force specific amount
        
        if creator_id not in self.creators:
            return {"error": "Creator not registered"}
            
        creator = self.creators[creator_id]
        
        # Check Stripe Connect account
        if not creator.get("stripe_account_id"):
            return {"error": "Creator has no connected payout account"}
            
        # Calculate payout amount
        available_balance = self.balances.get(creator_id, 0)
        
        # Release held funds that are past hold period
        released = await self._release_available_funds(creator_id)
        
        payout_amount = force_amount or available_balance
        
        if payout_amount < self.config["minimum_payout_cents"]:
            return {
                "error": f"Minimum payout is ${self.config['minimum_payout_cents'] / 100:.2f}",
                "available_balance": available_balance,
                "released_from_hold": released
            }
            
        if payout_amount > available_balance:
            return {
                "error": "Insufficient balance",
                "requested": payout_amount,
                "available": available_balance
            }
            
        # Create payout record
        payout_id = f"payout_{hashlib.sha256(f'{creator_id}:{datetime.utcnow()}'.encode()).hexdigest()[:12]}"
        
        payout = {
            "id": payout_id,
            "creator_id": creator_id,
            "amount_cents": payout_amount,
            "amount": f"${payout_amount / 100:.2f}",
            "status": PayoutStatus.PROCESSING.value,
            "method": "stripe_connect",
            "destination": creator["stripe_account_id"],
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": None,
            "transaction_ids": []  # Which earnings this covers
        }
        
        if creator_id not in self.payouts:
            self.payouts[creator_id] = []
        self.payouts[creator_id].append(payout)
        
        # Deduct from balance
        self.balances[creator_id] -= payout_amount
        
        # TODO: Actually process via Stripe Connect
        # stripe.payouts.create(...)
        
        payout["status"] = PayoutStatus.COMPLETED.value
        payout["completed_at"] = datetime.utcnow().isoformat()
        
        print(f"   ✓ Payout processed: ${payout_amount/100:.2f} to {creator_id[:8]}...")
        
        return {
            "payout_id": payout_id,
            "amount": payout["amount"],
            "status": payout["status"],
            "remaining_balance": self.balances.get(creator_id, 0)
        }
        
    async def _register_creator(self, data: Dict) -> Dict:
        """Register a new block creator"""
        user_id = data.get("user_id")
        email = data.get("email")
        stripe_account_id = data.get("stripe_account_id")
        tax_info = data.get("tax_info", {})
        
        if not user_id or not email:
            return {"error": "user_id and email required"}
            
        creator = {
            "id": user_id,
            "email": email,
            "stripe_account_id": stripe_account_id,
            "tax_info": tax_info,
            "registered_at": datetime.utcnow().isoformat(),
            "status": "active",
            "total_earnings_cents": 0,
            "total_payouts_cents": 0,
            "blocks_published": [],
            "payout_method": "stripe_connect" if stripe_account_id else "pending"
        }
        
        self.creators[user_id] = creator
        self.balances[user_id] = 0
        
        print(f"   ✓ Creator registered: {email}")
        
        return {
            "registered": True,
            "creator_id": user_id,
            "status": creator["status"]
        }
        
    async def _update_creator(self, data: Dict) -> Dict:
        """Update creator information"""
        creator_id = data.get("creator_id")
        
        if creator_id not in self.creators:
            return {"error": "Creator not found"}
            
        # Update allowed fields
        allowed_fields = ["email", "stripe_account_id", "tax_info", "payout_method"]
        for field in allowed_fields:
            if field in data:
                self.creators[creator_id][field] = data[field]
                
        return {
            "updated": True,
            "creator_id": creator_id
        }
        
    async def _revenue_report(self, data: Dict) -> Dict:
        """Generate revenue report for a creator"""
        creator_id = data.get("creator_id")
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        
        if creator_id not in self.creators:
            return {"error": "Creator not found"}
            
        # Get transactions in date range
        creator_transactions = [
            t for t in self.transactions
            if t["creator_id"] == creator_id
            and (not start_date or t["created_at"] >= start_date)
            and (not end_date or t["created_at"] <= end_date)
        ]
        
        # Calculate totals
        total_sales = len(creator_transactions)
        total_gross = sum(t["gross_amount_cents"] for t in creator_transactions)
        total_earnings = sum(t["creator_earnings_cents"] for t in creator_transactions)
        
        # Get referral earnings
        referral_transactions = [
            t for t in self.transactions
            if t.get("referrer_id") == creator_id
            and (not start_date or t["created_at"] >= start_date)
            and (not end_date or t["created_at"] <= end_date)
        ]
        total_referral_earnings = sum(t.get("referral_amount_cents", 0) for t in referral_transactions)
        
        # Get payouts
        creator_payouts = self.payouts.get(creator_id, [])
        period_payouts = [
            p for p in creator_payouts
            if (not start_date or p["created_at"] >= start_date)
            and (not end_date or p["created_at"] <= end_date)
        ]
        total_payouts = sum(p["amount_cents"] for p in period_payouts)
        
        return {
            "creator_id": creator_id,
            "period": {
                "start": start_date,
                "end": end_date
            },
            "sales": {
                "count": total_sales,
                "gross_revenue": f"${total_gross / 100:.2f}",
                "your_earnings": f"${total_earnings / 100:.2f}"
            },
            "referrals": {
                "count": len(referral_transactions),
                "earnings": f"${total_referral_earnings / 100:.2f}"
            },
            "payouts": {
                "count": len(period_payouts),
                "total": f"${total_payouts / 100:.2f}"
            },
            "balance": {
                "available": f"${self.balances.get(creator_id, 0) / 100:.2f}",
                "held": f"${await self._calculate_held_balance(creator_id) / 100:.2f}"
            }
        }
        
    async def _creator_dashboard(self, data: Dict) -> Dict:
        """Get creator dashboard data"""
        creator_id = data.get("creator_id")
        
        if creator_id not in self.creators:
            return {"error": "Creator not found"}
            
        creator = self.creators[creator_id]
        
        # Lifetime stats
        all_transactions = [t for t in self.transactions if t["creator_id"] == creator_id]
        lifetime_earnings = sum(t["creator_earnings_cents"] for t in all_transactions)
        
        # Monthly breakdown
        current_month = datetime.utcnow().strftime("%Y-%m")
        month_stats = self.earnings.get(creator_id, {}).get(current_month, {
            "sales": 0,
            "earnings_cents": 0
        })
        
        # Recent transactions
        recent = sorted(
            all_transactions,
            key=lambda x: x["created_at"],
            reverse=True
        )[:5]
        
        return {
            "creator": {
                "id": creator_id,
                "status": creator["status"],
                "payout_method": creator["payout_method"]
            },
            "lifetime": {
                "total_sales": len(all_transactions),
                "total_earnings": f"${lifetime_earnings / 100:.2f}",
                "blocks_published": len(creator.get("blocks_published", []))
            },
            "this_month": {
                "sales": month_stats.get("sales", 0),
                "earnings": f"${month_stats.get('earnings_cents', 0) / 100:.2f}"
            },
            "balance": {
                "available_cents": self.balances.get(creator_id, 0),
                "available": f"${self.balances.get(creator_id, 0) / 100:.2f}",
                "next_payout_eligible": self.balances.get(creator_id, 0) >= self.config["minimum_payout_cents"]
            },
            "recent_transactions": recent
        }
        
    async def _transfer_ownership(self, data: Dict) -> Dict:
        """Transfer block ownership to another creator"""
        block_id = data.get("block_id")
        from_creator = data.get("from_creator_id")
        to_creator = data.get("to_creator_id")
        
        if to_creator not in self.creators:
            return {"error": "New owner must be a registered creator"}
            
        # TODO: Update block ownership in database
        # TODO: Transfer pending earnings or wait for hold period?
        
        return {
            "transferred": True,
            "block_id": block_id,
            "from": from_creator,
            "to": to_creator,
            "note": "Future earnings will go to new owner"
        }
        
    async def _get_transaction(self, data: Dict) -> Dict:
        """Get transaction details"""
        transaction_id = data.get("transaction_id")
        
        for t in self.transactions:
            if t["id"] == transaction_id:
                return {"transaction": t}
                
        return {"error": "Transaction not found"}
        
    async def _list_transactions(self, data: Dict) -> Dict:
        """List transactions for a creator"""
        creator_id = data.get("creator_id")
        block_id = data.get("block_id")
        limit = data.get("limit", 20)
        
        transactions = self.transactions
        
        if creator_id:
            transactions = [t for t in transactions if t["creator_id"] == creator_id]
        if block_id:
            transactions = [t for t in transactions if t["block_id"] == block_id]
            
        transactions = sorted(transactions, key=lambda x: x["created_at"], reverse=True)
        
        return {
            "transactions": transactions[:limit],
            "total": len(transactions)
        }
        
    async def _hold_funds(self, data: Dict) -> Dict:
        """Place funds on hold (dispute/refund)"""
        transaction_id = data.get("transaction_id")
        reason = data.get("reason", "dispute")
        
        for t in self.transactions:
            if t["id"] == transaction_id:
                t["status"] = "held"
                t["hold_reason"] = reason
                return {"held": True, "transaction_id": transaction_id, "reason": reason}
                
        return {"error": "Transaction not found"}
        
    async def _release_hold(self, data: Dict) -> Dict:
        """Release held funds"""
        transaction_id = data.get("transaction_id")
        
        for t in self.transactions:
            if t["id"] == transaction_id:
                t["status"] = "completed"
                t["hold_reason"] = None
                return {"released": True, "transaction_id": transaction_id}
                
        return {"error": "Transaction not found"}
        
    # Helper methods
    async def _release_available_funds(self, creator_id: str) -> int:
        """Release funds past hold period to available balance"""
        now = datetime.utcnow()
        released = 0
        
        for t in self.transactions:
            if (t["creator_id"] == creator_id and 
                not t.get("released") and 
                t["status"] == "completed"):
                
                hold_until = datetime.fromisoformat(t["hold_until"])
                if now >= hold_until:
                    self.balances[creator_id] = self.balances.get(creator_id, 0) + t["creator_earnings_cents"]
                    t["released"] = True
                    released += t["creator_earnings_cents"]
                    
        return released
        
    async def _calculate_held_balance(self, creator_id: str) -> int:
        """Calculate amount still in hold period"""
        held = 0
        now = datetime.utcnow()
        
        for t in self.transactions:
            if (t["creator_id"] == creator_id and 
                not t.get("released") and 
                t["status"] == "completed"):
                
                hold_until = datetime.fromisoformat(t["hold_until"])
                if now < hold_until:
                    held += t["creator_earnings_cents"]
                    
        return held
        
    def health(self) -> Dict:
        h = super().health()
        h["registered_creators"] = len(self.creators)
        h["total_transactions"] = len(self.transactions)
        h["total_payouts"] = sum(len(p) for p in self.payouts.values())
        h["platform_fee_percent"] = self.config["platform_fee_percent"]
        return h
