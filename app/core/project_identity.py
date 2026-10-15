"""A project's identity set, and the one layer that keeps it out of every answer.

Order 2210Z: a project's real name, its short names and abbreviations, its
codes (the one inside drawing references), its contract numbers and its site
names never leave in an answer, a citation or a Sources line. The set is data
per project, derived from the project's own record -- never a list in code:

- the record's name, client and location, and the name without its generic
  trailing words ("Harbour Tower Development Phase 2" -> "Harbour Tower");
- the initials of a multi-word name or client, three letters or more ("HTD");
- every code written in those fields, and the code that leads the project's
  own document file names (app.core.document_labels);
- profile values whose key names a project identifier: a contract, project or
  tender number, code or reference; a site; a client, employer, owner or
  contractor;
- anything an admin added to the stored set ("added").

It is stored with the project (``projects.identity``, JSON) at ingest and
whenever it changes, and every member is replaced by the neutral project id
(``PRJ-<first six of the id>``) wherever text leaves: answer_exit.check_text
and check_sources (both answer paths, persistence and read-back), the
driver's exit and the streamed events.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
from collections.abc import Iterable
from typing import Any

_LOG = logging.getLogger(__name__)

#: Words that describe a kind of project or a stage of one, not its identity.
_GENERIC_TAIL = re.compile(
    r"(?:\s+(?:-\s*)?(?:project|development|works|scheme|package|phase|stage|lot|contract|programme|program)"
    r"(?:\s+[0-9IVX]+[A-Za-z]?)?)+\s*$",
    re.IGNORECASE,
)
_STOP = frozenset({"of", "the", "and", "at", "for", "in", "on", "a", "an", "to", "&"})
_ID_KEY = re.compile(
    r"(?i)^(?:(?:contract|project|tender|job|site)[ _-]?(?:no|nr|number|code|ref|reference|id|name)|"
    r"site|site[ _-]?name|client|employer|owner|contractor|main[ _-]?contractor|consultant|developer)$"
)
_CODE = re.compile(r"(?<![A-Za-z0-9])(?=(?:[0-9]*[A-Z]){2})[A-Z][A-Z0-9]{1,7}(?![A-Za-z0-9])")
_MIN_LEN = 3


def neutral_id(project_id: str) -> str:
    """The name every identity member becomes: ``PRJ-<first six of the id>``."""
    short = re.sub(r"[^0-9A-Za-z]", "", project_id or "")[:6]
    return f"PRJ-{short}" if short else "PRJ"


def _initials(value: str) -> str:
    words = [w for w in re.split(r"[\s/,-]+", value) if w and w.lower() not in _STOP]
    if len(words) < 2:
        return ""
    letters = "".join(w[0] for w in words if w[0].isalpha())
    return letters.upper() if len(letters) >= _MIN_LEN else ""


def derive(project: dict[str, Any], document_names: Iterable[str] = (), *, general: Any = None) -> list[str]:
    """The identity members the project's record and documents give.
    ``general`` overrides the platform vocabulary (document_labels)."""
    from app.core.document_labels import NameGuard

    fields = [str(project.get(k) or "").strip() for k in ("name", "client", "location")]
    profile = project.get("profile") if isinstance(project.get("profile"), dict) else {}
    for key, value in (profile or {}).items():
        if _ID_KEY.match(str(key)) and isinstance(value, (str, int)) and str(value).strip():
            fields.append(str(value).strip())
    members: set[str] = set()
    for value in fields:
        if len(value) < _MIN_LEN:
            continue
        members.add(value)
        short = _GENERIC_TAIL.sub("", value).strip(" -,")
        if len(short) >= _MIN_LEN and short != value and len(short.split()) >= 2:
            members.add(short)
        for form in {value, short}:
            if initials := _initials(form):
                members.add(initials)
        members.update(m.group(0) for m in _CODE.finditer(value))
    guard = NameGuard(({"id": str(i), "original_name": n} for i, n in enumerate(document_names)), general=general)
    members.update(guard.codes)
    return sorted(m for m in members if len(m) >= 2)


class Redactor:
    """Replaces every member of one project's identity set."""

    def __init__(self, project_id: str, members: Iterable[str]) -> None:
        self.project_id = project_id
        self.replacement = neutral_id(project_id)
        self.members = sorted({m for m in members if m and len(m) >= 2}, key=len, reverse=True)
        parts = []
        for member in self.members:
            body = re.escape(member).replace(r"\ ", r"[\s_-]+")
            # Codes and initials match as written; names match in any case.
            flags = "" if _CODE.fullmatch(member) else "(?i:"
            parts.append(f"{flags}{body})" if flags else body)
        self._re = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?![A-Za-z0-9])") if parts else None

    def text(self, value: Any) -> Any:
        if not isinstance(value, str) or self._re is None or not value:
            return value
        return self._re.sub(self.replacement, value)

    def deep(self, value: Any) -> Any:
        """``value`` with every string inside it redacted."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.deep(v) for v in value]
        if isinstance(value, dict):
            return {k: self.deep(v) for k, v in value.items()}
        return value


def _stored(project_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from app.core import projects
    from app.core.models import Project

    with projects.SessionLocal() as session:
        raw = session.execute(select(Project.identity).where(Project.id == project_id)).scalar_one_or_none()
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def refresh(project_id: str) -> list[str]:
    """Derive the set from the record and documents, store it when it
    changed, and return it (the derived members plus any an admin added)."""
    from app.core import projects
    from app.core.models import Project

    project = projects.get_project(project_id) or {}
    if not project:
        return []
    names = [str(d.get("original_name") or "") for d in (projects.list_document_names(project_id) or [])]
    derived = derive(project, names)
    stored = _stored(project_id)
    added = [str(m) for m in stored.get("added") or [] if str(m).strip()]
    if stored.get("derived") != derived:
        with projects.SessionLocal() as session:
            row = session.get(Project, project_id)
            if row is not None:
                row.identity = json.dumps({"derived": derived, "added": added})
                session.commit()
    return sorted(set(derived) | set(added))


def redactor_for(project_id: str | None) -> Redactor | None:
    """The project's redactor; None when there is no project or its set
    cannot be read (logged -- the record is never a reason to fail a turn)."""
    if not project_id:
        return None
    try:
        return Redactor(project_id, refresh(project_id))
    except Exception:  # noqa: BLE001
        _LOG.warning("project_identity: identity set unavailable for %s", project_id, exc_info=True)
        return None


_CURRENT: contextvars.ContextVar[Redactor | None] = contextvars.ContextVar("project_identity", default=None)


def set_current(redactor: Redactor | None) -> contextvars.Token:
    """This turn's redactor: every exit check in this context applies it."""
    return _CURRENT.set(redactor)


def reset(token: contextvars.Token) -> None:
    _CURRENT.reset(token)


def current() -> Redactor | None:
    return _CURRENT.get()


def redact(value: Any) -> Any:
    """``value`` (a string, list or dict) with this turn's project identity
    replaced; unchanged when no project is in scope."""
    red = _CURRENT.get()
    return red.deep(value) if red is not None else value
