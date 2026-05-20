"""Tests for the RestrictedPython sandbox — Reasoning Engine Plan 3."""

import pytest

from app.core.sandbox import (
    ALLOWED_MODULES,
    BLOCKED_BUILTINS,
    SandboxResult,
    run_sandboxed,
)


# --- Task 2: SandboxResult model + successful exec ------------------------

def test_successful_exec_returns_structured_result():
    r = run_sandboxed("result = 2 + 3")
    assert isinstance(r, SandboxResult)
    assert r.success is True
    assert r.result == 5
    assert r.error is None and r.error_type is None


def test_allowed_module_import_works():
    r = run_sandboxed("import math\nresult = math.sqrt(144)")
    assert r.success is True
    assert r.result == 12.0


def test_pm_computations_is_importable():
    code = (
        "from app.lib.pm_computations import compute_cpm\n"
        "result = compute_cpm is not None"
    )
    r = run_sandboxed(code)
    assert r.success is True and r.result is True


# --- Task 3: Import whitelist enforcement ---------------------------------

@pytest.mark.parametrize("module", ["os", "sys", "subprocess", "socket", "shutil"])
def test_dangerous_module_imports_are_blocked(module):
    r = run_sandboxed(f"import {module}")
    assert r.success is False
    assert r.error_type in ("ImportError", "SyntaxError")
    if r.error_type == "ImportError":
        assert module in r.error


def test_from_import_of_blocked_module_is_blocked():
    r = run_sandboxed("from os import getcwd\nresult = getcwd()")
    assert r.success is False


def test_whitelisted_modules_are_the_safe_set():
    assert "os" not in ALLOWED_MODULES and "sys" not in ALLOWED_MODULES
    assert {"math", "statistics", "datetime", "json"} <= ALLOWED_MODULES
    assert "app.lib.pm_computations" in ALLOWED_MODULES


# --- Task 4: Blocked builtins ---------------------------------------------

def test_open_is_not_available():
    r = run_sandboxed("result = open('secret.txt')")
    assert r.success is False
    assert r.error_type in ("NameError", "SyntaxError")


def test_eval_is_blocked():
    # RestrictedPython rejects eval() at compile time.
    r = run_sandboxed("result = eval('1 + 1')")
    assert r.success is False
    assert r.error_type in ("SyntaxError", "NameError")


def test_exec_is_blocked():
    r = run_sandboxed("exec('x = 1')")
    assert r.success is False


def test_dunder_import_is_blocked():
    r = run_sandboxed("result = __import__('os')")
    assert r.success is False


def test_blocked_builtins_constant_lists_the_dangerous_names():
    assert {"open", "eval", "exec", "__import__"} <= BLOCKED_BUILTINS


# --- Task 5: State injection + output capture -----------------------------

def test_state_is_injected_as_variables():
    r = run_sandboxed("result = activities + bonus", {"activities": 10, "bonus": 5})
    assert r.success is True and r.result == 15


def test_state_round_trip_through_namespace():
    state = {"crew": [4, 6, 2]}
    r = run_sandboxed("total = sum(crew)\nresult = total", state)
    assert r.success is True
    assert r.result == 12
    assert r.namespace["total"] == 12


def test_injected_state_is_copied_not_mutated_in_caller():
    state = {"items": [1, 2, 3]}
    run_sandboxed("items.append(99)", state)
    assert state == {"items": [1, 2, 3]}  # caller's dict untouched


def test_stdout_is_captured():
    r = run_sandboxed("print('hello'); print('world')")
    assert r.success is True
    assert "hello" in r.stdout and "world" in r.stdout


def test_result_variable_is_configurable():
    r = run_sandboxed("answer = 7 * 6", result_var="answer")
    assert r.success is True and r.result == 42


def test_missing_result_variable_yields_none():
    r = run_sandboxed("x = 1")
    assert r.success is True and r.result is None


# --- Task 6: Error handling — syntax & runtime ----------------------------

def test_syntax_error_is_captured_not_raised():
    r = run_sandboxed("def broken(:\n    pass")
    assert r.success is False
    assert r.error_type == "SyntaxError"
    assert r.error and "yntax" in r.error


def test_runtime_error_is_captured():
    r = run_sandboxed("result = 1 / 0")
    assert r.success is False
    assert r.error_type == "ZeroDivisionError"


def test_name_error_is_captured():
    r = run_sandboxed("result = undefined_name + 1")
    assert r.success is False
    assert r.error_type == "NameError"


def test_stdout_before_a_crash_is_still_returned():
    r = run_sandboxed("print('made it')\nresult = 1 / 0")
    assert r.success is False
    assert "made it" in r.stdout


def test_sandbox_never_raises_for_bad_code():
    # Whatever the input, run_sandboxed returns a SandboxResult.
    for bad in ["@@@", "raise Exception('boom')", "1/0", "import os"]:
        assert isinstance(run_sandboxed(bad), SandboxResult)
