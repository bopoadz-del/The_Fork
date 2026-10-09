"""Deterministic project/company identifier scrub for RAG answers.

Pilot confidentiality stopgap: the master-corpus source project still holds a
client's project documents, whose technical content is useful but whose
*names* must not leak into answers (one client's project identity showing up in
another context). Until deployment-grade per-tenant isolation lands, this
scrubs project / client identifiers out of the FINAL answer text and the
sources panel, replacing them with generic placeholders ("the project" /
"the client").

Structural, not a denylist. The repo holds no name: the identifiers are
DERIVED at run time from data the platform already keeps, by general rules
whose parameters live in ``app/core/scrub_rules.json``:

* **Which projects.** The projects whose documents are served to OTHER
  projects: the master-corpus source project and the general-knowledge
  projects (``app.core.projects``). Product-owned namespaces declared in
  ``app.core.system_projects`` carry the product's own labels, not a client's
  identity, and are skipped.
* **Identity fields.** Each of those projects' registered name, client and
  location, expanded with the shorter forms people actually write -- the
  variants and initials from ``app.core.party_names`` (one implementation,
  shared with the party-name withholding). A form made only of generic words
  ("Saudi Arabia", "Company Limited") identifies nobody and is not scrubbed.
* **Document-number codes.** A filename prefix code (the pattern is data) that
  recurs in at least N of one scrubbed project's filenames and appears in NO
  other project's filenames is that project's code ("XYZ" in "XYZ_MS-001").
  Common document-type codes (data) never qualify.
* **Title-block labels** ("Project Name: ...") are declared in the data file
  but switched off: finding them means pattern-searching the indexed chunk
  text of the source corpus, a scan of most of the chunk table that would run
  on the request path every cache period. It stays off until that lookup can
  be precomputed at ingest time.

Rules are read live through a short TTL cache, so a renamed project or a new
document is picked up without a redeploy. ``RAG_SCRUB_EXTRA_TERMS``
(comma-separated literals) still adds a word; ``RAG_SCRUB_IDENTIFIERS=0``
disables the scrub. A projects store that cannot be read never fails a turn:
it is logged loudly once and the scrub falls back to the data-file-only
rules.

Order matters: longer/multiword phrases are matched first so
"<CODE> Infra Pack 1" becomes "the project", never "the project Infra Pack 1".
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Pattern, Set, Tuple

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).with_name("scrub_rules.json")
_EXTRA_ENV = "RAG_SCRUB_EXTRA_TERMS"
_DEFAULT_REPLACEMENT = "the project"
_DEFAULT_TTL_SECONDS = 60.0
_EDGE_L = r"(?<![A-Za-z0-9])"
_EDGE_R = r"(?![A-Za-z0-9])"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# (regex source, replacement, re flags)
Rule = Tuple[str, str, int]

_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None, "expires": 0.0, "rules": []}
_warned_empty = False
_warned_db = False
_warned_file = False
_warned_labels = False
_spec_cache: List[Any] = [None, {}]  # [file mtime, parsed spec]


def _reset_cache() -> None:
    """Drop the cached structural rules (tests; an operator after a rename)."""
    with _lock:
        _cache.update(key=None, expires=0.0, rules=[])


def _load_spec() -> Dict[str, Any]:
    """The rule parameters. A missing or unreadable file is logged once and
    read as no structural rules -- never an exception inside a turn."""
    global _warned_file
    try:
        mtime = _RULES_FILE.stat().st_mtime_ns
        if _spec_cache[0] == mtime:
            return _spec_cache[1]
        with open(_RULES_FILE, encoding="utf-8") as fh:
            spec = json.load(fh)
        spec = spec if isinstance(spec, dict) else {}
        _spec_cache[:] = [mtime, spec]
        return spec
    except (OSError, ValueError) as exc:
        if not _warned_file:
            _warned_file = True
            logger.error("identifier scrub: cannot read %s (%s); structural "
                         "rules are off", _RULES_FILE.name, exc)
        return {}


def _scrubbed_project_ids(spec: Dict[str, Any]) -> Tuple[str, ...]:
    """Projects whose identities are scrubbed when their documents are served
    to other projects. Ids come from configuration, never from this file."""
    from app.core import projects
    from app.core.system_projects import SYSTEM_PROJECTS

    which = spec.get("scrubbed_projects") or {}
    ids: List[str] = []
    if which.get("master_corpus_source"):
        ids.append(projects.MASTER_CORPUS_SOURCE_PROJECT_ID)
    if which.get("general_knowledge"):
        ids.extend(sorted(projects.general_knowledge_project_ids()))
    if which.get("skip_system_namespaces"):
        ids = [i for i in ids if i not in SYSTEM_PROJECTS]
    return tuple(dict.fromkeys(i for i in ids if i))


def _phrase(form: str) -> str:
    return _EDGE_L + r"\s+".join(re.escape(w) for w in form.split()) + _EDGE_R


def _all_generic(form: str) -> bool:
    from app.core.party_names import _GENERIC_WORDS

    words = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", form)]
    return not words or all(w in _GENERIC_WORDS for w in words)


def _identity_rules(value: Optional[str], field: Dict[str, Any],
                    expansion: Dict[str, Any]) -> List[Rule]:
    """Rules for one registered identity value: the value itself, its written
    variants and (when the field allows) its initials."""
    from app.core.party_names import _acronym, _variants

    value = re.sub(r"\s+", " ", value or "").strip()
    min_len = int(expansion.get("min_value_length", 4))
    if len(value) < min_len:
        return []
    replacement = field.get("replacement") or _DEFAULT_REPLACEMENT
    forms: Set[str] = {value}
    if expansion.get("variants"):
        forms.update(_variants(value))
    skip_generic = bool(expansion.get("skip_all_generic_words", True))
    rules: List[Rule] = [
        (_phrase(f), replacement, re.IGNORECASE)
        for f in forms
        if len(f) >= min_len and not (skip_generic and _all_generic(f))
    ]
    if field.get("acronym"):
        initials = _acronym(value)
        if initials:
            # Case-sensitive: initials never touch an ordinary lower-case word.
            rules.append((_EDGE_L + re.escape(initials) + _EDGE_R, replacement, 0))
    return rules


def _code_rules(rows: Iterable[Tuple[str, str]], scrubbed: Set[str],
                spec: Dict[str, Any]) -> List[Rule]:
    """Document-number codes: a filename prefix code that recurs in at least
    ``min_occurrences`` of ONE scrubbed project's filenames and is a token of
    no other project's filename."""
    if not spec.get("enabled"):
        return []
    prefix = re.compile(spec.get("pattern") or r"^([A-Z][A-Z0-9]{2,11})(?=[-_ ])")
    min_n = int(spec.get("min_occurrences", 3))
    generic = {str(c).upper() for c in spec.get("generic_codes") or []}
    replacement = spec.get("replacement") or _DEFAULT_REPLACEMENT

    prefix_counts: Dict[str, Counter] = defaultdict(Counter)
    tokens_by_project: Dict[str, Set[str]] = defaultdict(set)
    for pid, name in rows:
        base = os.path.basename(name or "")
        tokens_by_project[pid].update(t.upper() for t in _TOKEN_RE.findall(base))
        m = prefix.search(base)
        if m and pid in scrubbed:
            prefix_counts[pid][m.group(1)] += 1

    rules: List[Rule] = []
    for pid, counts in prefix_counts.items():
        for code, n in counts.items():
            if n < min_n or code.upper() in generic:
                continue
            if any(code.upper() in toks for other, toks in tokens_by_project.items()
                   if other != pid):
                continue
            rules.append((_EDGE_L + re.escape(code) + _EDGE_R, replacement, 0))
    return rules


