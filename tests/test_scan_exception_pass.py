"""CI twin 2d — ``except Exception: pass`` fails the build.

S110 already gates bare ``except: pass`` (baseline 0). This twin is the
typed form ruff does not cover: ``except Exception: pass`` and
``except Exception as <name>: pass``. The detector lives in
``scripts/scan_exception_pass.py`` so the lint integrity job can run it
without pytest.

Each test names the mutation it kills.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "scan_exception_pass", _REPO / "scripts" / "scan_exception_pass.py"
)
scan_exception_pass = importlib.util.module_from_spec(_SPEC)
sys.modules["scan_exception_pass"] = scan_exception_pass
_SPEC.loader.exec_module(scan_exception_pass)


def _lines(src: str) -> list[int]:
    return scan_exception_pass.exception_pass_lines(ast.parse(src))


# ── the detector itself ───────────────────────────────────────────────────


def test_except_exception_pass_is_caught():
    """Mutation killed: a detector that only matches bare ``except:``."""
    assert _lines("try:\n    x()\nexcept Exception:\n    pass\n") == [3]


def test_except_exception_as_name_pass_is_caught():
    """Mutation killed: requiring the handler to be unnamed."""
    assert _lines("try:\n    x()\nexcept Exception as err:\n    pass\n") == [3]


def test_tuple_containing_exception_pass_is_caught():
    assert _lines("try:\n    x()\nexcept (ValueError, Exception):\n    pass\n") == [3]


def test_a_body_that_logs_is_not_silent():
    assert _lines("try:\n    x()\nexcept Exception:\n    log('boom')\n") == []


def test_narrow_oserror_pass_is_not_this_twin():
    """S110-shaped cleanup on a named type is a deliberate decision."""
    assert _lines("try:\n    x()\nexcept OSError:\n    pass\n") == []


def test_bare_except_pass_is_not_this_twin():
    """Bare ``except: pass`` is ruff S110. Do not steal that gate."""
    assert _lines("try:\n    x()\nexcept:\n    pass\n") == []


def test_baseexception_pass_is_not_this_twin():
    """Widening to BaseException is the SSE-watchdog AST test's job."""
    assert _lines("try:\n    x()\nexcept BaseException:\n    pass\n") == []


# ── planted fixture: fail, then pass once the swallow is gone ─────────────


def test_a_planted_exception_pass_fails_the_scanner(tmp_path):
    """A guard that cannot fail is not a guard."""
    (tmp_path / "planted.py").write_text(
        "try:\n    x()\nexcept Exception:\n    pass\n", encoding="utf-8"
    )
    findings = scan_exception_pass.scan(tmp_path)
    assert findings == ["planted.py:3"], findings


def test_removing_the_planted_pass_clears_the_scanner(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text("try:\n    x()\nexcept Exception:\n    pass\n", encoding="utf-8")
    assert scan_exception_pass.scan(tmp_path) == ["planted.py:3"]
    planted.write_text(
        "try:\n    x()\nexcept Exception:\n    log('boom')\n", encoding="utf-8"
    )
    assert scan_exception_pass.scan(tmp_path) == []


def test_an_allowlisted_line_with_a_reason_is_ignored(tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text(
        "try:\n    x()\nexcept Exception:\n    pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        scan_exception_pass,
        "ALLOWLIST",
        {"ok.py:3": "fixture — proves the named-reason hatch works"},
    )
    assert scan_exception_pass.scan(tmp_path) == []


# ── the walk must actually visit the tree ─────────────────────────────────


def test_the_walk_visits_known_repo_files():
    """Mutation killed: commenting out ``os.walk`` / the file loop.

    ``test_the_tree_is_clean_right_now`` would then pass vacuously — zero
    files scanned, zero findings. This asserts the walk still reaches
    files the scanner is supposed to police, and still skips tests/.
    """
    rels = {rel for rel, _ in scan_exception_pass.iter_python_files(_REPO)}
    assert rels, "walk returned no files — the scanner is a no-op"
    assert "app/main.py" in rels
    assert "scripts/audit_stubs.py" in rels
    assert "scripts/scan_exception_pass.py" in rels
    assert not any(r.startswith("tests/") for r in rels)


def test_the_walk_is_not_commented_out_in_source():
    """Source-level probe of the same mutation: the walk loop must exist."""
    src = (_REPO / "scripts" / "scan_exception_pass.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "iter_python_files"
    )
    walks = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr in {"walk", "rglob"})
            or (isinstance(node.func, ast.Name) and node.func.id == "walk")
        )
    ]
    assert walks, (
        "iter_python_files no longer walks the tree — commenting out "
        "os.walk / rglob would make the clean-tree test a tautology"
    )


# ── the gate itself ───────────────────────────────────────────────────────


def test_the_tree_is_clean_right_now():
    """If this fails, an ``except Exception: pass`` was committed."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "scripts/scan_exception_pass.py"],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"silent except Exception: pass in the tree:\n{result.stdout}"
    )


def test_allowlist_entries_must_name_a_reason():
    """An allowlist without a reason is how a swallow becomes invisible."""
    for key, reason in scan_exception_pass.ALLOWLIST.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"ALLOWLIST[{key!r}] has no named reason — fix the handler "
            "or write why it must stay"
        )
