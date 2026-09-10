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


# The RETURN twin's baseline. Keys are "relative/path.py:lineno"; the value is
# why the site may stay. Every entry has the same reason -- it was here before
# the scanner was -- and the count may only fall.
#
# The twin exists to stop the 125th, not to pretend these 124 are fine. Each
# closes one of two ways: log what failed and keep returning empty (the caller
# genuinely tolerates nothing), or raise a typed outcome (a decision path,
# where "nothing there" and "it broke" must not be the same answer).
#
# Regenerate after a cleanup:  python scripts/scan_exception_pass.py --list-returns
RETURN_ALLOWLIST: dict[str, str] = {
    "app/agents/formulas.py:21": "baseline 2026-09-10",
    "app/agents/formulas.py:30": "baseline 2026-09-10",
    "app/agents/formulas.py:48": "baseline 2026-09-10",
    "app/agents/runtime.py:11648": "baseline 2026-09-10",
    "app/agents/runtime.py:11671": "baseline 2026-09-10",
    "app/agents/runtime.py:1867": "baseline 2026-09-10",
    "app/agents/runtime.py:1873": "baseline 2026-09-10",
    "app/agents/runtime.py:1936": "baseline 2026-09-10",
    "app/agents/runtime.py:1942": "baseline 2026-09-10",
    "app/agents/runtime.py:2512": "baseline 2026-09-10",
    "app/agents/runtime.py:3461": "baseline 2026-09-10",
    "app/agents/runtime.py:4572": "baseline 2026-09-10",
    "app/agents/runtime.py:4953": "baseline 2026-09-10",
    "app/agents/runtime.py:5782": "baseline 2026-09-10",
    "app/agents/runtime.py:582": "baseline 2026-09-10",
    "app/agents/runtime.py:6601": "baseline 2026-09-10",
    "app/agents/runtime.py:7461": "baseline 2026-09-10",
    "app/agents/runtime.py:813": "baseline 2026-09-10",
    "app/blocks/bim_extractor.py:405": "baseline 2026-09-10",
    "app/blocks/bim_extractor.py:419": "baseline 2026-09-10",
    "app/blocks/bim_extractor.py:603": "baseline 2026-09-10",
    "app/blocks/boq_processor.py:562": "baseline 2026-09-10",
    "app/blocks/boq_processor.py:570": "baseline 2026-09-10",
    "app/blocks/image.py:100": "baseline 2026-09-10",
    "app/blocks/image.py:114": "baseline 2026-09-10",
    "app/blocks/image.py:189": "baseline 2026-09-10",
    "app/blocks/ocr.py:310": "baseline 2026-09-10",
    "app/blocks/ocr.py:312": "baseline 2026-09-10",
    "app/blocks/ocr.py:327": "baseline 2026-09-10",
    "app/blocks/primavera_parser.py:344": "baseline 2026-09-10",
    "app/blocks/primavera_parser.py:359": "baseline 2026-09-10",
    "app/blocks/safety_world_detector.py:64": "baseline 2026-09-10",
    "app/blocks/smart_orchestrator.py:567": "baseline 2026-09-10",
    "app/blocks/smart_orchestrator.py:588": "baseline 2026-09-10",
    "app/blocks/validation_pipeline.py:159": "baseline 2026-09-10",
    "app/blocks/voice.py:43": "baseline 2026-09-10",
    "app/containers/construction/__init__.py:1388": "baseline 2026-09-10",
    "app/containers/construction/__init__.py:145": "baseline 2026-09-10",
    "app/containers/construction/__init__.py:1485": "baseline 2026-09-10",
    "app/containers/construction/__init__.py:1618": "baseline 2026-09-10",
    "app/containers/construction/__init__.py:407": "baseline 2026-09-10",
    "app/containers/construction/__init__.py:414": "baseline 2026-09-10",
    "app/containers/construction/__init__.py:555": "baseline 2026-09-10",
    "app/containers/construction/boq.py:780": "baseline 2026-09-10",
    "app/containers/construction/helpers.py:69": "baseline 2026-09-10",
    "app/core/boq_validation.py:78": "baseline 2026-09-10",
    "app/core/cache_wrapper.py:15": "baseline 2026-09-10",
    "app/core/cache_wrapper.py:26": "baseline 2026-09-10",
    "app/core/cache_wrapper.py:36": "baseline 2026-09-10",
    "app/core/construction_knowledge.py:277": "baseline 2026-09-10",
    "app/core/doc_index.py:1025": "baseline 2026-09-10",
    "app/core/doc_index.py:1068": "baseline 2026-09-10",
    "app/core/doc_index.py:1076": "baseline 2026-09-10",
    "app/core/doc_index.py:1174": "baseline 2026-09-10",
    "app/core/doc_index.py:196": "baseline 2026-09-10",
    "app/core/doc_index.py:2628": "baseline 2026-09-10",
    "app/core/doc_index.py:304": "baseline 2026-09-10",
    "app/core/doc_index.py:3149": "baseline 2026-09-10",
    "app/core/doc_index.py:341": "baseline 2026-09-10",
    "app/core/doc_index.py:357": "baseline 2026-09-10",
    "app/core/doc_index.py:448": "baseline 2026-09-10",
    "app/core/doc_index.py:457": "baseline 2026-09-10",
    "app/core/doc_index.py:860": "baseline 2026-09-10",
    "app/core/doc_index.py:900": "baseline 2026-09-10",
    "app/core/doc_index.py:914": "baseline 2026-09-10",
    "app/core/doc_index.py:936": "baseline 2026-09-10",
    "app/core/doc_types.py:53": "baseline 2026-09-10",
    "app/core/email.py:89": "baseline 2026-09-10",
    "app/core/extract_isolated.py:118": "baseline 2026-09-10",
    "app/core/extract_isolated.py:99": "baseline 2026-09-10",
    "app/core/file_crypto.py:296": "baseline 2026-09-10",
    "app/core/file_crypto.py:90": "baseline 2026-09-10",
    "app/core/ingest_lifecycle.py:109": "baseline 2026-09-10",
    "app/core/ingest_lifecycle.py:147": "baseline 2026-09-10",
    "app/core/ingest_lifecycle.py:164": "baseline 2026-09-10",
    "app/core/ingest_lifecycle.py:568": "baseline 2026-09-10",
    "app/core/ingest_lifecycle.py:95": "baseline 2026-09-10",
    "app/core/learning/hydration.py:772": "baseline 2026-09-10",
    "app/core/learning/local_model.py:131": "baseline 2026-09-10",
    "app/core/llm_client.py:144": "baseline 2026-09-10",
    "app/core/predefined_reasoning.py:529": "baseline 2026-09-10",
    "app/core/predefined_reasoning.py:642": "baseline 2026-09-10",
    "app/core/projects.py:1082": "baseline 2026-09-10",
    "app/core/projects.py:156": "baseline 2026-09-10",
    "app/core/projects.py:164": "baseline 2026-09-10",
    "app/core/projects.py:757": "baseline 2026-09-10",
    "app/core/rag/coverage_honesty.py:86": "baseline 2026-09-10",
    "app/core/rag/embeddings.py:324": "baseline 2026-09-10",
    "app/core/rag/embeddings.py:332": "baseline 2026-09-10",
    "app/core/rag/retriever.py:2887": "baseline 2026-09-10",
    "app/core/rag/retriever.py:2914": "baseline 2026-09-10",
    "app/core/rag/retriever.py:2960": "baseline 2026-09-10",
    "app/core/rag/retriever.py:2980": "baseline 2026-09-10",
    "app/core/rag/retriever.py:3157": "baseline 2026-09-10",
    "app/core/rag/retriever.py:3193": "baseline 2026-09-10",
    "app/core/rag/retriever.py:3204": "baseline 2026-09-10",
    "app/core/rag/retriever.py:3276": "baseline 2026-09-10",
    "app/core/rag/retriever.py:4918": "baseline 2026-09-10",
    "app/core/rag/retriever.py:839": "baseline 2026-09-10",
    "app/core/rag/vector_store.py:1755": "baseline 2026-09-10",
    "app/core/sandbox.py:302": "baseline 2026-09-10",
    "app/core/url_guard.py:20": "baseline 2026-09-10",
    "app/core/usage_tracker.py:78": "baseline 2026-09-10",
    "app/infra/monitoring.py:326": "baseline 2026-09-10",
    "app/lib/boq_excel.py:188": "baseline 2026-09-10",
    "app/lib/boq_pricing.py:128": "baseline 2026-09-10",
    "app/lib/pm_computations.py:340": "baseline 2026-09-10",
    "app/lib/wbs_duration_overrides.py:147": "baseline 2026-09-10",
    "app/main.py:471": "baseline 2026-09-10",
    "app/routers/chat.py:364": "baseline 2026-09-10",
    "app/routers/chat_watchdog.py:97": "baseline 2026-09-10",
    "app/routers/mcp.py:61": "baseline 2026-09-10",
    "app/worker/ingest_queue.py:33": "baseline 2026-09-10",
    "scripts/build_rate_card.py:79": "baseline 2026-09-10",
    "scripts/extract_alostool.py:113": "baseline 2026-09-10",
    "scripts/extract_boq_rw.py:96": "baseline 2026-09-10",
    "scripts/extract_boq_section.py:167": "baseline 2026-09-10",
    "scripts/extract_wetransfer_boqs.py:39": "baseline 2026-09-10",
    "scripts/generate_scenarios_drive_archive.py:230": "baseline 2026-09-10",
    "scripts/generate_scenarios_drive_archive_v2.py:299": "baseline 2026-09-10",
    "scripts/p1b_ingest_drive_server.py:1348": "baseline 2026-09-10",
    "scripts/price_boq.py:54": "baseline 2026-09-10",
    "scripts/rag_backfill_audit.py:90": "baseline 2026-09-10",
    "scripts/security_scan.py:51": "baseline 2026-09-10",
}


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