def _structural_rules(spec: Dict[str, Any], ids: Tuple[str, ...]) -> List[Rule]:
    """Read identities from the projects store. Raises on a DB failure; the
    caller owns the fallback."""
    global _warned_labels
    if not ids:
        return []
    from sqlalchemy import select

    from app.core import projects

    projects._ensure_db()
    fields: Dict[str, Dict[str, Any]] = spec.get("identity_fields") or {}
    expansion: Dict[str, Any] = spec.get("identity_expansion") or {}
    rules: List[Rule] = []
    with projects.SessionLocal() as session:
        rows = session.execute(
            select(projects.Project).where(projects.Project.id.in_(ids))
        ).scalars().all()
        for row in rows:
            for attr, field in fields.items():
                rules.extend(_identity_rules(getattr(row, attr, None), field, expansion))
        code_spec = spec.get("document_number_code") or {}
        if code_spec.get("enabled"):
            doc_rows = session.execute(
                select(projects.Document.project_id, projects.Document.original_name)
            ).all()
            rules.extend(_code_rules(doc_rows, set(ids), code_spec))
    if (spec.get("title_block_labels") or {}).get("enabled") and not _warned_labels:
        _warned_labels = True
        logger.warning("identifier scrub: title_block_labels is enabled in %s but "
                       "is not applied at serve time (see module docstring)",
                       _RULES_FILE.name)
    return rules


