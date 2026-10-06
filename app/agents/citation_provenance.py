"""Citation provenance — an attribution the turn cannot back is not shipped.

THE INCIDENT (gate battery ``13b2bf7``, 2026-08-31, question F2). The WBS
answer closed with::

    BOQ context: Bill 03 - Example Works (AB-2001-101)

on a project whose contract is AB-2002-202. Three literal checks, not inference:

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
  the numeric/­retrieval guards' business. "Source: AB-2001-101" is this one's.
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
  its identifiers to a claim about this project's contract, which is the
  template-quoted-as-the-contract shape.
* A figure a calculator actually returned is credited to that calculation
  and the tool's own notes (the inputs it was given, and the note lines it
  emitted). A retrieved chunk that did not produce the figure — a ``Source:``
  line or an inline "chunk N" — is not that credit. A chunk that supplied a
  governing input may still be named for that input. An answer with no
  calculator result is left untouched.
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
# underscore-glued filenames ("AB-2002-202_Vol 1.pdf") must still match.
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
# "Bill 03 - Example Works (AB-2001-101)".
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
_MARKER_PAGE_RE = re.compile(r"\bpage=(\d+)")
_MARKER_LAYER_RE = re.compile(r"\blayer=([A-Za-z_]+)")

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
# about this contract is exactly the template-quoted-as-the-contract defect.
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
    chunk_index: int | None = None
    doc_id: str | None = None
    page: int | None = None
    layer: str | None = None

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
        contract id: ``AB-2002-202 Contract Template Vol 4.pdf`` is a
        template, and letting its name back a bare ``Source: AB-2002-202``
        is the template-as-contract defect arriving by the back door -- the id
        would be rescued by the
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
        page_m = _MARKER_PAGE_RE.search(attrs)
        layer_m = _MARKER_LAYER_RE.search(attrs)
        records.append(EvidenceRecord(
            kind="retrieval",
            text=content[start:end].strip(),
            source_name=(src_m.group(1).strip() if src_m else m.group("doc")),
            source_class=(cls_m.group(1).lower() if cls_m else "project_corpus"),
            chunk_index=int(m.group("chunk")),
            doc_id=m.group("doc"),
            page=(int(page_m.group(1)) if page_m else None),
            layer=(layer_m.group(1).lower() if layer_m else None),
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
    """Remove "(AB-2001-101)" when no record names that contract."""
    removed: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if m.group(1).lower() in allowed:
            return m.group(0)
        removed.append(m.group(1))
        return SENTINEL

    return _PAREN_ID_RE.sub(repl, text), removed


def _strip_cued_ids(text: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Strip "per AB-2001-101" / "as set out in AB-2001-101" in running prose.

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


# A chunk citation ("chunk 40", "file.pdf, chunk 40"). The whole Source
# line is one shape; an inline mention is the other. Both are the credit
# for a figure only when a calculator produced that figure.
_CHUNK_REF_RE = re.compile(r"\bchunks?\s*(\d+)\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w])(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])")
_INLINE_CHUNK_RE = re.compile(
    r"[\[【]\s*sources?\s*:\s*[^\]】,]{1,200}?\s*,\s*chunks?\s*\d+\s*[\]】]"
    r"|\(\s*(?:sources?\s*:\s*)?[^()]{0,200}?\s*chunks?\s*\d+\s*\)"
    r"|(?<![\w.])[\w][\w.+-]{0,160}\.(?:pdf|docx?|xlsx?|txt|md|csv)"
    r"\s*,?\s*chunks?\s*\d+"
    r"|(?:\b(?:see|per|from|source|sources)\s+)?\bchunks?\s*\d+\b",
    re.IGNORECASE,
)
_CALC_INPUT_SKIP = frozenset({
    "calculation", "name", "calculator", "params", "input", "text", "formula",
    "message", "query", "project_id", "conversation_id", "user_id", "action",
})


@dataclass
class _CalculatorCredit:
    """One successful calculator envelope and the figure it actually returned."""

    tool: str
    calculation: str
    notes: list[str]
    inputs: dict[str, Any]
    result_numbers: list[float]


def _parse_dict(raw: str) -> dict | None:
    if not raw or not str(raw).lstrip().startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError) as exc:
        _LOG.debug(
            "citation_provenance: tool payload is not JSON (%s)",
            type(exc).__name__,
        )
        return None
    return obj if isinstance(obj, dict) else None


def _calculation_payload(raw: str) -> dict | None:
    """The ``run_calculation`` envelope, or one wrapped a single level down."""
    obj = _parse_dict(raw)
    if not obj:
        return None
    if obj.get("status") not in (None, "success", "ok"):
        return None
    if obj.get("calculation") and isinstance(obj.get("result"), dict):
        return obj
    inner = obj.get("result")
    if isinstance(inner, dict) and inner.get("status") in (None, "success", "ok"):
        if inner.get("calculation") and isinstance(inner.get("result"), dict):
            return inner
    return None


def _result_numbers(result: dict) -> list[float]:
    """Numeric fields the calculator returned. Notes are prose, not targets."""
    found: list[float] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, bool) or obj is None:
            return
        if isinstance(obj, (int, float)):
            if obj != float("inf") and obj == obj:  # not NaN
                found.append(float(obj))
            return
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key == "notes":
                    continue
                walk(val)
            return
        if isinstance(obj, list):
            for val in obj:
                if not isinstance(val, str):
                    walk(val)

    walk(result)
    return found


def _governing_inputs(raw: str) -> dict[str, Any]:
    obj = _parse_dict(raw) or {}
    params = obj.get("params") if isinstance(obj.get("params"), dict) else None
    if params is None and isinstance(obj.get("input"), dict):
        params = obj["input"]
    if params is None:
        params = obj
    clean: dict[str, Any] = {}
    for key, val in params.items():
        if key in _CALC_INPUT_SKIP or isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            clean[str(key)] = val
        elif isinstance(val, str) and val.strip():
            clean[str(key)] = val.strip()
    return clean


def _calculator_credits(ev: Evidence) -> list[_CalculatorCredit]:
    credits: list[_CalculatorCredit] = []
    for rec in ev.records:
        if rec.kind != "tool_run":
            continue
        payload = _calculation_payload(rec.text)
        if payload is None:
            continue
        result = payload["result"]
        calculation = str(payload.get("calculation") or "").strip()
        numbers = _result_numbers(result)
        if not calculation or not numbers:
            continue
        notes_raw = result.get("notes") or []
        notes = (
            [str(note) for note in notes_raw if str(note).strip()]
            if isinstance(notes_raw, list) else []
        )
        credits.append(_CalculatorCredit(
            tool=rec.tool or "construction_calc",
            calculation=calculation,
            notes=notes,
            inputs=_governing_inputs(rec.inputs),
            result_numbers=numbers,
        ))
    return credits


def _numbers_in(text: str) -> list[float]:
    found: list[float] = []
    for match in _NUMBER_RE.finditer(text or ""):
        try:
            found.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return found


def _close(left: float, right: float) -> bool:
    tol = 1e-3 * max(1.0, abs(left), abs(right))
    if abs(left - right) <= tol:
        return True
    return abs(round(left, 3) - round(right, 3)) <= 1e-9


def _has_number(text: str, targets: list[float]) -> bool:
    if not targets:
        return False
    return any(
        _close(found, target)
        for found in _numbers_in(text)
        for target in targets
    )


def _answer_commits(answer: str, credit: _CalculatorCredit) -> bool:
    return _has_number(answer, credit.result_numbers)


def _fmt_input(val: Any) -> str:
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    if isinstance(val, float):
        return format(val, "g")
    return str(val)


def _record_supplies_input(text: str, inputs: dict[str, Any]) -> bool:
    """True when this chunk's words carry a value the calculator was given."""
    low = (text or "").lower()
    for key, val in inputs.items():
        if isinstance(val, str):
            if val.lower() in low:
                return True
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        label = str(key).replace("_", " ").lower()
        if label not in low and str(key).lower() not in low:
            continue
        if _has_number(text, [float(val)]):
            return True
    return False


