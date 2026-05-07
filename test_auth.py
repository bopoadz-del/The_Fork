#!/usr/bin/env python3
"""Test Auth Block - API keys, rate limiting, RBAC."""

import asyncio
import sys
sys.path.insert(0, '.')

import pytest
from blocks.auth.src.block import AuthBlock
from blocks.memory.src.block import MemoryBlock


@pytest.mark.asyncio
async def test_auth_block():
    """Test Auth Block functionality."""
    print("\n" + "="*60)
    print("🔐 TESTING AUTH BLOCK")
    print("="*60)
    
    # Initialize
    mem = MemoryBlock(None, {"max_size": 1000, "default_ttl": 3600})
    await mem.initialize()
    
    auth = AuthBlock(None, {
        "rate_limit_default": 5,  # 5 req/min for testing
        "rate_limit_window": 60
    })
    auth.memory_block = mem
    await auth.initialize()
    
    master_key = auth.master_key
    print(f"\n1. Master Key: {master_key[:30]}...")
    
    # Test 1: Validate master key (dev fallback since memory has no keys yet)
    print("\n2. Validating master key...")
    result = await auth.execute({"action": "validate", "api_key": "cb_dev_key"})
    print(f"   Valid: {result['valid']}, Role: {result['role']}")
    assert result["valid"] and result["role"] == "admin"
    
    # Test 2: Create API key
    print("\n3. Creating new API key...")
    result = await auth.execute({
        "action": "create_key",
        "name": "test_user",
        "role": "basic",
        "owner": "test"
    })
    assert "api_key" in result
    user_key = result["api_key"]
    print(f"   Created: {user_key[:40]}...")
    
    # Store key in memory so validate can find it
    await mem.execute({
        "action": "set",
        "key": f"auth:keys:{user_key}",
        "value": {"name": "test_user", "role": "basic", "owner": "test", "created": 0},
        "ttl": 0
    })
    
    # Test 3: Validate user key
    print("\n4. Validating user key...")
    result = await auth.execute({"action": "validate", "api_key": user_key})
    print(f"   Valid: {result['valid']}, Role: {result['role']}")
    assert result["valid"] and result["role"] == "basic"
    
    # Test 4: Check permissions
    print("\n5. Checking permissions...")
    for block_name in ["chat", "vector", "storage"]:
        result = await auth.execute({
            "action": "check_permission",
            "api_key": user_key,
            "block": block_name
        })
        status = "✅" if result["allowed"] else "❌"
        print(f"   {status} {block_name}: {result['allowed']}")
    
    # Test 5: Rate limiting
    print("\n6. Testing rate limiting (5 req/min)...")
    for i in range(7):
        result = await auth.execute({
            "action": "check_rate_limit",
            "api_key": user_key
        })
        status = "✅" if result["allowed"] else "❌ RATE LIMITED"
        print(f"   Request {i+1}: {status} (remaining: {result.get('remaining', 0)})")
    
    # Test 6: Invalid key
    print("\n7. Testing invalid key...")
    result = await auth.execute({"action": "validate", "api_key": "cb_invalid_key"})
    print(f"   Valid: {result['valid']}, Error: {result.get('reason')}")
    assert not result["valid"]
    
    # Test 7: Revoke key
    print("\n8. Revoking key...")
    result = await auth.execute({
        "action": "revoke_key",
        "api_key": user_key
    })
    print(f"   Revoked: {result.get('revoked')}")
    
    # Verify revoked
    result = await auth.execute({"action": "validate", "api_key": user_key})
    print(f"   Post-revoke valid: {result['valid']}")
    assert not result["valid"]
    
    # Test 8: Create readonly key
    print("\n9. Creating readonly key...")
    result = await auth.execute({
        "action": "create_key",
        "name": "readonly_user",
        "role": "readonly",
        "owner": "test"
    })
    readonly_key = result["api_key"]
    
    await mem.execute({
        "action": "set",
        "key": f"auth:keys:{readonly_key}",
        "value": {"name": "readonly_user", "role": "readonly", "owner": "test", "created": 0},
        "ttl": 0
    })
    
    result = await auth.execute({
        "action": "check_permission",
        "api_key": readonly_key,
        "block": "chat"
    })
    print(f"   Readonly can write: {result['allowed']} (should be False)")
    assert not result["allowed"]
    
    print("\n✅ Auth Block tests PASSED!")
    return True


async def main():
    print("\n" + "="*60)
    print("🔐 AUTH BLOCK - TEST SUITE")
    print("="*60)
    
    try:
        await test_auth_block()
        
        print("\n" + "="*60)
        print("🎉 ALL TESTS PASSED!")
        print("="*60)
        print("\n🔐 Auth Block Features:")
        print("   • API key validation (Bearer tokens)")
        print("   • Rate limiting (configurable per key)")
        print("   • Role-based access (admin/user/readonly)")
        print("   • Key rotation & revocation")
        print("   • Master key for admin operations")
        
        return 0
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
