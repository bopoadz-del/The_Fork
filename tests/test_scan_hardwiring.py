"""scan_hardwiring: the NO HARDWIRING gate has teeth, and its baseline is honest.

Two things, mirroring how scan_exception_pass is tested:
  * a planted fixture in a temp tree is FLAGGED -- all four forms;
  * the real baseline is a ceiling that may only shrink and refuses stale keys,
    so the gate can never be "passed" by growing the baseline.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "scan_hardwiring", ROOT / "scripts" / "scan_hardwiring.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_planted_hardwiring_is_flagged(tmp_path: Path):
    """Each of the four forms, planted in product code with no baseline, is NEW."""
    sh = _load()
    pkg = tmp_path / "app" / "core"
    pkg.mkdir(parents=True)
    (pkg / "retr.py").write_text(
        "import os\n"
        "_LUX_NEEDLES = ['concrete placement lux']\n"
        "FLAG = os.getenv('RAG_LUX_TABLE_RESCUE')\n"
        "def _rescue_lux_table(chunks):\n"
        "    # tuned for R18\n"
        "    return chunks\n",
        encoding="utf-8",
    )
    findings = sh.scan(tmp_path)
    joined = "\n".join(findings)
    assert "::rescue::_rescue_lux_table" in joined
    assert "::needles::_LUX_NEEDLES" in joined
    assert "::knob::RAG_LUX_TABLE_RESCUE" in joined
    assert "::probe::R18" in joined
    assert all(f.startswith("NEW ") for f in findings)


def test_clean_product_code_passes(tmp_path: Path):
    """A mechanism fix with no per-case form produces no findings."""
    sh = _load()
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "ok.py").write_text(
        "def rank_table_chunks(chunks, k):\n"
        "    return sorted(chunks, key=lambda c: c.score)[:k]\n",
        encoding="utf-8",
    )
    assert sh.scan(tmp_path) == []


def test_probe_count_growth_is_flagged(tmp_path: Path):
    """A probe ID already baselined may not gain occurrences."""
    sh = _load()
    pkg = tmp_path / "app"
    pkg.mkdir()
    (tmp_path / "scripts").mkdir()
    (pkg / "m.py").write_text("# E1\n# E1\n", encoding="utf-8")
    sh.write_baseline(tmp_path)  # baseline: E1 x2
    (pkg / "m.py").write_text("# E1\n# E1\n# E1\n", encoding="utf-8")
    findings = sh.scan(tmp_path)
    assert any(f.startswith("GREW ") and "::probe::E1" in f for f in findings)


def test_line_allowlist_skips_only_the_named_fragment(tmp_path: Path):
    """An allowlisted (file, ID, fragment) line is skipped; the same ID on any
    other line of that file, or in another file, still counts."""
    sh = _load()
    pkg = tmp_path / "app" / "lib"
    pkg.mkdir(parents=True)
    (pkg / "pm_excel.py").write_text(
        'ws.add_chart(chart, "G3")\n'
        "# tuned for G3\n",
        encoding="utf-8",
    )
    (pkg / "other.py").write_text('ws.add_chart(chart, "G3")\n', encoding="utf-8")
    _syms, probes = sh.inventory(tmp_path)
    assert probes.get("app/lib/pm_excel.py::probe::G3") == 1
    assert probes.get("app/lib/other.py::probe::G3") == 1


def test_real_baseline_is_a_ceiling_with_no_stale_keys():
    """The committed baseline matches reality: nothing new, nothing grown, and
    every baselined key still exists (a deleted form must be removed by
    regenerating the baseline, never left behind as a stale allowance)."""
    sh = _load()
    base_syms, base_probes = sh.load_baseline()
    assert base_syms or base_probes, "baseline missing: run --write-baseline"
    # Ceiling: the gate itself is green against main.
    assert sh.scan() == []
    # No stale keys: every grandfathered form is still present.
    symbols, probes = sh.inventory()
    stale_syms = sorted(k for k in base_syms if k not in symbols)
    stale_probes = sorted(k for k in base_probes if k not in probes)
    assert not stale_syms, f"stale baseline symbols (regenerate): {stale_syms}"
    assert not stale_probes, f"stale baseline probe keys (regenerate): {stale_probes}"