def _cached_structural_rules() -> List[Rule]:
    global _warned_db
    spec = _load_spec()
    try:
        from app.core.db import get_database_url

        ids = _scrubbed_project_ids(spec)
        key = (ids, get_database_url())
    except Exception as exc:  # noqa: BLE001 -- config read must not fail a turn
        logger.error("identifier scrub: cannot resolve scrubbed projects (%s)", exc)
        return []
    now = time.monotonic()
    with _lock:
        if _cache["key"] == key and now < _cache["expires"]:
            return list(_cache["rules"])
    try:
        rules = _structural_rules(spec, ids)
        _warned_db = False
    except Exception as exc:  # noqa: BLE001 -- a DB outage must not fail a turn
        if not _warned_db:
            _warned_db = True
            logger.error(
                "identifier scrub: projects store unreadable (%s: %s); falling "
                "back to data-file-only rules -- client identifiers may pass "
                "through unscrubbed", type(exc).__name__, exc,
            )
        rules = []
    ttl = float(spec.get("cache_ttl_seconds", _DEFAULT_TTL_SECONDS))
    with _lock:
        _cache.update(key=key, expires=now + ttl, rules=list(rules))
    return rules


def _extra_term_rules(spec: Dict[str, Any]) -> List[Rule]:
    replacement = spec.get("extra_terms_replacement") or _DEFAULT_REPLACEMENT
    extra = os.getenv(_EXTRA_ENV, "")
    return [
        (r"\b" + re.escape(term) + r"\b", replacement, re.IGNORECASE)
        for term in (x.strip() for x in extra.split(","))
        if term
    ]


def _rules() -> List[Rule]:
    rules = _cached_structural_rules() + _extra_term_rules(_load_spec())
    return list(dict.fromkeys(rules))


def rules_loaded() -> int:
    """How many scrub rules are currently derived. Zero while scrubbing is
    enabled means identifiers pass through, and /ready reports it."""
    return len(_rules())


def _enabled() -> bool:
    return os.getenv("RAG_SCRUB_IDENTIFIERS", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _compiled() -> List[Tuple[Pattern[str], str]]:
    """Compile the derived rules. Enabled-with-no-rules is logged loudly ONCE:
    it cannot refuse to serve (that would take the service down), but it must
    never be silent."""
    global _warned_empty
    rules = _rules()
    if not rules and not _warned_empty:
        _warned_empty = True
        logger.error(
            "identifier scrub is enabled but no rules were derived: client "
            "identifiers will pass through unscrubbed. Check that the master-"
            "corpus source / general-knowledge projects exist and carry a name.",
        )
    # Longest source phrase first so specific multiword names win over substrings.
    rules.sort(key=lambda r: len(r[0]), reverse=True)
    return [(re.compile(p, flags), repl) for p, repl, flags in rules]


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

    Filenames bind identifiers with underscores ("QPII_MS-001.pdf"), which
    are word characters, so the prose rules' ``\\b`` boundaries never fire
    -- the answer text was scrubbed while the sources panel leaked the
    identity verbatim. Normalise underscores to spaces first; the result is
    a display label, not a path, so the change is safe."""
    if not name or not _enabled():
        return name
    return scrub_identifiers(name.replace("_", " "))
