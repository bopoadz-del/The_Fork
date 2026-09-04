"""Deterministic project/company identifier scrub for RAG answers.

Pilot confidentiality stopgap: the general-knowledge corpus (drive_archive)
still holds a client's project documents, whose technical content is useful but
whose *names* must not leak into answers (one client's project identity showing
up in another context). Until deployment-grade per-tenant isolation lands, this
scrubs known project / client / third-party names out of the FINAL answer text
and replaces them with generic placeholders ("the project" / "the client").

Deterministic denylist, not NER — it reliably catches the known identifiers in
this corpus. Rules live in RAG_SCRUB_RULES (see below); RAG_SCRUB_EXTRA_TERMS adds
literal terms. Disable with RAG_SCRUB_IDENTIFIERS=0.

Order matters: longer/multiword phrases are matched first so "<CODE> Infra Pack 1"
becomes "the project", never "the project Infra Pack 1".
"""
from __future__ import annotations

import logging
import os
import re
from typing import List, Pattern, Tuple

# The denylist is NOT in this file. The identifiers it scrubs are the client's
# own names, and a rule that carries them is itself the leak: the repo-wide
# client-pattern scan (scripts/scan_secrets.py) is fail-closed on exactly those
# strings. Rules are fed from the environment, one per line:
#
#     <regex> => <replacement>      # replacement defaults to "the project"
#
# RAG_SCRUB_EXTRA_TERMS (comma-separated literals) is kept for operators who
# only need to add a word. Both are read live, so a term can be added without
# a redeploy of this module.
_RULES_ENV = "RAG_SCRUB_RULES"
_EXTRA_ENV = "RAG_SCRUB_EXTRA_TERMS"
_DEFAULT_REPLACEMENT = "the project"
_warned_empty = False


def _rules_from_env() -> List[Tuple[str, str]]:
    raw = os.getenv(_RULES_ENV, "").replace("\\n", "\n")
    rules: List[Tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            pat, repl = line.split("=>", 1)
            rules.append((pat.strip(), repl.strip() or _DEFAULT_REPLACEMENT))
        else:
            rules.append((line, _DEFAULT_REPLACEMENT))
    extra = os.getenv(_EXTRA_ENV, "")
    for term in (x.strip() for x in extra.split(",")):
        if term:
            rules.append((r"\b" + re.escape(term) + r"\b", _DEFAULT_REPLACEMENT))
    return rules


def rules_loaded() -> int:
    """How many scrub rules the environment currently supplies. Zero while
    scrubbing is enabled is a misconfiguration, and /ready should say so."""
    return len(_rules_from_env())


def _enabled() -> bool:
    return os.getenv("RAG_SCRUB_IDENTIFIERS", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _compiled() -> List[Tuple[Pattern[str], str]]:
    """Compile the env-fed rules. Read live so operators can add terms without
    a redeploy. Enabled-with-no-rules is logged loudly ONCE: it cannot refuse
    to serve (that would take the service down), but it must never be silent."""
    global _warned_empty
    rules = _rules_from_env()
    if not rules and not _warned_empty:
        _warned_empty = True
        logging.getLogger(__name__).error(
            "identifier scrub is enabled but %s is empty: client identifiers "
            "will pass through unscrubbed. Set the env var.", _RULES_ENV,
        )
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


def scrub_identifiers_filename(name: str) -> str:
    """Scrub a FILENAME for display (sources panel, citations).

    Filenames bind identifiers with underscores ("DGII_MS-001.pdf"), which
    are word characters, so the prose rules' ``\\b`` boundaries never fire
    -- the answer text was scrubbed while the sources panel leaked the
    identity verbatim. Normalise underscores to spaces first; the result is
    a display label, not a path, so the change is safe."""
    if not name or not _enabled():
        return name
    return scrub_identifiers(name.replace("_", " "))
