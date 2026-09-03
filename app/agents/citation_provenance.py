"""Citation provenance — an attribution the turn cannot back is not shipped.

THE INCIDENT (gate battery ``13b2bf7``, 2026-08-31, question F2). The WBS
answer closed with::

    BOQ context: Bill 03 - Demolition and Site Clearance (DD-2022-175)

on a DD-2023-118 project. Three literal checks, not inference:

* ``generate_wbs`` (``app/containers/construction/schedule.py:2235``)
  documents itself "Deterministic template-based: no LLM"; its body contains
  no ``boq``/``bill``/``quantit``/``contract`` token at all. It has no BOQ
  input to read.
* The string ``BOQ context`` does not exist anywhere in this repository.
  Nothing composed that line; the model wrote it.
* The contract id it named belongs to a different contract year than the
  project under discussion.

So the sentence was not a mis-scoped retrieval that a fence could re-scope.
It was prose shaped like a citation, with no retrieval or tool run behind it.

THE RULE (owner's ruling, 2026-09-01): *the model never writes a Source
line.* Citations are rendered from EVIDENCE OBJECTS — retrieval records and
tool-run records with their actual inputs. Any Source / BOQ / contract
attribution in model prose that matches no evidence record is stripped and
the answer flagged "unverified attribution removed".

This module is the sibling of the cost-grounding gate in ``runtime.py``.
That one grounds the FIGURES; this one grounds the claim about WHERE the
figures came from. Same contract: flag-controlled, never raises, and it
edits the attribution only — an answer's content is never rewritten by it.

SCOPE, stated so the edges are known rather than discovered:

* Attributions are policed; content claims are not. "Clause 8.8.1 says X" is
  the numeric/­retrieval guards' business. "Source: DD-2022-175" is this one's.
* When the turn read the corpus at all, only the attribution surface is
  policed (Source lines, ``BOQ context:`` fragments, parenthesised ids), so a
  working answer that discusses a contract in prose is never mangled.
* When the turn read NO corpus — the F2 shape — every attribution in the
  answer is unbacked by construction, and ids the user did not themselves
  name are stripped wherever they appear.
* ``source_class`` is carried on every record and enforced here, and the
  retrieval marker now emits ``class=`` (the owner's numbered item 2), so
  the classes are live rather than a forward fence. ``project_corpus`` and
  ``master_corpus`` may back an identifier attribution; ``knowledge_base``
  and ``template`` may not. A reference note or a blank form is a real
  document and may still be NAMED as a source — what it may not do is lend
  its identifiers to a claim about this project's contract, which is the G1
  shape.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

_LOG = logging.getLogger(__name__)

FLAG_ENV = "CITATION_PROVENANCE_GATE"

UNVERIFIED_NOTE = (
    "\n\n_Unverified attribution removed: this answer named a source that "
    "nothing in this turn actually read. The answer's content is unchanged; "
    "only the unbacked attribution was taken out._"
)

# PREFIX-YEAR-SEQ, identical in shape to
# ``app.core.rag.retriever._CONTRACT_DOC_ID_RE`` so an id this module strips
# is exactly an id that module would have scoped on. Not a \b pattern:
# underscore-glued filenames ("DD-2023-118_Vol 1.pdf") must still match.
_CONTRACT_ID_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,}-\d{4}-\d+)(?![A-Za-z0-9])")

# The F2 form, and the general "BOQ context: ..." attribution it belongs to.
# Leading separator is swallowed so removing the fragment does not leave a
# dangling pipe or bullet behind.
_BOQ_ATTRIB_RE = re.compile(
    r"(?:BOQ|Bill\s+of\s+Quantities)\s+context\s*:[^|\n]*",
    re.IGNORECASE,
)

# A markdown table row. Inside one, every pipe is structure: a stripped cell
# must be left EMPTY, never closed up, or the row loses a column and the table
# stops rendering. Found by the table-row fence test, which the first cut of
# this module failed.
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")

# A "Source:" / "Sources:" attribution line. Only whole lines — an inline
# "source: ..." mid-sentence is prose, and prose is out of scope.
_SOURCE_LINE_RE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]+)?\**[ \t]*Sources?[ \t]*:?\**[ \t]*:?[ \t]*"
    r"\**[ \t]*(?P<body>.+?)[ \t]*\**[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)

# An id in running prose is only an attribution when the grammar makes it one.
# Blanket id-stripping would eat standards references -- "ISO-9001-2015"
# matches the contract-id shape exactly -- so the cue is required.
_ATTRIB_CUE_ID_RE = re.compile(
    r"\b(?P<cue>per|from|source|sources|ref\.?|refer\s+to|see|under|cited\s+in|"
    r"according\s+to|as\s+stated\s+in|as\s+set\s+out\s+in|taken\s+from)"
    r"[ \t]+(?P<id>[A-Za-z]{2,}-\d{4}-\d+)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# A parenthesised id directly after a name is an attribution:
# "Bill 03 - Demolition and Site Clearance (DD-2022-175)".
_PAREN_ID_RE = re.compile(
    r"[ \t]*\((?:(?:from|per|source|ref\.?|see)[ \t]+)?"
    r"([A-Za-z]{2,}-\d{4}-\d+)\)",
    re.IGNORECASE,
)

# Per-chunk retrieval marker emitted by
# ``app.core.rag.inject.format_chunks_as_system_message``. ``class=`` does not
# exist yet (numbered item 2); it is read here so that when it lands, this
# gate enforces it without another change.
_MARKER_RE = re.compile(
    r"\[doc_id=(?P<doc>[^\s\]]+)\s+chunk=(?P<chunk>\d+)"
    r"(?P<attrs>[^\]]*)\]",
)
_MARKER_SRC_RE = re.compile(r"\bsrc=([^\]]+?)(?=\s+\w+=|$)")
_MARKER_CLASS_RE = re.compile(r"\bclass=([A-Za-z_]+)")

# Chunks that actually carry priced-bill material. A "BOQ context" claim is
# backed only by one of these, never by a chunk that merely says "bill".
_BOQ_SEMANTIC_RE = re.compile(
    r"\b(?:bill\s+of\s+quantit|\bboq\b|priced\s+bill|rate\s+schedule|"
    r"schedule\s+of\s+rates|unit\s+rate)\b",
    re.IGNORECASE,
)

# Tools PROVEN not to retrieve from the project corpus. Each was verified by
# reading its body on 13b2bf7 -- zero corpus/retriev/search_project/rag_/chunk
# references in any of them:
#   generate_wbs            schedule.py:2235   (template scheduler)
#   construction_calc       __init__.py:2164   (deterministic formula registry)
#   resource_histogram      schedule.py:724
#   commissioning_checklist schedule.py:652
#   cash_flow_forecast      boq.py:1709        (reads a BOQ only from its OWN
#                                               payload -- see boq_backed())
#   wir_form                documents.py:2112
# The default for an unlisted tool is "assume it read the corpus", so a new
# tool can never have a legitimate attribution stripped by omission. Adding a
# tool here is a claim about its body and must be verified the same way.
NON_CORPUS_TOOLS: frozenset[str] = frozenset({
    "generate_wbs",
    "construction_calc",
    "resource_histogram",
    "commissioning_checklist",
    "cash_flow_forecast",
    "wir_form",
})

# Source classes whose material may be cited as the project's own words.
#
# ``master_corpus`` is here and ``knowledge_base``/``template`` are not, and
# the asymmetry is deliberate. The master corpus is the DISCLOSED empty/thin
# fallback (STEP 0b): when it is in play it is the only corpus there is, the
# runtime already labels the answer as a fallback, and refusing its
# identifiers would strip the attribution off every answer on that path. A
# curated reference note or a blank form is the opposite case -- the project
# corpus is right there, and lending a standard form's identifiers to a claim
# about this contract is exactly the G1 defect.
CITABLE_CLASSES: frozenset[str] = frozenset(
    {"project_corpus", "master_corpus", "user"}
)


@dataclass
class EvidenceRecord:
    """One thing that actually happened this turn.

    ``kind`` is "retrieval" (a chunk came back), "tool_run" (a tool executed,
    with the inputs it was actually given) or "user" (the operator's own
    words -- an id the user named is theirs, not a fabrication).
    """

    kind: str
    text: str = ""
    tool: str | None = None
    inputs: str = ""
    source_name: str = ""
    source_class: str = "project_corpus"

    @property
    def reads_corpus(self) -> bool:
        if self.kind == "retrieval":
            return bool(self.text.strip())
        if self.kind == "tool_run":
            return (self.tool or "") not in NON_CORPUS_TOOLS
        return False

    @property
    def citable(self) -> bool:
        """May an identifier in this record back an attribution?

        A corpus-reading tool run can: ``search_project_documents`` returns
        real document ids. A template scheduler cannot -- that is the whole
        of F2. The operator's own words always can: an id the user typed is
        theirs, not an invention.
        """
        if self.kind == "user":
            return True
        if self.kind == "tool_run":
            return self.reads_corpus
        return self.source_class in CITABLE_CLASSES

    def identifiers(self) -> set[str]:
        blob = " ".join((self.text, self.inputs, self.source_name))
        return {m.group(1).lower() for m in _CONTRACT_ID_RE.finditer(blob)}

    def carries_boq(self) -> bool:
        """True when this record actually holds priced-bill material.

        Retrieval: the chunk is BOQ-semantic. Tool run: the tool was HANDED a
        non-empty ``boq`` payload -- ``cash_flow_forecast`` legitimately works
        that way, and its citation is honest. ``generate_wbs`` has no such
        field at all, which is why F2's line had nothing behind it.
        """
        if self.kind == "retrieval":
            return bool(_BOQ_SEMANTIC_RE.search(self.text))
        if self.kind == "tool_run":
            try:
                args = json.loads(self.inputs) if self.inputs else {}
            except (ValueError, TypeError):
                return bool(_BOQ_SEMANTIC_RE.search(self.inputs))
            if isinstance(args, dict):
                for key in ("boq", "bill_of_quantities", "priced_boq", "rates"):
                    if args.get(key):
                        return True
            return False
        return False


@dataclass
class Evidence:
    """Every record for one turn, and the questions the gate asks of them."""

    records: list[EvidenceRecord] = field(default_factory=list)

    def any_corpus_read(self) -> bool:
        return any(r.reads_corpus for r in self.records)

    def citable_ids(self) -> set[str]:
        ids: set[str] = set()
        for r in self.records:
            if r.citable:
                ids |= r.identifiers()
        return ids

    def user_ids(self) -> set[str]:
        ids: set[str] = set()
        for r in self.records:
            if r.kind == "user":
                ids |= r.identifiers()
        return ids

    def boq_backed(self) -> bool:
        return any(r.carries_boq() for r in self.records)

    def source_names(self, citable_only: bool = False) -> set[str]:
        """Filenames the turn actually read.

        ``citable_only`` narrows to records whose class may back a claim
        about this project. It matters because a filename can CONTAIN a
        contract id: ``DD-2023-118 Contract Template Vol 4.pdf`` is a
        template, and letting its name back a bare ``Source: DD-2023-118``
        is G1 arriving by the back door -- the id would be rescued by the
        very document that must not lend it.
        """
        return {
            r.source_name.lower()
            for r in self.records
            if r.source_name and (r.citable or not citable_only)
        }

    def tool_names(self) -> set[str]:
        return {r.tool for r in self.records if r.kind == "tool_run" and r.tool}


def _enabled() -> bool:
    return os.getenv(FLAG_ENV, "1") not in ("0", "false", "False", "")


def _retrieval_records(rag_sys_msg: dict[str, Any] | None) -> list[EvidenceRecord]:
    """One record per retrieved chunk.

    Per-chunk, never one flat blob: the cost gate learned on 2026-07-14 that a
    single block mixing a rate table with a drawing dimension-table reads as
    rate-semantic as a whole and grounds numbers it should not. The same
    applies to a BOQ claim -- a bundle containing one bill chunk must not make
    every other chunk in it look like a bill.
    """
    content = ((rag_sys_msg or {}).get("content") or "") if rag_sys_msg else ""
    if not content.strip():
        return []
    records: list[EvidenceRecord] = []
    matches = list(_MARKER_RE.finditer(content))
    if not matches:
        # Retrieval happened but not in marker form (a non-agent path passing
        # a flat context). One record, corpus-reading, whole blob.
        return [EvidenceRecord(kind="retrieval", text=content)]
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        attrs = m.group("attrs") or ""
        src_m = _MARKER_SRC_RE.search(attrs)
        cls_m = _MARKER_CLASS_RE.search(attrs)
        records.append(EvidenceRecord(
            kind="retrieval",
            text=content[start:end].strip(),
            source_name=(src_m.group(1).strip() if src_m else m.group("doc")),
            source_class=(cls_m.group(1).lower() if cls_m else "project_corpus"),
        ))
    return records


def _message_records(messages: Iterable[dict[str, Any]] | None) -> list[EvidenceRecord]:
    """Tool-run records (name + the arguments actually passed + the result)
    and the operator's own words."""
    records: list[EvidenceRecord] = []
    pending_args: dict[str, str] = {}
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "assistant":
            for tc in (m.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                tid = tc.get("id") or fn.get("name") or ""
                args = fn.get("arguments")
                if tid:
                    pending_args[str(tid)] = args if isinstance(args, str) else json.dumps(args or {})
        elif role == "tool":
            name = m.get("name") or None
            tid = str(m.get("tool_call_id") or "")
            records.append(EvidenceRecord(
                kind="tool_run",
                text=str(m.get("content") or ""),
                tool=name,
                inputs=pending_args.get(tid, "") or pending_args.get(str(name), ""),
                source_class="tool",
            ))
        elif role == "user":
            records.append(EvidenceRecord(
                kind="user",
                text=str(m.get("content") or ""),
                source_class="user",
            ))
    return records


def build_evidence(
    rag_sys_msg: dict[str, Any] | None,
    messages: Iterable[dict[str, Any]] | None,
) -> Evidence:
    """The turn's evidence objects. This is what a citation is rendered from."""
    return Evidence(records=_retrieval_records(rag_sys_msg) + _message_records(messages))


# A removal leaves a hole. Marking it lets the tidy pass clean up exactly the
# lines that changed -- an answer's markdown tables are full of pipes and
# bullets, and a blanket separator sweep would eat them.
SENTINEL = "\x00"


def _tidy(text: str) -> str:
    """Clean up only the lines a strip actually touched.

    Whole-line context is what tells debris from structure: the same pipe is
    an orphan at the end of a sentence and a column boundary inside a table.
    """
    out: list[str] = []
    for ln in text.split("\n"):
        if SENTINEL not in ln:
            out.append(ln)
            continue
        table_row = bool(_TABLE_ROW_RE.match(ln))
        ln = ln.replace(SENTINEL, "")
        ln = re.sub(r"[ \t]{2,}", " ", ln)
        if table_row:
            # Leave the emptied cell in place; the row keeps its columns.
            out.append(ln.rstrip())
            continue
        ln = re.sub(r"[ \t]*\|[ \t]*$", "", ln)
        ln = re.sub(r"^([ \t]*)\|[ \t]*", r"\1", ln)
        ln = re.sub(r"[ \t]+([.,;:])", r"\1", ln)
        ln = ln.rstrip()
        if not ln.strip(" \t|-*\u2022"):
            ln = ""
        out.append(ln)
    t = "\n".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.rstrip()


def _strip_boq_attributions(text: str, ev: Evidence) -> tuple[str, list[str]]:
    """Remove a "BOQ context: ..." claim when no record carries a bill.

    This is the F2 line. ``generate_wbs`` has no BOQ field to read, and no
    chunk was retrieved, so the claim had nothing behind it at all.
    """
    if ev.boq_backed():
        return text, []
    removed = [m.group(0).strip() for m in _BOQ_ATTRIB_RE.finditer(text)]
    if not removed:
        return text, []
    return _BOQ_ATTRIB_RE.sub(SENTINEL, text), removed


def _strip_paren_ids(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Remove "(DD-2022-175)" when no record names that contract."""
    removed: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if m.group(1).lower() in allowed:
            return m.group(0)
        removed.append(m.group(1))
        return SENTINEL

    return _PAREN_ID_RE.sub(repl, text), removed


def _strip_cued_ids(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Strip "per DD-2022-175" / "as set out in DD-2022-175" in running prose.

    Only reached when NOTHING read the corpus this turn: with no retrieval and
    no corpus-reading tool, an id the operator did not name has no possible
    origin but invention. The cue is required so a standards reference --
    "ISO-9001-2015" matches the contract-id shape exactly -- is never touched.
    The cue word goes with the id; a dangling "per" would read worse than the
    fabrication did.
    """
    removed: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if m.group("id").lower() in allowed:
            return m.group(0)
        removed.append(m.group("id"))
        return SENTINEL

    return _ATTRIB_CUE_ID_RE.sub(repl, text), removed


def _strip_source_lines(text: str, ev: Evidence) -> tuple[str, list[str]]:
    """Drop a Source line whose named sources are all unbacked.

    A line naming something the evidence does have is left alone: the job is
    to remove invention, not to delete correct attributions.
    """
    allowed_ids = ev.citable_ids()
    tools = ev.tool_names()
    removed: list[str] = []

    def repl(m: re.Match[str]) -> str:
        body = m.group("body")
        low = body.lower()
        ids = {i.group(1).lower() for i in _CONTRACT_ID_RE.finditer(body)}
        if ids & allowed_ids:
            return m.group(0)
        # Named a document rather than an id: backed when an evidence source
        # name meets it either way round (the answer may shorten the filename,
        # or quote it in full).
        #
        # When the line DOES name an identifier and none of them are allowed,
        # only a citable record's filename may rescue it. Otherwise a template
        # or reference note whose own name carries the contract id would back
        # the attribution the class exists to refuse.
        names = ev.source_names(citable_only=bool(ids))
        for n in names:
            if n and (n in low or low in n):
                return m.group(0)
        # A line naming the TOOL that ran is a RENDERED citation, not an
        # invention -- and R3 requires exactly that self-declaration from the
        # template scheduler. Never strip it.
        for t in tools:
            if t and t.lower() in low:
                return m.group(0)
        if ev.any_corpus_read() and not ev.source_names() and not ids:
            # Retrieval happened in a form carrying no source names; the gate
            # cannot judge this line, so it does not touch it.
            return m.group(0)
        removed.append(m.group(0).strip())
        return SENTINEL

    return _SOURCE_LINE_RE.sub(repl, text), removed


def gate(
    text: str,
    rag_sys_msg: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None,
) -> str:
    """Strip attributions no evidence record backs; flag the answer when any
    were removed. Content is never rewritten. Never raises -- a gate that can
    break an answer is a gate that gets switched off."""
    try:
        if not _enabled() or not text or not text.strip():
            return text
        ev = build_evidence(rag_sys_msg, messages)
        removed: list[str] = []

        out, r = _strip_boq_attributions(text, ev)
        removed += r
        out, r = _strip_source_lines(out, ev)
        removed += r

        if ev.any_corpus_read():
            out, r = _strip_paren_ids(out, ev.citable_ids())
            removed += r
        else:
            # Nothing read the corpus: every attribution in the answer is
            # unbacked by construction. The operator's own ids still stand.
            allowed = ev.user_ids()
            out, r = _strip_paren_ids(out, allowed)
            removed += r
            out, r = _strip_cued_ids(out, allowed)
            removed += r

        if not removed:
            return text
        _LOG.warning(
            "citation_provenance: removed %d unbacked attribution(s): %s",
            len(removed), removed[:5],
        )
        return _tidy(out) + UNVERIFIED_NOTE
    except Exception:  # noqa: BLE001 -- a gate must never break an answer
        _LOG.exception("citation_provenance failed; passing answer through")
        return text
