import pytest
"""
Live API Tests - Test the deployed Render instance
"""

import asyncio
import aiohttp
import sys
from datetime import datetime

BASE_URL = "https://ssdppg.onrender.com"

results = {"passed": [], "failed": [], "total": 0}

def log(endpoint, test, passed, error=None):
    results["total"] += 1
    status = "✅" if passed else "❌"
    print(f"  {status} {endpoint}.{test}")
    if error:
        print(f"     {error}")
    (results["passed"] if passed else results["failed"]).append({
        "endpoint": endpoint, "test": test, "error": error
    })

@pytest.mark.asyncio
async def test_health():
    """Test health endpoint"""
    print("\n🩺 Testing /health...")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BASE_URL}/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    log("health", "status_ok", True)
                    log("health", f"blocks_{data.get('blocks_available', 0)}", True)
                else:
                    log("health", "status", False, f"HTTP {resp.status}")
        except Exception as e:
            log("health", "connect", False, str(e))

@pytest.mark.asyncio
async def test_blocks_list():
    """Test blocks list endpoint"""
    print("\n📋 Testing /blocks...")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BASE_URL}/blocks") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    total = data.get('total', 0)
                    log("blocks", f"count_{total}", total >= 19)
                    
                    # Check for domain containers
                    blocks = [b['name'] for b in data.get('blocks', [])]
                    key_blocks = ['construction', 'medical', 'legal', 'finance', 'security', 'ai_core']
                    for block in key_blocks:
                        log("blocks", f"has_{block}", block in blocks)
                else:
                    log("blocks", "status", False, f"HTTP {resp.status}")
        except Exception as e:
            log("blocks", "connect", False, str(e))

@pytest.mark.asyncio
async def test_execute_construction():
    """Test construction block execution"""
    print("\n🏗️ Testing /execute (construction)...")
    
    async with aiohttp.ClientSession() as session:
        try:
            payload = {
                "block": "construction",
                "input": {},
                "params": {"action": "extract_measurements"}
            }
            async with session.post(f"{BASE_URL}/execute", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "success":
                        log("execute", "construction", True)
                    else:
                        log("execute", "construction", False, data.get("error"))
                else:
                    text = await resp.text()
                    log("execute", "construction", False, f"HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            log("execute", "construction", False, str(e))

@pytest.mark.asyncio
async def test_execute_security():
    """Test security block execution"""
    print("\n🔐 Testing /execute (security)...")
    
    async with aiohttp.ClientSession() as session:
        try:
            payload = {
                "block": "security",
                "input": {},
                "params": {"action": "create_key", "owner": "test"}
            }
            async with session.post(f"{BASE_URL}/execute", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    if result.get("api_key", "").startswith("cb_"):
                        log("execute", "security_create_key", True)
                    else:
                        log("execute", "security_create_key", False, "Invalid key format")
                else:
                    text = await resp.text()
                    log("execute", "security_create_key", False, f"HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            log("execute", "security_create_key", False, str(e))

@pytest.mark.asyncio
async def test_execute_ai_core():
    """Test AI core block execution"""
    print("\n🤖 Testing /execute (ai_core)...")
    
    async with aiohttp.ClientSession() as session:
        try:
            payload = {
                "block": "ai_core",
                "input": {},
                "params": {"action": "leaderboard"}
            }
            async with session.post(f"{BASE_URL}/execute", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    if result.get("rankings"):
                        log("execute", "ai_core_leaderboard", True)
                    else:
                        log("execute", "ai_core_leaderboard", False, "No rankings")
                else:
                    text = await resp.text()
                    log("execute", "ai_core_leaderboard", False, f"HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            log("execute", "ai_core_leaderboard", False, str(e))

@pytest.mark.asyncio
async def test_chain_execution():
    """Test chain execution"""
    print("\n⛓️ Testing /chain...")
    
    async with aiohttp.ClientSession() as session:
        try:
            payload = {
                "steps": [
                    {"block": "security", "params": {"action": "create_key", "owner": "chain_test"}}
                ],
                "initial_input": {}
            }
            async with session.post(f"{BASE_URL}/chain", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success"):
                        log("chain", "execution", True)
                    else:
                        log("chain", "execution", False, data.get("error"))
                else:
                    text = await resp.text()
                    log("chain", "execution", False, f"HTTP {resp.status}: {text[:100]}")
        except Exception as e:
            log("chain", "execution", False, str(e))

async def run():
    print("=" * 60)
    print("🌐 LIVE API TESTS")
    print(f"Target: {BASE_URL}")
    print("=" * 60)
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}")
    
    await test_health()
    await test_blocks_list()
    await test_execute_construction()
    await test_execute_security()
    await test_execute_ai_core()
    await test_chain_execution()
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    passed = len(results["passed"])
    failed = len(results["failed"])
    total = results["total"]
    print(f"Total:  {total}")
    print(f"✅ Pass: {passed} ({passed/total*100:.0f}%)")
    print(f"❌ Fail: {failed} ({failed/total*100:.0f}%)")
    
    if failed > 0:
        print("\n❌ Failed:")
        for f in results["failed"]:
            print(f"  - {f['endpoint']}.{f['test']}")
    
    return failed == 0

if __name__ == "__main__":
    try:
        success = asyncio.run(run())
        sys.exit(0 if success else 1)
    except ImportError:
        print("\n⚠️  aiohttp not installed. Install with: pip install aiohttp")
        print("Running basic test instead...")
        # Fallback to simple test
        import subprocess
        subprocess.run([sys.executable, "tests/test_blocks_simple.py"])
