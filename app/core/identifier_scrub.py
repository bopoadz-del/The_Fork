"""Deterministic project/company identifier scrub for RAG answers.

Pilot confidentiality stopgap: the general-knowledge corpus (drive_archive)
still holds a client's project documents, whose technical content is useful but
whose *names* must not leak into answers (one client's project identity showing
up in another context). Until deployment-grade per-tenant isolation lands, this
scrubs known project / client / third-party names out of the FINAL answer text
and replaces them with generic placeholders ("the project" / "the client").

Deterministic denylist, not NER — it reliably catches the known identifiers in
this corpus. Add more via RAG_SCRUB_EXTRA_TERMS (comma-separated; each mapped to
"the project"). Disable with RAG_SCRUB_IDENTIFIERS=0.

Order matters: longer/multiword phrases are matched first so "DG2 Infra Pack 1"
becomes "the project", never "the project Infra Pack 1".
"""
from __future__ import annotations

import os
import re
from typing import List, Pattern, Tuple

# (pattern, replacement) — longest/most-specific first.
_DEFAULT_RULES: List[Tuple[str, str]] = [
    (r"\bDG2\s+Infra\s+Pack\s*1\b", "the project"),
    (r"\bInfra\s+Pack\s*1\b", "the project"),
    (r"\bDiriyah(?:\s+Gate)?\b", "the project"),
    (r"\bDG\s?II\b", "the project"),
    (r"\bDG2\b", "the project"),
    (r"\bDG1\b", "the project"),
    (r"\bInfra1\b", "the project"),
    (r"\bLAAR\b", "the project"),
    (r"\bDar\s+Al\s+Arkan\b", "the client"),
    (r"\bDubai\s+Parks(?:\s+and\s+Resorts)?\b", "a third party"),
    (r"\bDPR\b", "the contractor"),
]


def _enabled() -> bool:
    return os.getenv("RAG_SCRUB_IDENTIFIERS", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _compiled() -> List[Tuple[Pattern[str], str]]:
    """Compile default rules + any RAG_SCRUB_EXTRA_TERMS (each -> 'the project').
    Read live so operators can add terms without a redeploy of this module."""
    rules = list(_DEFAULT_RULES)
    extra = os.getenv("RAG_SCRUB_EXTRA_TERMS", "")
    for term in (t.strip() for t in extra.split(",")):
        if term:
            rules.append((r"\b" + re.escape(term) + r"\b", "the project"))
    # Longest source phrase first so specific multiword names win over substrings.
    rules.sort(key=lambda r: len(r[0]), reverse=True)
    return [(re.compile(p, re.IGNORECASE), repl) for p, repl in rules]


def scrub_identifiers(text: str) -> str:
    """Replace known project/client/third-party names with generic placeholders.
    No-op when disabled or text is empty."""
    if not text or not _enabled():
        return text
    for pat, repl in _compiled():
        text = pat.sub(repl, text)
    return text
