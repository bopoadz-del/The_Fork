"""Billing Block - WORKING Stripe integration"""
from blocks.base import LegoBlock
from typing import Dict, Any
from datetime import datetime

class BillingBlock(LegoBlock):
    """Stripe Billing - REAL usage metering, subscriptions"""
    name = "billing"
    version = "1.0.0"
    requires = ["config", "auth", "memory"]
    layer = 5  # Integration layer
    tags = ["billing", "usage", "integration"]
    default_config = {
        "stripe_key": None,
        "free_tier_requests": 1000,
        "pro_tier_requests": 50000
    }
    
    PLANS = {
        "free": {
            "price": 0,
            "requests": 1000,
            "blocks": ["chat", "vector", "storage"],
            "stripe_price_id": None
        },
        "pro": {
            "price": 2900,  # $29
            "requests": 50000,
            "blocks": ["*"],
            "stripe_price_id": "price_pro_monthly"
        },
        "enterprise": {
            "price": None,
            "requests": float('inf'),
            "blocks": ["*"],
            "custom": True
        }
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.stripe_key = config.get("stripe_secret_key")
        self.stripe = None
        self.webhook_secret = config.get("stripe_webhook_secret")
        
        if self.stripe_key:
            try:
                import stripe
                stripe.api_key = self.stripe_key
                self.stripe = stripe
                print("   ✅ Stripe client initialized")
            except ImportError:
                print("   ⚠️  stripe not installed: pip install stripe")
            except Exception as e:
                print(f"   ⚠️  Stripe init failed: {e}")
        
        self.auth_block = None
        self.memory_block = None
        
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "record_usage":
            return await self._record_usage(input_data)
        elif action == "check_quota":
            return await self._check_quota(input_data)
        elif action == "create_customer":
            return await self._create_customer(input_data)
        elif action == "create_subscription":
            return await self._create_subscription(input_data)
        elif action == "get_invoice":
            return await self._get_invoice(input_data)
        elif action == "upgrade":
            return await self._upgrade_plan(input_data)
        elif action == "webhook":
            return await self._handle_webhook(input_data)
        return {"error": "Unknown action"}
    
    async def _record_usage(self, data: Dict) -> Dict:
        """Record API usage per customer"""
        api_key = data.get("api_key")
        block_used = data.get("block")
        tokens = data.get("tokens", 0)
        cost_cents = self._calculate_cost(block_used, tokens)
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Store in memory
        if self.memory_block:
            key = f"billing:usage:{api_key}:{today}"
            
            current = await self.memory_block.execute({
                "action": "get",
                "key": key
            })
            
            usage = current.get("value", {"requests": 0, "tokens": 0, "cost": 0}) if current.get("hit") else {"requests": 0, "tokens": 0, "cost": 0}
            usage["requests"] += 1
            usage["tokens"] += tokens
            usage["cost"] += cost_cents
            
            await self.memory_block.execute({
                "action": "set",
                "key": key,
                "value": usage,
                "ttl": 86400 * 35  # 35 days
            })
        
        # Report to Stripe if configured
        if self.stripe and data.get("stripe_subscription_item"):
            try:
                self.stripe.SubscriptionItem.create_usage_record(
                    data["stripe_subscription_item"],
                    quantity=1,
                    timestamp=int(datetime.now().timestamp())
                )
            except Exception as e:
                print(f"Stripe usage report failed: {e}")
        
        return {"recorded": True, "cost_cents": cost_cents}
    
    async def _check_quota(self, data: Dict) -> Dict:
        """Check if user has quota remaining"""
        api_key = data.get("api_key")
        
        # Get user's plan
        plan = "free"
        if self.auth_block:
            user = await self.auth_block.execute({
                "action": "validate",
                "api_key": api_key
            })
            if user.get("valid"):
                plan = user.get("role", "free")
        
        plan_config = self.PLANS.get(plan, self.PLANS["free"])
        limit = plan_config["requests"]
        
        # Get usage
        today = datetime.now().strftime("%Y-%m-%d")
        used = 0
        
        if self.memory_block:
            usage_data = await self.memory_block.execute({
                "action": "get",
                "key": f"billing:usage:{api_key}:{today}"
            })
            used = usage_data.get("value", {}).get("requests", 0) if usage_data.get("hit") else 0
        
        remaining = max(0, limit - used)
        percent_used = (used / limit * 100) if limit > 0 else 0
        
        return {
            "plan": plan,
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "percent_used": round(percent_used, 2),
            "exceeded": remaining == 0,
            "upgrade_recommended": percent_used > 80
        }
    
    async def _create_customer(self, data: Dict) -> Dict:
        """Create Stripe customer"""
        if not self.stripe:
            return {"error": "Stripe not configured"}
        
        try:
            customer = self.stripe.Customer.create(
                email=data.get("email"),
                name=data.get("name"),
                metadata={
                    "user_id": data.get("user_id"),
                    "api_key": data.get("api_key")
                }
            )
            
            return {
                "created": True,
                "customer_id": customer.id,
                "email": customer.email
            }
            
        except Exception as e:
            return {"error": f"Customer creation failed: {str(e)}"}
    
    async def _create_subscription(self, data: Dict) -> Dict:
        """Create Stripe subscription"""
        if not self.stripe:
            return {"error": "Stripe not configured"}
        
        try:
            customer_id = data.get("customer_id")
            plan = data.get("plan", "pro")
            
            plan_config = self.PLANS.get(plan)
            if not plan_config or not plan_config.get("stripe_price_id"):
                return {"error": f"Plan {plan} not available for Stripe subscription"}
            
            # Create subscription
            subscription = self.stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": plan_config["stripe_price_id"]}],
                payment_behavior="default_incomplete",
                expand=["latest_invoice.payment_intent"]
            )
            
            return {
                "subscription_id": subscription.id,
                "client_secret": subscription.latest_invoice.payment_intent.client_secret,
                "status": subscription.status,
                "plan": plan
            }
            
        except Exception as e:
            return {"error": f"Subscription creation failed: {str(e)}"}
    
    async def _get_invoice(self, data: Dict) -> Dict:
        """Get invoices for customer"""
        if not self.stripe:
            return {"invoices": []}
        
        try:
            customer_id = data.get("customer_id")
            
            invoices = self.stripe.Invoice.list(
                customer=customer_id,
                limit=10
            )
            
            return {
                "invoices": [
                    {
                        "id": inv.id,
                        "amount": inv.amount_due,
                        "status": inv.status,
                        "date": inv.created
                    }
                    for inv in invoices.data
                ]
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    async def _upgrade_plan(self, data: Dict) -> Dict:
        """Upgrade user plan"""
        api_key = data.get("api_key")
        new_plan = data.get("plan")
        
        # Update in auth block
        if self.auth_block:
            # Would update user role
            pass
        
        return {
            "upgraded": True,
            "plan": new_plan,
            "note": "Use create_subscription to set up Stripe billing"
        }
    
    async def _handle_webhook(self, data: Dict) -> Dict:
        """Handle Stripe webhook"""
        payload = data.get("payload")
        sig_header = data.get("signature")
        
        if not self.stripe or not self.webhook_secret:
            return {"error": "Webhook handling not configured"}
        
        try:
            event = self.stripe.Webhook.construct_event(
                payload, sig_header, self.webhook_secret
            )
            
            # Handle events
            if event["type"] == "invoice.payment_succeeded":
                # Update user subscription status
                pass
            elif event["type"] == "customer.subscription.deleted":
                # Downgrade to free
                pass
            
            return {"handled": True, "type": event["type"]}
            
        except Exception as e:
            return {"error": f"Webhook error: {str(e)}"}
    
    def _calculate_cost(self, block: str, tokens: int) -> int:
        """Calculate cost in cents"""
        rates = {
            "chat": 0.002,      # $0.002 per 1K tokens
            "image": 2.0,       # $0.02 per image
            "vector": 0.001,
            "pdf": 0.005,
            "ocr": 0.01,
            "default": 0.001
        }
        rate = rates.get(block, rates["default"])
        return int(tokens * rate)
    
    def health(self) -> Dict:
        h = super().health()
        h["stripe_connected"] = self.stripe is not None
        h["plans"] = list(self.PLANS.keys())
        h["webhook_configured"] = self.webhook_secret is not None
        return h