def _record_backs_figure(rec: EvidenceRecord, credit: _CalculatorCredit) -> bool:
    text = rec.text or ""
    if _has_number(text, credit.result_numbers):
        return True
    return _record_supplies_input(text, credit.inputs)


def _records_for_citation(citation: str, ev: Evidence) -> list[EvidenceRecord]:
    low = (citation or "").lower()
    indexes = {int(n) for n in _CHUNK_REF_RE.findall(citation or "")}
    named = [
        rec for rec in ev.records
        if rec.kind == "retrieval" and rec.source_name and rec.source_name.lower() in low
    ]
    if named and indexes:
        indexed = [rec for rec in named if rec.chunk_index in indexes]
        if indexed:
            return indexed
    if named:
        return named
    if not indexes:
        return []
    return [
        rec for rec in ev.records
        if rec.kind == "retrieval" and rec.chunk_index in indexes
    ]


def _citation_is_false_chunk_credit(
    citation: str,
    ev: Evidence,
    credits: list[_CalculatorCredit],
    answer: str,
) -> bool:
    """A chunk mention crediting a figure the chunk did not produce.

    Kept when the chunk text contains the figure, or a governing input the
    calculator was actually given. No calculator, or no chunk mention, and
    this class does not apply.
    """
    if not credits or not _CHUNK_REF_RE.search(citation or ""):
        return False
    committed = [c for c in credits if _answer_commits(answer, c)]
    if not committed:
        return False
    pool = _records_for_citation(citation, ev)
    if not pool:
        return True
    for rec in pool:
        for credit in committed:
            if _record_backs_figure(rec, credit):
                return False
    return True