#: The empty values a handler can hand back instead of an outcome.
_EMPTY_CONSTANTS = (None, "", 0, False)


def _returns_only_an_empty_value(handler: ast.ExceptHandler) -> bool:
    """True when the handler's only statement returns None / {} / [] / '' / 0."""
    body = [
        n
        for n in handler.body
        if not (
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        )
    ]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    value = body[0].value
    if value is None:  # bare `return`
        return True
    if isinstance(value, ast.Constant) and value.value in _EMPTY_CONSTANTS:
        return True
    if isinstance(value, ast.Dict) and not value.keys:
        return True
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)) and not value.elts:
        return True
    return False


def exception_return_lines(tree: ast.AST) -> list[int]:
    """Line numbers of ``except <anything>:`` whose body is only an empty return.

    ANY exception type counts here, unlike the pass-twin which is scoped to
    ``Exception``: swallowing ``OSError`` into ``return None`` is the same
    invisible degradation as swallowing ``Exception``, and the caller cannot
    tell "nothing there" from "the lookup failed" in either case.

    One statement only. A handler that logs and then returns empty has made
    the degradation visible, which is the whole ask -- it is not flagged.
    """
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _returns_only_an_empty_value(node)
    )


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


def scan_returns(root: Path | None = None) -> list[str]:
    """``file:line`` for every empty-return handler not in RETURN_ALLOWLIST."""
    findings: list[str] = []
    root = Path(root) if root is not None else repo_root()
    for rel, abs_path in iter_python_files(root):
        try:
            tree = ast.parse(abs_path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for lineno in exception_return_lines(tree):
            key = f"{rel}:{lineno}"
            if RETURN_ALLOWLIST.get(key):
                continue
            findings.append(key)
    return findings


def all_return_sites(root: Path | None = None) -> list[str]:
    """Every empty-return handler, allowlisted or not. Backs --list-returns."""
    out: list[str] = []
    root = Path(root) if root is not None else repo_root()
    for rel, abs_path in iter_python_files(root):
        try:
            tree = ast.parse(abs_path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        out += [f"{rel}:{n}" for n in exception_return_lines(tree)]
    return sorted(out)


def main() -> int:
    if "--list-returns" in sys.argv:
        # Paste-ready RETURN_ALLOWLIST body, for regenerating after a cleanup.
        for key in all_return_sites():
            sys.stdout.write('    "%s": "baseline 2026-09-10",\n' % key)
        return 0

    rc = 0
    findings = scan()
    if findings:
        sys.stdout.write(
            "SILENT except Exception: pass (log it or name a reason in "
            "ALLOWLIST):\n"
        )
        for item in findings:
            sys.stdout.write(f"  {item}\n")
        sys.stdout.write(f"TOTAL: {len(findings)}\n")
        rc = 1
    else:
        sys.stdout.write("NO silent except Exception: pass handlers.\n")

    returns = scan_returns()
    if returns:
        sys.stdout.write(
            "SILENT except -> empty return. Log what failed, or raise a typed "
            "outcome; do not add to RETURN_ALLOWLIST:\n"
        )
        for item in returns:
            sys.stdout.write(f"  {item}\n")
        sys.stdout.write(f"TOTAL: {len(returns)}\n")
        rc = 1
    else:
        sys.stdout.write(
            f"RETURN: 0 new ({len(RETURN_ALLOWLIST)} baselined).\n"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
