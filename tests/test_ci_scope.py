"""The CI test selector is derived from paths (ORDERS_2026-10-13T0748Z step 2)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ci_scope  # noqa: E402

PKGS = {"base": "app/agents/base/", "alpha": "app/agents/hats/alpha/", "beta": "app/agents/hats/beta/"}


def test_one_package_plus_its_tests_is_scoped():
    kind, pkg, tests = ci_scope.scope(
        ["app/agents/hats/alpha/formulas/x.py", "tests/test_alpha_x.py", "docs/note.md"], PKGS)
    assert (kind, pkg, tests) == ("scoped", "alpha", ["tests/test_alpha_x.py"])


def test_two_packages_select_the_full_suite():
    assert ci_scope.scope(["app/agents/hats/alpha/a.py", "app/agents/hats/beta/b.py"], PKGS)[0] == "full"


def test_any_shared_file_selects_the_full_suite():
    for shared in ("app/agents/driver/loop.py", "app/lib/formula_registry.py", ".github/workflows/test.yml",
                   "requirements.txt", "tests/conftest.py", "tests/lane5_core_support.py"):
        assert ci_scope.scope(["app/agents/hats/alpha/a.py", shared], PKGS)[0] == "full", shared


def test_packages_come_from_the_layout():
    pkgs = ci_scope.packages()
    assert pkgs["base"] == "app/agents/base/"
    assert all(p.startswith("app/agents/hats/") for n, p in pkgs.items() if n != "base")
    assert len(pkgs) > 1


def test_a_scoped_selection_always_carries_the_driver_smoke_test():
    kind, tests = ci_scope.selection(["app/agents/base/tools.py"])
    assert kind == "scoped" and ci_scope.SMOKE in tests


def test_a_package_brings_the_tests_that_name_its_formulas(tmp_path):
    pkg = tmp_path / "app" / "agents" / "hats" / "alpha"
    (pkg / "formulas").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "formulas" / "f.py").write_text(
        "@formula(owner='alpha', description='d', display_name='D', inputs={}, outputs={})\n"
        "def syn_alpha_widget(x):\n    return x\n")
    (tmp_path / "app" / "agents" / "base").mkdir(parents=True)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_names_it.py").write_text("run_calculation('syn_alpha_widget', {})\n")
    (tests / "test_unrelated.py").write_text("assert True\n")
    pkgs = ci_scope.packages(tmp_path)
    assert ci_scope.package_tests("alpha", pkgs, tmp_path) == ["tests/test_names_it.py"]
