"""How the model and the answer see a project's own documents.

Order 2130Z (LANE4 NAME FLAG): a stored file name can carry a client's or a
project's real name, and so can the project code that leads it and recurs in
drawing references ("ABC-ST-0042"). One rule for every project document, at
every place its text reaches the model:

- a document is named by its type and a short id ("Specification abc123"),
  never by its file name or path;
- in passage text, the document's own names become that label, and the
  project code that leads the project's file names becomes ``[project]``
  (the rest of a drawing reference stays readable: "[project]-ST-0042").

A project code is the leading segment of the project's own file names when it
is written as a code (2-8 capitals and digits, at least two letters) and the
platform's own data does not use it: not in its formula, hat or unit texts,
not a currency in its currency table, and not a whole word of its library
corpus (general_vocabulary). No list of codes is kept here. Library files are
not project documents and keep their titles.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

_LOG = logging.getLogger(__name__)

PROJECT_PLACEHOLDER = "[project]"
_CODE_RE = re.compile(r"^(?=(?:[0-9]*[A-Z]){2})[A-Z][A-Z0-9]{1,7}$")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_SEP_RE = re.compile(r"[-_.\s]+")


def label(doc_id: str, doc_type: str = "") -> str:
    """'<Type> <short id>': the document's type and the first six
    characters of its id; 'Document' when it has no type."""
    kind = re.sub(r"[_\-]+", " ", (doc_type or "").strip()).strip().capitalize() or "Document"
    short = re.sub(r"[^0-9A-Za-z]", "", doc_id or "")[:6]
    return f"{kind} {short}".strip() if short else kind


def _base(name: str) -> str:
    parts = [p for p in (name or "").replace(chr(92), "/").split("/") if p.strip()]
    return (parts[-1] if parts else "").strip()


_REGISTRY_VOCAB: set[str] | None = None
_LIBRARY_SEEN: dict[str, bool] = {}
_CODE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(?=(?:[0-9]*[A-Z]){2})[A-Z][A-Z0-9]{1,7}(?![A-Za-z0-9])")


def _registry_vocabulary() -> set[str]:
    """Every code the platform's own registries write: formula names,
    descriptions and units, hat descriptions, and the currency table."""
    global _REGISTRY_VOCAB
    if _REGISTRY_VOCAB is None:
        texts: list[str] = []
        try:
            from app.lib import formula_registry

            for spec in list(formula_registry._REGISTRY.values()):
                texts += [spec.name, spec.description, spec.display_name, *map(str, spec.outputs.values()),
                          *map(str, spec.inputs.values())]
        except Exception:  # noqa: BLE001 -- vocabulary is best-effort; a missing source narrows it
            _LOG.debug("document_labels: formula registry unavailable", exc_info=True)
        try:
            from app.agents import catalog

            texts += [f"{h.name} {h.description} {h.system_prompt_guidance or ''}" for h in catalog.list_hats()]
        except Exception:  # noqa: BLE001
            _LOG.debug("document_labels: hat catalogue unavailable", exc_info=True)
        vocab = set(_CODE_TOKEN_RE.findall(" ".join(texts)))
        try:
            from app.lib.construction_formulas import _CURRENCY_CODES

            vocab |= set(_CURRENCY_CODES)
        except Exception:  # noqa: BLE001
            _LOG.debug("document_labels: currency table unavailable", exc_info=True)
        _REGISTRY_VOCAB = vocab
    return _REGISTRY_VOCAB


def _in_library(code: str) -> bool:
    """True when ``code`` is a whole word of the platform's library corpus
    (the general-knowledge projects' passages). Cached per process."""
    if code in _LIBRARY_SEEN:
        return _LIBRARY_SEEN[code]
    found = False
    try:
        from sqlalchemy import select

        from app.core import projects
        from app.core.models import RagChunk
        from app.core.rag.retriever import _general_knowledge_project_ids

        ids = [i for i in _general_knowledge_project_ids() if i]
        if ids:
            word = re.compile(r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])")
            with projects.SessionLocal() as session:
                rows = session.execute(
                    select(RagChunk.text).where(RagChunk.project_id.in_(ids), RagChunk.text.like(f"%{code}%")).limit(20)
                ).scalars().all()
            found = any(word.search(t or "") for t in rows)
    except Exception:  # noqa: BLE001 -- unknown means "not general": the code stays redacted
        _LOG.debug("document_labels: library lookup failed for %s", code, exc_info=True)
    _LIBRARY_SEEN[code] = found
    return found


def general_vocabulary(codes: Iterable[str]) -> set[str]:
    """Which of ``codes`` the platform's own data uses -- general words,
    never a project's identity."""
    vocab = _registry_vocabulary()
    return {c for c in codes if c in vocab or _in_library(c)}


class NameGuard:
    """The labels and project codes of one project's documents."""

    def __init__(self, docs: Iterable[dict[str, Any]] = (), *, general: Any = None,
                 placeholder: str = PROJECT_PLACEHOLDER) -> None:
        self.placeholder = placeholder
        leads: set[str] = set()
        self.names: dict[str, str] = {}
        self.by_label: dict[str, str] = {}
        for doc in docs:
            doc_id = str(doc.get("id") or doc.get("document_id") or "").strip()
            name = str(doc.get("original_name") or doc.get("filename") or "").strip()
            if not doc_id:
                continue
            shown = label(doc_id, str(doc.get("doc_type") or ""))
            self.by_label.setdefault(shown.lower(), doc_id)
            base = _base(name)
            stem = _EXT_RE.sub("", base)
            for variant in {name, base, stem, _SEP_RE.sub(" ", stem).strip()}:
                if len(variant) >= 4:
                    self.names[variant] = shown
            lead = _SEP_RE.split(stem)[0] if stem else ""
            if _CODE_RE.match(lead):
                leads.add(lead)
        known = general(leads) if callable(general) else (set(general) if general is not None
                                                           else general_vocabulary(leads))
        self.codes = leads - set(known)
        self._code_re = (re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, sorted(self.codes, key=len, reverse=True)))
                                    + r")(?![A-Za-z0-9])") if self.codes else None)

    def text(self, value: str) -> str:
        """``value`` with every document name made its label and every
        project code made ``[project]``."""
        out = value or ""
        if not out:
            return out
        for name in sorted(self.names, key=len, reverse=True):
            if name in out:
                out = out.replace(name, self.names[name])
        if self._code_re is not None:
            out = self._code_re.sub(self.placeholder, out)
        return out

    def resolve(self, shown: str) -> str | None:
        """The document id a label stands for, or None."""
        return self.by_label.get((shown or "").strip().lower())


def for_project(project_id: str | None) -> NameGuard:
    """The guard for ``project_id``'s documents; an empty guard when the
    documents cannot be read (a label is never a gate)."""
    if not project_id:
        return NameGuard()
    try:
        from app.core import projects

        docs: list[dict[str, Any]] = projects.list_document_names(project_id) or []
    except Exception:  # noqa: BLE001 -- the labels are a presentation rule, never a failure
        _LOG.debug("document_labels: could not list documents for %s", project_id, exc_info=True)
        docs = []
    from app.core.project_identity import neutral_id

    return NameGuard(docs, placeholder=neutral_id(project_id))
