"""The silent-return twin: an except whose whole body is an empty value.

``scan_exception_pass`` already fails the build on ``except Exception: pass``.
The larger and quieter version is the one that returns instead:

    except Exception:
        return None

The caller then cannot tell "there is nothing there" from "the lookup
failed" — same outage, two meanings, one answer. Measured on this tree the
day the scanner was written: **124 sites**, 58 of them under ``app/core``.

The twin does not pretend those are fine. It baselines them by ``file:line``
and fails on the 125th, so the count can only fall.

Any exception TYPE counts, unlike the pass-twin which is scoped to
``Exception``: swallowing ``OSError`` into ``return None`` is the same
degradation. One statement only — a handler that logs first has made the
degradation visible, which is the whole ask, and is not flagged.
"""
from __future__ import annotations

import ast

from scripts.scan_exception_pass import (
    RETURN_ALLOWLIST,
    all_return_sites,
    exception_return_lines,
    scan_returns,
)


def _lines(src: str) -> list[int]:
    return exception_return_lines(ast.parse(src))


def test_it_flags_every_empty_shape():
    src = (
        "def a():\n    try:\n        x()\n    except Exception:\n        return None\n"
        "def b():\n    try:\n        x()\n    except ValueError:\n        return {}\n"
        "def c():\n    try:\n        x()\n    except (OSError, KeyError) as e:\n        return ''\n"
        "def d():\n    try:\n        x()\n    except Exception:\n        return []\n"
        "def e():\n    try:\n        x()\n    except Exception:\n        return\n"
        "def f():\n    try:\n        x()\n    except Exception:\n        return 0\n"
    )
    assert _lines(src) == [4, 9, 14, 19, 24, 29]


def test_a_narrow_exception_type_counts_too():
    """OSError swallowed into None is the same lie as Exception swallowed."""
    src = "def a():\n    try:\n        x()\n    except OSError:\n        return None\n"
    assert _lines(src) == [4]


def test_a_logged_handler_is_not_flagged():
    """Visibly degraded is the ask. Logging first satisfies it."""
    src = (
        "def a():\n    try:\n        x()\n    except Exception:\n"
        "        log.warning('x failed', exc_info=True)\n        return None\n"
    )
    assert _lines(src) == []


def test_a_typed_outcome_is_not_flagged():
    src = (
        "def a():\n    try:\n        x()\n    except Exception as exc:\n"
        "        return {'ok': False, 'error': str(exc)}\n"
    )
    assert _lines(src) == []


def test_a_reraise_is_not_flagged():
    src = "def a():\n    try:\n        x()\n    except Exception:\n        raise\n"
    assert _lines(src) == []


def test_a_docstring_does_not_hide_the_return():
    """A handler cannot escape by carrying a docstring above the return."""
    src = (
        "def a():\n    try:\n        x()\n    except Exception:\n"
        '        """why this is fine"""\n        return None\n'
    )
    assert _lines(src) == [4]


def test_the_repo_has_no_new_sites():
    """The gate itself. A new empty-return handler fails the build."""
    assert scan_returns() == [], (
        "new silent empty-return handler(s); log what failed or raise a typed "
        "outcome — do not add them to RETURN_ALLOWLIST"
    )


def test_the_baseline_is_only_ever_smaller():
    """Every allowlisted key must still be a real site.

    A stale key is how a baseline quietly grows room: the line moves, the
    entry stops matching anything, and the next real site at that line slips
    through under an old reason.
    """
    live = set(all_return_sites())
    stale = sorted(set(RETURN_ALLOWLIST) - live)
    assert not stale, (
        "RETURN_ALLOWLIST entries that no longer point at a site — regenerate "
        "with `python scripts/scan_exception_pass.py --list-returns`: "
        + ", ".join(stale)
    )


def test_the_baseline_is_not_a_place_to_add_things():
    """A ceiling, stated. Raise it only by deleting this assertion on purpose."""
    assert len(RETURN_ALLOWLIST) <= 64, (
        f"the baseline grew to {len(RETURN_ALLOWLIST)}; it was 124 on "
        "2026-09-10 and is meant to shrink"
    )
