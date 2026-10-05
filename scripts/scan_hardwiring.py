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
    python scripts/scan_hardwiring.py --strict        # ignore the baseline: any form fails
    python scripts/scan_hardwiring.py --cases <battery.py|.json> --live-names --strict
        # + battery question text, case ids, expected figures, live document
        #   names / reference codes and project ids (DATABASE_URL, read-only),
        #   all read at run time and never baselined
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

#: Lines whose probe-ID match is NOT a probe ID. Keyed by (file, probe ID,
#: a fragment of the matching line), never by line number, so the entry
#: follows the line when code above it moves. Narrow on purpose: only a
#: line in that file that contains that exact fragment is skipped, and only
#: for that probe ID -- the same ID elsewhere on another line still counts.
PROBE_LINE_ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("app/blocks/bim.py", "A3", "(A0|A1|A2|A3|A4)"):
        "ISO 216 drawing sheet sizes (A0-A4), not a probe ID",
    ("app/containers/construction/__init__.py", "R1", "R1+R2 < design"):
        "BS 7671 continuity test R1+R2 (conductor resistances), not a probe ID",
    ("app/lib/pm_excel.py", "G3", '"G3")'):
        "Excel cell G3 as a chart anchor, not a probe ID",
    ("app/lib/pm_excel.py", "G4", "=SUM(G4:G{"):
        "Excel cell G4 in a SUM range formula, not a probe ID",
}


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


def _allowlisted_fragments(rel: str, pid: str) -> list[str]:
    return [frag for (f, p, frag) in PROBE_LINE_ALLOWLIST if f == rel and p == pid]


def _probe_counts(rel: str, text: str) -> dict[str, int]:
    """``rel::probe::ID -> occurrences`` for every probe ID present in the file.

    A line covered by ``PROBE_LINE_ALLOWLIST`` for that ID is not counted.
    """
    out: dict[str, int] = {}
    for pid, rx in _PROBE_RES.items():
        frags = _allowlisted_fragments(rel, pid)
        if frags:
            n = sum(
                len(rx.findall(line))
                for line in text.splitlines()
                if not any(frag in line for frag in frags)
            )
        else:
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


# ── run-time inputs: the battery, the live corpus ─────────────────────────────
#
# The forms above are structural. Hardwiring also hides as plain data: a
# battery question pasted into a prompt, an expected figure as a constant, a
# live document's name or reference number, a project id. Those are not known
# to the repo, so they are read at RUN TIME: ``--cases FILE`` (the owner's
# battery: a ``CASES = {id: (questions, check, expected)}`` .py, or JSON
# ``{"cases": [{"id", "questions", "expected"}]}``) and ``--live-names``
# (document names + project ids read from DATABASE_URL, read-only). Findings
# from these inputs are never baselined: any one fails.

#: Where product text lives: code, agent prompts, and config.
TEXT_ROOTS = ("app", "config")
TEXT_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".json", ".txt")
_WORD = re.compile(r"[a-z0-9]+")
_REF_CODE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)+(?![A-Za-z0-9])")
#: A figure distinctive enough to identify one expected answer: at least five
#: significant digits (263,175.67; 80892), not a round quantity like 2500.
_BIG_NUMBER = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+(?:\.\d+)?")


def iter_product_text(root: Path | None = None):
    """Yield (relpath, text) for product code, agent prompts and config."""
    root = (Path(root) if root is not None else repo_root()).resolve()
    for top in TEXT_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if not name.endswith(TEXT_SUFFIXES):
                    continue
                p = Path(dirpath) / name
                rel = p.relative_to(root).as_posix()
                if "/tests/" in f"/{rel}" or "/knowledge/" in f"/{rel}":
                    continue  # knowledge content is data the RAG serves, not code
                yield rel, p.read_text(encoding="utf-8", errors="ignore")


