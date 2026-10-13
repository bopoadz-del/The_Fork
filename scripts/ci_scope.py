#!/usr/bin/env python3
"""Which tests a change needs, derived from the paths it touches.

A change entirely inside ONE owner package -- ``app/agents/hats/<hat>/`` or
``app/agents/base/`` -- plus tests and docs, runs that package's tests, the
tests it changed, and one short driver smoke test. Anything else (core code,
shared libraries, routers, workflows, config, two packages at once) runs the
full suite. Main always runs the full suite after a merge.

A test file belongs to a package when it refers to that package's module
path. The package list is read from the repository layout, never kept by
hand.

    python scripts/ci_scope.py BASE HEAD     # prints scope=..., tests=...
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
HATS = ROOT / "app" / "agents" / "hats"
#: The driver's own end-to-end test: a scoped change still drives one turn.
SMOKE = "tests/test_driver_mode.py"
_NEUTRAL = re.compile(r"(^docs/|^FLEET_OPS/|\.md$)")


def packages(root: Path = ROOT) -> Dict[str, str]:
    """Owner package -> its path prefix, from the layout."""
    out = {"base": "app/agents/base/"}
    hats = root / "app" / "agents" / "hats"
    if hats.is_dir():
        for d in sorted(hats.iterdir()):
            if d.is_dir() and (d / "__init__.py").exists():
                out[d.name] = f"app/agents/hats/{d.name}/"
    return out


def _owner(path: str, pkgs: Dict[str, str]) -> Optional[str]:
    for name, prefix in pkgs.items():
        if path.startswith(prefix):
            return name
    return None


def scope(changed: Iterable[str], pkgs: Dict[str, str]) -> Tuple[str, Optional[str], List[str]]:
    """(``full`` | ``scoped``, the one package or None, the changed test files)."""
    owners = set()
    tests: List[str] = []
    for path in (p.strip().replace("\\", "/") for p in changed):
        if not path:
            continue
        if path.startswith("tests/"):
            if path.endswith(".py") and Path(path).name.startswith("test_"):
                tests.append(path)
            elif path.endswith(".py"):
                return "full", None, []  # a shared test helper or conftest
            continue
        if _NEUTRAL.search(path):
            continue
        owner = _owner(path, pkgs)
        if owner is None:
            return "full", None, []  # core or shared
        owners.add(owner)
    if len(owners) > 1:
        return "full", None, []
    return "scoped", (owners.pop() if owners else None), sorted(set(tests))


def registered_names(package_dir: Path) -> List[str]:
    """Formula and tool names the package registers, read from its source:
    functions decorated with ``@formula(...)`` (or its ``name=``) and the
    names given to ``@tool("...")``. Parsed, never imported."""
    import ast

    names: List[str] = []
    for f in sorted(package_dir.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fname = getattr(dec.func, "id", getattr(dec.func, "attr", ""))
                if fname == "formula":
                    given = [k.value.value for k in dec.keywords
                             if k.arg == "name" and isinstance(k.value, ast.Constant)]
                    names.append(given[0] if given else node.name)
                elif fname == "tool" and dec.args and isinstance(dec.args[0], ast.Constant):
                    names.append(str(dec.args[0].value))
    return sorted(set(n for n in names if len(n) > 3))


def package_tests(package: Optional[str], pkgs: Dict[str, str], root: Path = ROOT) -> List[str]:
    """Test files that refer to ``package``'s module path or name one of the
    formulas or tools it registers."""
    if not package:
        return []
    dotted = pkgs[package].rstrip("/").replace("/", ".")
    words = registered_names(root / pkgs[package])
    parts = [re.escape(dotted) + r"\b", re.escape(pkgs[package])]
    if words:
        parts.append(r"(?<![A-Za-z0-9_])(?:" + "|".join(map(re.escape, words)) + r")(?![A-Za-z0-9_])")
    rx = re.compile("|".join(parts))
    out = []
    for f in sorted((root / "tests").glob("test_*.py")):
        try:
            if rx.search(f.read_text(encoding="utf-8", errors="ignore")):
                out.append(f"tests/{f.name}")
        except OSError:
            continue
    return out


def selection(changed: Iterable[str], root: Path = ROOT) -> Tuple[str, List[str]]:
    pkgs = packages(root)
    kind, package, changed_tests = scope(changed, pkgs)
    if kind == "full":
        return "full", []
    existing = [t for t in changed_tests if (root / t).exists()]
    return "scoped", sorted(set(existing + package_tests(package, pkgs, root) + [SMOKE]))


def main(argv: List[str]) -> int:
    base, head = argv[0], argv[1]
    diff = subprocess.run(["git", "diff", "--name-only", base, head], cwd=ROOT,
                          capture_output=True, text=True)
    if diff.returncode != 0:
        print("scope=full")
        print("tests=")
        return 0
    kind, tests = selection(diff.stdout.splitlines())
    print(f"scope={kind}")
    print("tests=" + " ".join(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
