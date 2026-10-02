#!/usr/bin/env python3
"""Fail the build on NEW hardwiring in product code (NO HARDWIRING doctrine).

Owner doctrine, 2026-10-02 -- one rule everywhere, no exceptions:

    NO HARDWIRING. A failing case is never fixed by name. No per-case
    branches, no literal document strings, no test-case IDs, no rescue
    functions, no per-case switches. Find the general cause and fix the
    mechanism so every case of that kind passes. If you cannot find a
    general fix, say so and stop -- a pass patch is a failure, not progress.

This is the gate that gives that rule teeth. It rejects any diff that ADDS one
of the four hardwiring forms to product code:

  1. a ``_rescue_*`` function        -- a per-question retrieval patch
  2. a ``*_NEEDLES`` list            -- literal strings photographed from one corpus
  3. a ``RAG_*_RESCUE`` / ``*_BONUS`` / ``*_EXTRA_K`` env knob -- a per-case switch
  4. a probe / test-case ID          -- R18, M3, E1, UI-PHYS, SET5 ... in product code

Why it exists: on 2026-10-02 ``app/core/rag/retriever.py`` held 43 ``_rescue_*``
functions, 7 ``_NEEDLES`` lists, 28 per-case knobs, and probe IDs photographed
into production (``E1`` x64). Each was one failed probe turned into a special
case; none was a retrieval improvement. The loop "a case fails -> patch that case
by name -> it passes -> next case" produced them, and this scanner exists so the
44th cannot land.

The existing forms are GRANDFATHERED in ``scripts/hardwiring_baseline.json`` so
``main`` stays green today. The baseline may only SHRINK: a key that is not in
it is a new hardwiring and fails the build; a probe-ID count that grows fails
the build. The retriever surgery regenerates it with ``--write-baseline`` after
each batch of deletions -- never to admit a new entry.

Walks ``app/`` only (product code). ``tests/``, ``scripts/`` (owner tooling,
including this file), ``docs/`` and vendor dirs are out of scope. Same
``SKIP_DIRS`` and forward-slash keys as ``scan_exception_pass.py``.

Stdlib only. Exit 1 with a list of new forms.

Usage:
    python scripts/scan_hardwiring.py                 # CI check (exit 1 on new)
    python scripts/scan_hardwiring.py --write-baseline  # regenerate after cleanup
    python scripts/scan_hardwiring.py --list          # print current inventory
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "node_modules", "generated", ".worktrees",
    ".claude", "dist", "build", ".pytest_cache",
}

#: Product code only. Owner tooling (scripts/), the suite, and docs are not
#: where a rescue can masquerade as retrieval.
PRODUCT_ROOT = "app"

BASELINE_PATH = "scripts/hardwiring_baseline.json"

#: A per-case env switch. ``RAG_<CASE>_RESCUE``, ``RAG_<CASE>_BONUS``,
#: ``RAG_<CASE>_EXTRA_K`` -- one kill-switch per patched question.
KNOB_RE = re.compile(r"^RAG_[A-Z0-9_]+_(?:RESCUE|BONUS|EXTRA_K)$")

#: Probe / test-case IDs. A production file that names the probe it was
#: written to pass is a photograph of that probe. Explicit, not a loose regex:
#: ``R2`` (Cloudflare R2 storage) is deliberately absent.
#:
#: The G-series are NOT only SET6: an earlier "OLD-pack" / "F-BAT-D" probe pack
#: was G-numbered and is already photographed (retriever.py:3987 "Schedule-
#: register / Not Used rescue (live OLD-pack G1)"; 555 "F-BAT-D G3/G6"; 559
#: "OLD-pack G4"). ``A5``/``A9`` come from "wave-1 A3/A5/A9" (retriever.py:450).
#: ``A4`` is deliberately absent (paper size).
PROBE_IDS: tuple[str, ...] = (
    "R1", "R12", "R18", "R19", "N3",
    "T2", "T6", "T11", "T12", "T14", "T20",
    "X1", "X2", "M3", "D3", "P1a", "S2",
    "E1", "E3", "E6", "E12", "E15", "A3", "A5", "A9",
    *[f"G{i}" for i in range(1, 23)],
    "SET4", "SET4.1", "SET5", "SET6", "UI-PHYS", "UI_PHYS", "F-BAT-D",
)
_PROBE_RES = {pid: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(pid) + r"(?![A-Za-z0-9_])")
              for pid in PROBE_IDS}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def iter_product_files(root: Path | None = None):
    """Yield (relpath, abspath) for every ``.py`` under ``app/`` (posix keys)."""
    root = (Path(root) if root is not None else repo_root()).resolve()
    base = root / PRODUCT_ROOT
    if not base.is_dir():
        return
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            abs_path = Path(dirpath) / name
            rel = abs_path.relative_to(root).as_posix()
            if rel.startswith("tests/") or "/tests/" in rel:
                continue
            yield rel, abs_path


def _symbol_findings(rel: str, tree: ast.AST) -> list[str]:
    """Rescue functions, NEEDLES lists and per-case knobs, keyed by symbol."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "_rescue" in node.name:
                out.append(f"{rel}::rescue::{node.name}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.endswith("_NEEDLES"):
                    out.append(f"{rel}::needles::{t.id}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and KNOB_RE.match(node.value)
        ):
            out.append(f"{rel}::knob::{node.value}")
    return out


def _probe_counts(rel: str, text: str) -> dict[str, int]:
    """``rel::probe::ID -> occurrences`` for every probe ID present in the file."""
    out: dict[str, int] = {}
    for pid, rx in _PROBE_RES.items():
        n = len(rx.findall(text))
        if n:
            out[f"{rel}::probe::{pid}"] = n
    return out


def inventory(root: Path | None = None) -> tuple[dict[str, str], dict[str, int]]:
    """Current state: ``symbols`` (key -> kind) and ``probe_counts`` (key -> n)."""
    symbols: dict[str, str] = {}
    probes: dict[str, int] = {}
    for rel, abs_path in iter_product_files(root):
        text = abs_path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for key in _symbol_findings(rel, tree):
            symbols[key] = key.split("::", 2)[1]
        probes.update(_probe_counts(rel, text))
    return symbols, probes


def load_baseline(root: Path | None = None) -> tuple[dict[str, str], dict[str, int]]:
    root = (Path(root) if root is not None else repo_root()).resolve()
    p = root / BASELINE_PATH
    if not p.exists():
        return {}, {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return dict(data.get("symbols", {})), {k: int(v) for k, v in data.get("probe_counts", {}).items()}


def write_baseline(root: Path | None = None) -> Path:
    root = (Path(root) if root is not None else repo_root()).resolve()
    symbols, probes = inventory(root)
    payload = {
        "_doctrine": (
            "NO HARDWIRING (owner, 2026-10-02). GRANDFATHERED pre-doctrine forms. "
            "This file may only shrink. Regenerate with "
            "`python scripts/scan_hardwiring.py --write-baseline` after deleting "
            "forms -- never to admit a new one."
        ),
        "symbols": {k: "baseline 2026-10-02 (pre-doctrine)" for k in sorted(symbols)},
        "probe_counts": {k: probes[k] for k in sorted(probes)},
    }
    p = root / BASELINE_PATH
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return p


def scan(root: Path | None = None) -> list[str]:
    """Findings not covered by the baseline: new symbols, or grown probe counts."""
    base_syms, base_probes = load_baseline(root)
    symbols, probes = inventory(root)
    findings: list[str] = []
    for key in sorted(symbols):
        if key not in base_syms:
            findings.append(f"NEW {key}")
    for key in sorted(probes):
        n = probes[key]
        if key not in base_probes:
            findings.append(f"NEW {key} (x{n})")
        elif n > base_probes[key]:
            findings.append(f"GREW {key} ({base_probes[key]} -> {n})")
    return findings


def main() -> int:
    if "--write-baseline" in sys.argv:
        p = write_baseline()
        symbols, probes = inventory()
        sys.stdout.write(
            f"wrote {p.as_posix()}: {len(symbols)} symbols, "
            f"{len(probes)} probe-id keys ({sum(probes.values())} occurrences)\n"
        )
        return 0
    if "--list" in sys.argv:
        symbols, probes = inventory()
        for k in sorted(symbols):
            sys.stdout.write(f"{k}\n")
        for k in sorted(probes):
            sys.stdout.write(f"{k} x{probes[k]}\n")
        return 0

    findings = scan()
    if findings:
        sys.stdout.write(
            "HARDWIRING: a failing case is never fixed by name. Find the general\n"
            "cause and fix the mechanism; if there is no general fix, say so and\n"
            "stop. Do NOT add to the baseline to pass this check.\n"
        )
        for item in findings:
            sys.stdout.write(f"  {item}\n")
        sys.stdout.write(f"TOTAL: {len(findings)}\n")
        return 1
    base_syms, base_probes = load_baseline()
    sys.stdout.write(
        f"HARDWIRING: 0 new ({len(base_syms)} symbols + {len(base_probes)} "
        f"probe-id keys grandfathered; baseline may only shrink).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
