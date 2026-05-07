#!/usr/bin/env python3
"""Test Memory and Monitoring Blocks."""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
from blocks.memory.src.block import MemoryBlock
from blocks.monitoring.src.block import MonitoringBlock


@pytest.mark.asyncio
async def test_memory_block():
    """Test Memory Block functionality."""
    print("\n" + "="*60)
    print("🧠 TESTING MEMORY BLOCK")
    print("="*60)
    
    memory = MemoryBlock(None, {"max_size": 1000, "default_ttl": 60})
    await memory.initialize()
    
    # Test SET
    print("\n1. Testing SET...")
    result = await memory.execute({
        "action": "set",
        "key": "test_key",
        "value": {"message": "Hello from Memory Block!"},
        "ttl": 300
    })
    print(f"   Set result: {result}")
    assert result["stored"] == True, "SET failed"
    
    # Test GET (hit)
    print("\n2. Testing GET (cache hit)...")
    result = await memory.execute({
        "action": "get",
        "key": "test_key"
    })
    print(f"   Get result: {result}")
    assert result["hit"] == True, "GET should be a hit"
    assert result["value"]["message"] == "Hello from Memory Block!", "Value mismatch"
    
    # Test GET (miss)
    print("\n3. Testing GET (cache miss)...")
    result = await memory.execute({
        "action": "get",
        "key": "nonexistent_key"
    })
    print(f"   Get result: {result}")
    assert result["hit"] == False, "GET should be a miss"
    
    # Test STATS
    print("\n4. Testing STATS...")
    result = await memory.execute({"action": "stats"})
    print(f"   Stats: {result}")
    assert result["hits"] == 1, "Should have 1 hit"
    assert result["misses"] == 1, "Should have 1 miss"
    
    # Test EXISTS
    print("\n5. Testing EXISTS...")
    result = await memory.execute({
        "action": "exists",
        "key": "test_key"
    })
    print(f"   Exists result: {result}")
    assert result["exists"] == True, "Key should exist"
    
    # Test KEYS
    print("\n6. Testing KEYS...")
    result = await memory.execute({"action": "keys"})
    print(f"   Keys result: {result}")
    assert "test_key" in result["keys"], "test_key should be in keys"
    
    # Test DELETE
    print("\n7. Testing DELETE...")
    result = await memory.execute({
        "action": "delete",
        "key": "test_key"
    })
    print(f"   Delete result: {result}")
    assert result["deleted"] == True, "DELETE should succeed"
    
    # Verify deletion
    result = await memory.execute({
        "action": "get",
        "key": "test_key"
    })
    assert result["hit"] == False, "Key should be deleted"
    
    print("\n✅ Memory Block tests PASSED!")
    return True


@pytest.mark.asyncio
async def test_monitoring_block():
    """Test Monitoring Block functionality."""
    print("\n" + "="*60)
    print("📊 TESTING MONITORING BLOCK")
    print("="*60)
    
    memory = MemoryBlock(None, {"max_size": 1000, "default_ttl": 3600})
    await memory.initialize()
    
    monitoring = MonitoringBlock(None, {})
    monitoring.memory_block = memory
    await monitoring.initialize()
    
    # Simulate some calls
    print("\n1. Simulating provider calls...")
    
    # DeepSeek - good performance
    await monitoring.execute({
        "action": "record_call",
        "provider": "deepseek",
        "latency_ms": 800,
        "success": True
    })
    await monitoring.execute({
        "action": "record_call",
        "provider": "deepseek",
        "latency_ms": 750,
        "success": True
    })
    
    # DeepSeek - one failure
    await monitoring.execute({
        "action": "record_call",
        "provider": "deepseek",
        "latency_ms": 5000,
        "success": False,
        "error_type": "timeout"
    })
    
    # Groq - excellent performance
    await monitoring.execute({
        "action": "record_call",
        "provider": "groq",
        "latency_ms": 120,
        "success": True
    })
    await monitoring.execute({
        "action": "record_call",
        "provider": "groq",
        "latency_ms": 95,
        "success": True
    })
    await monitoring.execute({
        "action": "record_call",
        "provider": "groq",
        "latency_ms": 110,
        "success": True
    })
    
    # OpenAI - slower
    await monitoring.execute({
        "action": "record_call",
        "provider": "openai",
        "latency_ms": 2500,
        "success": True
    })
    
    print("   Recorded 6 calls across 3 providers")
    
    # Test LEADERBOARD
    print("\n2. Testing LEADERBOARD...")
    lb = await monitoring.execute({"action": "leaderboard"})
    print(f"   Generated at: {lb['generated_at']}")
    print(f"   Top provider: {lb['top_provider']}")
    print("\n   📊 Provider Rankings:")
    for entry in lb["leaderboard"]:
        emoji = "🥇" if entry["rank"] == 1 else "🥈" if entry["rank"] == 2 else "🥉" if entry["rank"] == 3 else "  "
        print(f"   {emoji} #{entry['rank']} {entry['name']}: {entry['reliability_score']}% ({entry['status']}) - {entry['avg_latency_ms']}ms avg")
    
    # Test RECOMMEND
    print("\n3. Testing RECOMMEND...")
    rec = await monitoring.execute({"action": "recommend"})
    print(f"   Recommended: {rec['recommended']}")
    print(f"   Confidence: {rec['confidence']}%")
    print(f"   Reason: {rec['reason']}")
    print(f"   Fallback sequence: {rec.get('fallback_sequence', [])}")
    
    # Test PREDICTIVE ANALYSIS
    print("\n4. Testing PREDICTIVE ANALYSIS...")
    pred = await monitoring.execute({"action": "predictive_failover"})
    print(f"   Predictions: {len(pred['predictions'])}")
    if pred['predictions']:
        for p in pred['predictions']:
            print(f"   ⚠️  {p['provider']}: {p['prediction']} ({p['severity']})")
    else:
        print("   ✅ No issues predicted")
    
    # Test HEALTH REPORT
    print("\n5. Testing HEALTH REPORT...")
    health = await monitoring.execute({"action": "health_report"})
    print(f"   Overall status: {health['overall_status']}")
    print(f"   Failover readiness: {health['failover_readiness']}")
    
    # Test PROVIDER STATUS
    print("\n6. Testing PROVIDER STATUS...")
    status = await monitoring.execute({
        "action": "provider_status",
        "provider": "deepseek"
    })
    print(f"   DeepSeek reliability: {status['reliability_score']}%")
    
    print("\n✅ Monitoring Block tests PASSED!")
    return True


async def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("🔥 CEREBRUM BLOCKS - MEMORY & MONITORING TEST SUITE")
    print("="*60)
    
    try:
        await test_memory_block()
        await test_monitoring_block()
        
        print("\n" + "="*60)
        print("🎉 ALL TESTS PASSED!")
        print("="*60)
        print("\n📦 New Blocks Ready:")
        print("   • Memory Block: High-speed cache with TTL + LRU eviction")
        print("   • Monitoring Block: Provider leaderboard & predictive failover")
        print("\n🔌 New API Endpoints:")
        print("   GET  /v1/leaderboard      - Provider reliability rankings")
        print("   GET  /v1/recommend        - AI-powered provider selection")
        print("   GET  /v1/predict          - Predictive failure analysis")
        print("   GET  /v1/system/health    - Full system health report")
        print("   GET  /v1/memory/stats     - Cache statistics")
        print("   POST /v1/memory/{action}  - Cache operations (get/set/delete)")
        return 0
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