def _fmt_credit(credit: _CalculatorCredit) -> str:
    rendered = ", ".join(
        f"{key}={_fmt_input(val)}" for key, val in credit.inputs.items()
    )
    head = f"Source: {credit.tool} {credit.calculation}"
    if rendered:
        head += f" ({rendered})"
    if credit.notes:
        head += " — " + "; ".join(credit.notes)
    return head


def _credit_already_present(answer: str, credit: _CalculatorCredit) -> bool:
    if credit.calculation not in (answer or ""):
        return False
    if credit.tool and credit.tool not in answer:
        return False
    for note in credit.notes:
        if note not in answer:
            return False
    for key, val in credit.inputs.items():
        if f"{key}={_fmt_input(val)}" not in answer:
            return False
    return True


def _missing_calculator_credit(
    out: str,
    credits: list[_CalculatorCredit],
    answer: str,
) -> str:
    lines: list[str] = []
    for credit in credits:
        if not _answer_commits(answer, credit):
            continue
        if _credit_already_present(out, credit):
            continue
        lines.append(_fmt_credit(credit))
    return "\n".join(lines)


def _strip_inline_chunk_credits(
    text: str,
    ev: Evidence,
    credits: list[_CalculatorCredit],
    answer: str,
) -> tuple[str, list[str]]:
    """Drop an inline chunk credit that is not a whole Source line.

    ``_SOURCE_LINE_RE`` only sees a line that begins with Source. "see chunk
    40" and "(file.pdf chunk 40)" sit in the sentence and used to survive.
    """
    if not credits:
        return text, []
    removed: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if not _citation_is_false_chunk_credit(m.group(0), ev, credits, answer):
            return m.group(0)
        removed.append(m.group(0).strip())
        return SENTINEL

    return _INLINE_CHUNK_RE.sub(repl, text), removed


