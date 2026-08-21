"""Clash stays off unless the user actually asks for it.

A leftover project-assistant turn that said \"Do not run clash detection\"
was routed to ``bim_clash_detection`` because the matcher only looked for
the word ``clash``. Negated phrasing is not a request.
"""
from __future__ import annotations

import re

_CLASH_WORD = re.compile(r"\bclash", re.IGNORECASE)
_CLASH_NEGATED = re.compile(
    r"\b(?:do\s+not|don't|dont|never|without|no)\b[\s\w,/:'-]{0,48}\bclash",
    re.IGNORECASE,
)


def message_wants_clash(text: str) -> bool:
    """True only when the message asks to run clash detection."""
    raw = text or ""
    if not _CLASH_WORD.search(raw):
        return False
    if _CLASH_NEGATED.search(raw):
        return False
    return True
