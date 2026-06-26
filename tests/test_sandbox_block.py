"""Tests for app.blocks.sandbox.SandboxBlock.

These tests exercise the block-level sandbox wrapper using harmless Python and
JavaScript snippets.  No dangerous code is executed and no live external
services are contacted.  Resource-limit assertions are skipped on Windows where
the `resource` module is unavailable.
"""

import sys

import pytest

from app.blocks.sandbox import SandboxBlock, _RESOURCE_AVAILABLE


@pytest.fixture
def block():
    return SandboxBlock()


# ---------------------------------------------------------------------------
# execute — Python
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_python_returns_result(block):
    result = await block.process({
        "action": "execute",
        "code": "result = 2 + 3",
        "language": "python",
    })
    assert result.get("success") is True
    assert result.get("result") == 5
    assert result.get("error") is None
    assert result.get("sandboxed") is True


@pytest.mark.asyncio
async def test_execute_python_captures_stdout(block):
    result = await block.process({
        "action": "execute",
        "code": "print('hello')\nprint('world')",
        "language": "python",
    })
    assert result.get("success") is True
    assert "hello" in result.get("stdout", "")
    assert "world" in result.get("stdout", "")


@pytest.mark.asyncio
async def test_execute_python_runtime_error_returned(block):
    result = await block.process({
        "action": "execute",
        "code": "result = 1 / 0",
        "language": "python",
    })
    assert result.get("success") is False
    assert "division by zero" in (result.get("error") or "")


@pytest.mark.asyncio
async def test_execute_python_inputs_injected(block):
    result = await block.process({
        "action": "execute",
        "code": "result = input('value: ') + '!'",
        "language": "python",
        "inputs": {"input": "hi"},
    })
    assert result.get("success") is True
    assert result.get("result") == "hi!"


@pytest.mark.asyncio
async def test_execute_python_allowed_module(block):
    result = await block.process({
        "action": "execute",
        "code": "import math\nresult = math.sqrt(144)",
        "language": "python",
    })
    assert result.get("success") is True
    assert result.get("result") == 12.0


@pytest.mark.asyncio
async def test_execute_blocked_dangerous_code(block):
    result = await block.process({
        "action": "execute",
        "code": "import os\nresult = os.system('echo bad')",
        "language": "python",
    })
    assert result.get("blocked") is True
    assert "safety check" in (result.get("error") or "").lower()


# ---------------------------------------------------------------------------
# execute — JavaScript (best-effort, skipped if node is unavailable)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_javascript_simple(block):
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not available")

    result = await block.process({
        "action": "execute",
        "code": "console.log(2 + 3);",
        "language": "javascript",
    })
    assert result.get("success") is True
    assert "5" in result.get("stdout", "")


# ---------------------------------------------------------------------------
# validate_code
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validate_code_safe_code(block):
    result = await block.process({
        "action": "validate_code",
        "code": "result = 2 + 3",
        "language": "python",
    })
    assert result.get("safe") is True
    assert result.get("violations") == []


@pytest.mark.asyncio
async def test_validate_code_detects_eval(block):
    result = await block.process({
        "action": "validate_code",
        "code": "result = eval('1 + 1')",
        "language": "python",
    })
    assert result.get("safe") is False
    assert any(v.get("pattern") == "eval(" for v in result.get("violations", []))


@pytest.mark.asyncio
async def test_validate_code_detects_open(block):
    result = await block.process({
        "action": "validate_code",
        "code": "result = open('x.txt').read()",
        "language": "python",
    })
    assert result.get("safe") is False
    assert any(v.get("pattern") == "open(" for v in result.get("violations", []))


@pytest.mark.asyncio
async def test_validate_code_warnings_infinite_loop(block):
    result = await block.process({
        "action": "validate_code",
        "code": "while True:\n    pass",
        "language": "python",
    })
    warnings = result.get("warnings", [])
    assert any("infinite loop" in w for w in warnings)


@pytest.mark.asyncio
async def test_validate_code_warnings_recursion(block):
    result = await block.process({
        "action": "validate_code",
        "code": "def fib(n):\n    return fib(n - 1) + fib(n - 2)\nresult = fib(3)",
        "language": "python",
    })
    warnings = result.get("warnings", [])
    assert any("recursion" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# check_safety
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_safety_clean_code(block):
    result = await block.process({
        "action": "check_safety",
        "code": "result = [x * 2 for x in range(3)]",
        "language": "python",
    })
    assert result.get("safe") is True
    assert result.get("score", 0) == 100


@pytest.mark.asyncio
async def test_check_safety_violations_reduce_score(block):
    result = await block.process({
        "action": "check_safety",
        "code": "result = eval('1 + 1') + open('x').read()",
        "language": "python",
    })
    assert result.get("safe") is False
    assert result.get("score", 100) < 100


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_stats_initial(block):
    result = await block.process({"action": "get_stats"})
    assert result.get("executions") == 0
    assert result.get("blocked") == 0
    assert result.get("policies") == 1
    assert result.get("active_sessions") == 0
    assert "memory_mb" in result.get("default_limits", {})


@pytest.mark.asyncio
async def test_get_stats_after_blocked_execution(block):
    await block.process({
        "action": "execute",
        "code": "import os\nresult = os.system('echo bad')",
        "language": "python",
    })
    result = await block.process({"action": "get_stats"})
    assert result.get("executions") == 1
    assert result.get("blocked") == 1


# ---------------------------------------------------------------------------
# create_policy + custom policy execution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_and_use_custom_policy(block):
    create_result = await block.process({
        "action": "create_policy",
        "name": "tight",
        "max_memory_mb": 64,
        "max_cpu_time": 1,
    })
    assert create_result.get("created") is True

    result = await block.process({
        "action": "execute",
        "code": "result = 10 * 10",
        "language": "python",
        "policy": "tight",
    })
    assert result.get("success") is True
    assert result.get("result") == 100


# ---------------------------------------------------------------------------
# Regression: default policy must exist before legacy initialization
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_policy_exists_without_legacy_init():
    fresh = SandboxBlock()
    assert "default" in fresh.policies
    result = await fresh.process({
        "action": "execute",
        "code": "result = 1 + 1",
        "language": "python",
    })
    assert result.get("success") is True


# ---------------------------------------------------------------------------
# POSIX-only resource limit sanity check
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform == "win32" or not _RESOURCE_AVAILABLE, reason="POSIX resource limits only")
@pytest.mark.asyncio
async def test_execute_python_sets_rlimit_on_posix(block):
    # Harmless code that just verifies the sandbox ran under a limit.
    result = await block.process({
        "action": "execute",
        "code": "import resource\nresult = resource.getrlimit(resource.RLIMIT_AS)[1]",
        "language": "python",
    })
    assert result.get("success") is True
    # The block caps virtual memory at the policy limit.
    assert result.get("result") == 512 * 1024 * 1024


# ---------------------------------------------------------------------------
# wrap_block
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wrap_block_returns_metadata(block):
    result = await block.process({
        "action": "wrap_block",
        "block_name": "chat",
        "policy": "default",
    })
    assert result.get("wrapped") is True
    assert result.get("block") == "chat"
    assert result.get("policy") == "default"


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_action_returns_error(block):
    result = await block.process({"action": "bad_action"})
    assert "error" in result
    assert "Unknown action" in result.get("error", "")