def load_cases(path: str) -> dict[str, dict[str, list[str]]]:
    """``{case_id: {"questions": [...], "expected": [...]}}`` from the battery file."""
    src = Path(path).read_text(encoding="utf-8")
    if path.endswith(".json"):
        return {c["id"]: {"questions": list(c.get("questions", [])), "expected": [str(c.get("expected", ""))]}
                for c in json.loads(src)["cases"]}
    out: dict[str, dict[str, list[str]]] = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CASES" for t in node.targets) \
                and isinstance(node.value, ast.Dict):
            for k, v in zip(node.value.keys, node.value.values):
                if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                    continue
                strings = [n.value for n in ast.walk(v) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                questions = [s for s in strings if len(s.split()) >= 6]
                expected = [s for s in strings if s not in questions]
                out[k.value] = {"questions": questions, "expected": expected}
    return out


def load_live_names() -> dict[str, list[str]]:
    """Document names and project ids from the live database (read-only)."""
    import sqlalchemy as sa

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("--live-names needs DATABASE_URL")
    url = url.replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
    with sa.create_engine(url).connect() as c:
        c.execute(sa.text("SET TRANSACTION READ ONLY"))
        names = [r[0] for r in c.execute(sa.text("SELECT DISTINCT original_name FROM documents")) if r[0]]
        projects = [r[0] for r in c.execute(sa.text("SELECT id FROM projects")) if r[0]]
        c.rollback()
    return {"documents": names, "projects": projects}


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _plain_number(s: str) -> str:
    s = s.replace(",", "")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _distinctive(fig: str) -> bool:
    return len(fig.replace(".", "").lstrip("0").rstrip("0")) >= 5


def leakage_findings(cases: dict | None = None, live: dict | None = None,
                     root: Path | None = None) -> list[str]:
    """Battery text, case ids, expected figures, live names and project ids in product text."""
    files = list(iter_product_text(root))
    findings: list[str] = []
    if cases:
        ids = {cid: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(cid) + r"(?![A-Za-z0-9_])") for cid in cases}
        q_grams = {g: cid for cid, c in cases.items() for q in c["questions"] for g in _ngrams(_words(q), 8)}
        figures = {_plain_number(m): cid for cid, c in cases.items()
                   for e in c["expected"] for m in _BIG_NUMBER.findall(e)
                   if _distinctive(_plain_number(m))}
        for rel, text in files:
            for cid, rx in ids.items():
                if rx.search(text):
                    findings.append(f"CASE-ID {rel}: {cid}")
            hit = {q_grams[g] for g in _ngrams(_words(text), 8) if g in q_grams}
            for cid in sorted(hit):
                findings.append(f"QUESTION-TEXT {rel}: battery question of {cid}")
            nums = {_plain_number(m) for m in _BIG_NUMBER.findall(text)}
            for fig in sorted(nums & set(figures)):
                findings.append(f"EXPECTED-FIGURE {rel}: {fig} ({figures[fig]})")
    if live:
        doc_seqs = {}
        codes = set()
        for name in live.get("documents", []):
            stem = os.path.splitext(name)[0]
            w = _words(stem)
            if len(w) >= 3 and len(" ".join(w)) >= 15:
                doc_seqs[" ".join(w)] = name
            codes.update(_REF_CODE.findall(stem))
        projects = {p: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(p) + r"(?![A-Za-z0-9_])")
                    for p in live.get("projects", []) if len(p) >= 6}
        code_rx = {c: re.compile(r"(?<![A-Za-z0-9])" + re.escape(c) + r"(?![A-Za-z0-9])") for c in codes}
        for rel, text in files:
            norm = " " + " ".join(_words(text)) + " "
            for seq, name in doc_seqs.items():
                if f" {seq} " in norm:
                    findings.append(f"DOCUMENT-NAME {rel}: {name}")
            for c, rx in code_rx.items():
                if rx.search(text):
                    findings.append(f"DOCUMENT-REF {rel}: {c}")
            for p, rx in projects.items():
                if rx.search(text):
                    findings.append(f"PROJECT-ID {rel}: {p}")
    return sorted(set(findings))


def _arg(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    return None


def main() -> int:
    cases_path = _arg("--cases")
    if cases_path or "--live-names" in sys.argv:
        found = leakage_findings(
            cases=load_cases(cases_path) if cases_path else None,
            live=load_live_names() if "--live-names" in sys.argv else None,
        )
        for item in found:
            sys.stdout.write(f"  {item}\n")
        sys.stdout.write(f"LEAKAGE: {len(found)} finding(s) from run-time inputs\n")
        if found:
            return 1
        if "--strict" not in sys.argv:
            return 0
    if "--strict" in sys.argv:
        symbols, probes = inventory()
        for k in sorted(symbols):
            sys.stdout.write(f"  {k}\n")
        for k in sorted(probes):
            sys.stdout.write(f"  {k} x{probes[k]}\n")
        sys.stdout.write(f"STRICT: {len(symbols)} symbols + {len(probes)} probe-id keys (baseline ignored)\n")
        return 1 if (symbols or probes) else 0
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
