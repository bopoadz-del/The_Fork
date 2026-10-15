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
is written as a code (2-8 capitals and digits, at least two letters) and is not
a generic document or discipline code. Library files are not project
documents and keep their titles.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

_LOG = logging.getLogger(__name__)

#: Codes that name a kind of document or a discipline, not a project.
GENERIC_CODES = frozenset({
    "DWG", "DRG", "DRW", "REV", "SPEC", "SPC", "BOQ", "BQ", "RFI", "NCR", "MS", "MOM", "SOW", "TQ", "ITP",
    "SI", "VO", "IFC", "IFT", "IFA", "GA", "SK", "CAD", "PDF", "DOC", "RPT", "LOG", "CV", "AR", "ARC", "ARCH",
    "ST", "STR", "SE", "ME", "MEP", "EL", "ELE", "PL", "PLB", "FP", "FA", "HVAC", "ID", "LS", "LA", "TM", "TR",
    "GEO", "QS", "HSE", "QA", "QC", "SD", "DD", "CD", "TD", "PM", "OM", "ASB", "MTO", "WBS", "CPM",
})
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


class NameGuard:
    """The labels and project codes of one project's documents."""

    def __init__(self, docs: Iterable[dict[str, Any]] = ()) -> None:
        self.names: dict[str, str] = {}
        self.by_label: dict[str, str] = {}
        self.codes: set = set()
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
            if _CODE_RE.match(lead) and lead not in GENERIC_CODES:
                self.codes.add(lead)
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
            out = self._code_re.sub(PROJECT_PLACEHOLDER, out)
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
    return NameGuard(docs)
