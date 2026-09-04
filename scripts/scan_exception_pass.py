#!/usr/bin/env python3
"""Fail the build on ``except Exception: pass`` (body is only Pass).

Bare ``except: pass`` is ruff S110 (baseline 0). This twin is the typed
form S110 does not cover: ``except Exception: pass`` and
``except Exception as <name>: pass``.

Walks the same tree as ``scripts/audit_stubs.py`` (skips ``tests/`` and
vendor dirs). Exit 1 with a file:line list if any remain that are not in
ALLOWLIST with a named reason. Empty allowlist is the goal — prefer
logging or a narrower ``except`` over growing the list.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

# Same vendor / worktree dirs ``audit_stubs.py`` refuses to walk. ``tests/``
# is skipped separately (the same deviation audit_stubs documents): a
# planted fixture in the suite is how this scanner is tested, not a
# production swallow.
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    "generated",
    ".worktrees",
    ".claude",
    "dist",
    "build",
    ".pytest_cache",
}

# Keys are "relative/path.py:lineno". Value is the named reason this
# Exception+pass is allowed to stay. Same shape as KNOWN_INCOMPLETE.md
# entries (path + reason, visible, greppable). Empty is the goal.
#
# The SSE watchdog (app/routers/chat_watchdog.py) catches Exception so
# CancelledError still cancels, but its body logs and closes the turn —
# it is not a Pass, so it does not belong here.
ALLOWLIST: dict[str, str] = {}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _catches_exception(handler: ast.ExceptHandler) -> bool:
    """True only for ``except Exception`` (optionally ``as name``).

    Bare ``except:`` is ruff S110. Narrow types (OSError, …) are a
    deliberate cleanup. ``BaseException`` is a different, worse bug and
    is fenced by the SSE-watchdog AST test, not this twin.
    """
    t = handler.type
    if t is None:
        return False
    names = t.elts if isinstance(t, ast.Tuple) else [t]
    for n in names:
        if isinstance(n, ast.Name) and n.id == "Exception":
            return True
        if isinstance(n, ast.Attribute) and n.attr == "Exception":
            return True
    return False


def _body_is_only_pass(handler: ast.ExceptHandler) -> bool:
    body = [
        n
        for n in handler.body
        if not (
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        )
    ]
    return len(body) == 1 and isinstance(body[0], ast.Pass)


def exception_pass_lines(tree: ast.AST) -> list[int]:
    """Line numbers of ``except Exception: pass`` / ``as <name>: pass``."""
    out: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ExceptHandler)
            and _catches_exception(node)
            and _body_is_only_pass(node)
        ):
            out.append(node.lineno)
    return out


def iter_python_files(root: Path | None = None):
    """Yield (relpath, absolute path) for every scanned ``.py`` file.

    ``relpath`` uses forward slashes so Windows and POSIX report the same
    file:line keys (the same normalisation ``audit_stubs.py`` documents).
    """
    root = Path(root) if root is not None else repo_root()
    root = root.resolve()
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            abs_path = Path(dirpath) / name
            rel = abs_path.relative_to(root).as_posix()
            if rel.startswith("tests/") or "/tests/" in rel:
                continue
            yield rel, abs_path


def scan(root: Path | None = None) -> list[str]:
    """Return ``file:line`` findings not covered by a named ALLOWLIST entry."""
    findings: list[str] = []
    root = Path(root) if root is not None else repo_root()
    for rel, abs_path in iter_python_files(root):
        try:
            tree = ast.parse(abs_path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for lineno in exception_pass_lines(tree):
            key = f"{rel}:{lineno}"
            reason = ALLOWLIST.get(key)
            if reason:
                continue
            findings.append(key)
    return findings


def main() -> int:
    findings = scan()
    if findings:
        sys.stdout.write("SILENT except Exception: pass (log it or name a reason in ALLOWLIST):\n")
        for item in findings:
            sys.stdout.write(f"  {item}\n")
        sys.stdout.write(f"TOTAL: {len(findings)}\n")
        return 1
    sys.stdout.write("NO silent except Exception: pass handlers.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
