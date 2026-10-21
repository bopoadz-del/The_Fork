#!/usr/bin/env python3
"""Registry schema gaps: list them, or fail on gaps a change introduces.

    python scripts/registry_schema_gaps.py                 # what is still missing
    python scripts/registry_schema_gaps.py --compare BASE  # fail on gaps HEAD has and BASE does not

CI runs --compare against the PR's base commit (and, on main, against the
commit before the push). A change may leave existing gaps for their lane to
fill, but may not add one: a new or changed registry entry must be complete.
No gap list is committed, so no lane regenerates a shared file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Set

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_PROBE = ("import json; from app.lib.formula_registry import schema_gaps; "
          "print('GAPS=' + json.dumps(schema_gaps()))")


def new_gaps(base: Iterable[str], head: Iterable[str]) -> List[str]:
    """Gaps the head has and the base does not."""
    return sorted(set(head) - set(base))


def gaps_at(commit: str) -> Set[str]:
    """The gap set of ``commit``, computed in a temporary worktree."""
    env = {**os.environ, "RAG_EMBEDDING_MODEL": "fake", "PYTHONPATH": ""}
    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "base")
        subprocess.run(["git", "worktree", "add", "--detach", tree, commit], cwd=ROOT, check=True,
                       capture_output=True)
        try:
            out = subprocess.run([sys.executable, "-c", _PROBE], cwd=tree, env=env,
                                 capture_output=True, text=True)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", tree], cwd=ROOT, capture_output=True)
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("GAPS=")), None)
    if line is None:
        # The base predates the schema check: nothing to compare against.
        print(f"base {commit[:12]} has no schema_gaps(); every head gap is reported but none fails")
        return set()
    return set(json.loads(line[len("GAPS="):]))


def main(argv: List[str]) -> int:
    from app.lib.formula_registry import schema_gaps

    head = set(schema_gaps())
    if "--compare" not in argv:
        print(f"gaps {len(head)}")
        for g in sorted(head):
            print("  ", g)
        return 0
    base_commit = argv[argv.index("--compare") + 1]
    base = gaps_at(base_commit)
    added = new_gaps(base, head) if base else []
    print(f"gaps: base {len(base)}, head {len(head)}; filled {len(base - head)}; new {len(added)}")
    for g in added:
        print("  NEW", g)
    return 1 if added else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