def _strip_source_lines(
    text: str,
    ev: Evidence,
    credits: list[_CalculatorCredit] | None = None,
    answer: str | None = None,
) -> tuple[str, list[str]]:
    """Drop a Source line whose named sources are all unbacked.

    A line naming something the evidence does have is left alone: the job is
    to remove invention, not to delete correct attributions.

    A filename match is not enough when the line is a chunk credit for a
    figure a calculator returned and that chunk did not produce. The
    calculation's own line (it names the tool) is still kept.
    """
    allowed_ids = ev.citable_ids()
    tools = ev.tool_names()
    credits = credits or []
    judged = text if answer is None else answer
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
        #
        # That rescue does not cover a chunk credit for a calculator figure
        # the chunk did not produce (Dewatering-p2: the proposal chunk was
        # retrieved, and the factor of safety came from the tool).
        names = ev.source_names(citable_only=bool(ids))
        name_hit = False
        for n in names:
            if n and (n in low or low in n):
                name_hit = True
                break
        if name_hit and not _citation_is_false_chunk_credit(body, ev, credits, judged):
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
        credits = _calculator_credits(ev)
        removed: list[str] = []

        out, r = _strip_boq_attributions(text, ev)
        removed += r
        out, r = _strip_source_lines(out, ev, credits, text)
        removed += r
        out, r = _strip_inline_chunk_credits(out, ev, credits, text)
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

        if removed:
            out = _tidy(out)
        credit = _missing_calculator_credit(out, credits, text)
        if not removed and not credit:
            return text
        if removed:
            _LOG.warning(
                "citation_provenance: removed %d unbacked attribution(s): %s",
                len(removed), removed[:5],
            )
        if credit:
            out = out.rstrip() + "\n\n" + credit
        if removed:
            out += UNVERIFIED_NOTE
        return out
    except Exception:  # noqa: BLE001 -- a gate must never break an answer
        _LOG.exception("citation_provenance failed; passing answer through")
        return text


# ── Figure provenance ────────────────────────────────────────────────────────
#
# The same evidence objects, asked one more question: where did each FIGURE in
# the answer come from? Every figure (a number carrying a unit, a currency or a
# percent) gets an entry -- user input, calculator (formula and inputs),
# project document or general knowledge (document and page). A figure with no
# entry is not stated (when enforced): its clause is removed. One mechanism:
# the calculator credit, retrieval records and user words above are what back
# a figure here.

_PROV_FIGURE_RE = re.compile(
    r"(?<![\w.])(?P<pre>(?:SAR|AED|USD|EUR|GBP|QAR|KWD|OMR|BHD|\$|£|€)\s?)?"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?P<post>\s?(?:%|(?:mm|cm|km|m|m2|m²|m3|m³|kg|t|kN|kN/m|kN/m2|kN/m²|MPa|kPa|Pa|"
    r"N/mm2|N/mm²|psi|ksi|days?|weeks?|months?|years?|hours?|hrs?|mins?|kW|kWh|lux|lx|"
    r"°C|L|litres?|liters?|tonnes?|tons?|nos?|sqm|cum)\b))?",
    re.IGNORECASE,
)


def _figure_mentions(text: str) -> list[tuple[re.Match, float]]:
    out: list[tuple[re.Match, float]] = []
    for m in _PROV_FIGURE_RE.finditer(text or ""):
        if not (m.group("pre") or m.group("post")):
            continue  # a bare number (clause 4.2, page 12, a year) is not a figure
        try:
            out.append((m, float(m.group("num").replace(",", ""))))
        except ValueError:
            continue
    return out


def _nums(text: str) -> list[float]:
    out = []
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            continue
    return out


def _match(value: float, pool: Iterable[float]) -> bool:
    return any(_close(value, p) for p in pool)


def _rescaled(values: Iterable[float]) -> set[float]:
    vals = set(values)
    return vals | {v * 1000 for v in vals} | {v / 1000 for v in vals}


def _working(values: set[float]) -> set[float]:
    """Pairwise working on grounded numbers: a substitution line explains the
    calculator's arithmetic; it does not invent a figure."""
    out: set[float] = set()
    vals = sorted(values)[:40]
    for a in vals:
        for b in vals:
            out.add(a * b)
            out.add(a + b)
            out.add(abs(a - b))
            if b:
                out.add(a / b)
    return out


def _entry_for_record(rec: EvidenceRecord, figure: str) -> dict[str, Any]:
    from app.agents.provenance_trail import SOURCE_GENERAL, SOURCE_PROJECT

    gk = (rec.layer or "") == "general_knowledge"
    return {
        "figure": figure, "source": SOURCE_GENERAL if gk else SOURCE_PROJECT,
        "doc_id": rec.doc_id, "doc_name": rec.source_name or None, "page": rec.page,
        "chunk_index": rec.chunk_index,
    }


def figure_provenance(
    text: str,
    rag_sys_msg: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None,
    *,
    enforce: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(answer, provenance)``: every figure credited to its source --
    the user's own words, a calculator run (formula and inputs), or a
    retrieved chunk (layer, document, page). With ``enforce`` a figure that
    matches none of them is removed (its clause); without, the answer is
    returned unchanged and only the record is built. Never raises -- on
    failure the answer passes through with an empty record."""
    from app.agents import provenance_trail as pt

    try:
        if not text or not text.strip():
            return text, []
        ev = build_evidence(rag_sys_msg, messages)
        user_vals: set[float] = set()
        for rec in ev.records:
            if rec.kind == "user":
                user_vals = set(_nums(rec.text))  # the latest operator turn wins
        calc_entries: list[tuple[set[float], dict[str, Any]]] = []
        for credit in _calculator_credits(ev):
            nums = set(credit.result_numbers) | set(_nums(json.dumps(credit.inputs, default=str)))
            calc_entries.append((nums, {"source": pt.SOURCE_CALCULATOR,
                                        "formula": credit.calculation, "inputs": credit.inputs}))
        calc_pool: set[float] = set()
        for nums, _meta in calc_entries:
            calc_pool |= nums
        working = _working(_rescaled(calc_pool | user_vals)) if calc_entries else set()
        retrievals = [r for r in ev.records if r.kind == "retrieval" and r.text]

        entries: list[dict[str, Any]] = []
        bad: list[re.Match] = []
        for m, value in _figure_mentions(text):
            fig = m.group(0).strip()
            entry: dict[str, Any] | None = None
            if _match(value, _rescaled(user_vals)):
                entry = {"figure": fig, "source": pt.SOURCE_USER}
            if entry is None:
                for nums, meta in calc_entries:
                    if _match(value, _rescaled(nums)):
                        entry = {"figure": fig, **meta}
                        break
            if entry is None:
                for rec in retrievals:
                    if _match(value, _nums(rec.text)):
                        entry = _entry_for_record(rec, fig)
                        break
            if entry is None and working and _match(value, working):
                entry = {"figure": fig, "source": pt.SOURCE_CALCULATOR, "working": True}
            if entry is None:
                bad.append(m)
                entries.append({"figure": fig, "source": None})
            else:
                entries.append(entry)
        if not enforce:
            return text, entries
        out = text
        for m in reversed(bad):
            start, end = _clause_span(out, m.start(), m.end())
            out = out[:start].rstrip(" ,;") + out[end:]
        if bad:
            _LOG.info("figure_provenance: removed %d figure(s) with no source", len(bad))
        return out, [e for e in entries if e.get("source")]
    except Exception:  # noqa: BLE001 -- a gate must never break an answer
        _LOG.exception("figure_provenance failed; passing answer through")
        return text, []


def _clause_span(text: str, start: int, end: int) -> tuple[int, int]:
    """The parenthetical around [start, end), else the sentence around it."""
    open_ = text.rfind("(", 0, start)
    close = text.find(")", end)
    if open_ != -1 and close != -1 and ")" not in text[open_:start] and "\n" not in text[open_:close]:
        return open_, close + 1
    left = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start))
    rights = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    right = min(rights) + 1 if rights else len(text)
    return (left + 1 if left != -1 else 0), right
