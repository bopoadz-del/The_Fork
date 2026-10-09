"""The exit check: nothing internal leaves in an answer.

Every answer a user reads, streamed, returned or reopened from history,
passes through :func:`check_text` last, and its Sources rows through
:func:`check_sources`. The rules are structural; none names a project, a
document or a question.

* A citation is resolved to the document's name and page, or removed. A
  citation is any span carrying a retrieval key (``doc_id``, ``chunk``,
  ``src=``, ``score=``), bracketed or not, closed or not, and any
  ``Source:`` mention that carries one. The turn's evidence (the retrieval
  markers the model was given, the passages tools returned, the Sources rows)
  says which document and page a chunk number or document id stands for.
  A citation nothing resolves is taken out.
* Chunk numbers and document ids never appear. A Sources row shows the page
  (``p. N``) or nothing; a row with no document name is not shown.
* A registered formula, tool or parameter id reads as its display name
  (``source_labels.plain_registry_text``). A ``key=value`` whose key is not
  registered reads as words; one whose value is an empty or literal dump
  (``{}``, ``[]``, ``true``, ``null``) goes. Any other code identifier
  (``snake_case``, ``dotted.name``) reads as words.
* A tool's error text copied into the answer is replaced by one plain
  sentence naming the step, when that text carries an internal token. An
  ``error:`` label or an exception name is replaced the same way.
* Refusal wording that names the platform's machinery (retrieved excerpts,
  provided chunks, injected context, tool calls, supplied parameters) reads
  in the user's terms.

Never raises: a check that can break an answer is a check that gets
switched off.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger(__name__)

#: Keys the retrieval marker (``app.core.rag.inject``) writes on every
#: excerpt. ``tests/test_answer_exit_registry.py`` checks this against the
#: marker the injector actually builds, so a new key cannot slip past.
MARKER_KEYS: tuple[str, ...] = ("doc_id", "chunk", "score", "class", "layer", "page", "rev", "src")

#: Stands where text was removed until the line is tidied.
_MARK = "\x00"


# ── The turn's evidence ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Place:
    """One place in one document the turn read."""

    name: str
    doc_id: str = ""
    chunk: int | None = None
    page: int | None = None


@dataclass
class Turn:
    """What this turn read and which tools failed, for the exit check."""

    places: list[Place] = field(default_factory=list)
    #: (step label, error text) for every tool that returned an error.
    tool_errors: list[tuple[str, str]] = field(default_factory=list)


_TURN: contextvars.ContextVar[Turn | None] = contextvars.ContextVar("answer_exit_turn", default=None)


def open_turn() -> Turn:
    """Start a turn's record. Tasks and threads started afterwards share it."""
    turn = Turn()
    _TURN.set(turn)
    return turn


def current_turn() -> Turn | None:
    return _TURN.get()


def reset_turn(token: contextvars.Token) -> None:
    _TURN.reset(token)


def begin_turn() -> tuple[Turn, contextvars.Token]:
    """``open_turn`` for a caller that restores the previous record afterwards."""
    turn = Turn()
    return turn, _TURN.set(turn)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        return int(value)
    return None


def _page(value: Any) -> int | None:
    page = _as_int(value)
    return page if page is not None and page > 0 else None


def _chunk(value: Any) -> int | None:
    return _as_int(value)


def _step_label(tool: str, payload: Any) -> str:
    action = payload.get("action") if isinstance(payload, dict) else None
    from app.lib.source_labels import formula_display_name, tool_display_name

    for name in (action, tool):
        if not isinstance(name, str) or not name.strip():
            continue
        shown = tool_display_name(name) or formula_display_name(name)
        if shown:
            return shown
    for name in (action, tool):
        if isinstance(name, str) and name.strip():
            words = re.sub(r"[_.]+", " ", name).strip()
            return words[:1].upper() + words[1:]
    return ""


def _error_texts(payload: Any) -> list[str]:
    """Error strings an error envelope carries, at any depth."""
    found: list[str] = []
    if isinstance(payload, dict):
        failed = payload.get("status") == "error" or payload.get("ok") is False
        err = payload.get("error")
        if isinstance(err, str) and err.strip() and (failed or "status" not in payload):
            found.append(err.strip())
        elif isinstance(err, dict):
            found.extend(_error_texts({"status": "error", **err}))
        for key in ("result", "data"):
            if isinstance(payload.get(key), dict):
                found.extend(_error_texts(payload[key]))
    return found


def note_tool_result(tool: str, payload: Any, turn: Turn | None = None) -> None:
    """Record a tool's error text so a copy of it in the answer is recognised."""
    turn = turn or _TURN.get()
    if turn is None:
        return
    try:
        for err in _error_texts(payload):
            label = _step_label(tool, payload)
            if (label, err) not in turn.tool_errors:
                turn.tool_errors.append((label, err))
    except Exception:  # noqa: BLE001 -- bookkeeping must never break a tool
        _LOG.warning("answer_exit: could not record a tool error", exc_info=True)


def note_evidence(
    rag_sys_msg: dict[str, Any] | None,
    messages: Iterable[dict[str, Any]] | None,
    turn: Turn | None = None,
) -> None:
    """Record the places this turn read (retrieval markers, tool passages) and
    the tools that failed (tool messages)."""
    turn = turn or _TURN.get()
    if turn is None:
        return
    try:
        from app.agents import citation_provenance as cp

        msgs = list(messages or [])
        evidence = cp.build_evidence(rag_sys_msg, msgs, tool_passages=True)
        for rec in evidence.records:
            if rec.kind != "retrieval" or not (rec.source_name or rec.doc_id):
                continue
            name = rec.source_name if rec.source_name != rec.doc_id else ""
            place = Place(name=_display_name(name), doc_id=str(rec.doc_id or ""),
                          chunk=rec.chunk_index, page=_page(rec.page))
            if place not in turn.places:
                turn.places.append(place)
        for m in msgs:
            if not isinstance(m, dict) or m.get("role") != "tool":
                continue
            try:
                payload = json.loads(m.get("content") or "")
            except (TypeError, ValueError):
                continue
            note_tool_result(str(m.get("name") or ""), payload, turn)
    except Exception:  # noqa: BLE001 -- bookkeeping must never break an answer
        _LOG.warning("answer_exit: could not record the turn's evidence", exc_info=True)


def places_from_sources(rows: Iterable[dict[str, Any]] | None) -> list[Place]:
    places: list[Place] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = _display_name(str(row.get("doc_name") or ""))
        doc_id = str(row.get("doc_id") or "")
        if not (name or doc_id):
            continue
        places.append(Place(name=name, doc_id=doc_id, chunk=_chunk(row.get("chunk_index")),
                            page=_page(row.get("page"))))
    return places


# ── Resolving a citation ─────────────────────────────────────────────────────

_HEX_ID = r"[0-9a-f]{6,}(?:-[0-9a-f]{2,}){0,4}"
_UUID_RE = re.compile(r"(?<![\w-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![\w-])",
                      re.IGNORECASE)
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")
#: A hex token at least this long is an id (stored ids carry 32).
_ID_LENGTH = 24


def _display_name(name: str) -> str:
    """A document name as a user reads it: the base name, no path."""
    name = (name or "").strip().strip("\"'“”‘’`")
    if not name:
        return ""
    parts = [p for p in name.replace("\\", "/").split("/") if p.strip()]
    return (parts[-1] if parts else name).strip()


def _name_key(name: str) -> str:
    low = _display_name(name).lower()
    low = re.sub(r"^(?:…|\.\.\.)\s*", "", low)
    low = re.sub(r"\s*(?:…|\.\.\.)$", "", low)
    for dash in ("‐", "‑", "‒", "–", "—", "−"):
        low = low.replace(dash, "-")
    low = _EXT_RE.sub("", low)
    return re.sub(r"[\s_]+", " ", low).strip(" .,;:-")


def _names_match(a: str, b: str) -> bool:
    ka, kb = _name_key(a), _name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    shorter, longer = sorted((ka, kb), key=len)
    return len(shorter) >= 5 and shorter in longer


def _looks_like_id(token: str) -> bool:
    token = (token or "").strip()
    if _UUID_RE.fullmatch(token):
        return True
    return bool(re.fullmatch(_HEX_ID, token, re.IGNORECASE)) and bool(re.search(r"\d", token))


def _document_name(doc_id: str) -> str:
    """The stored name of a document id, or ''."""
    if not doc_id:
        return ""
    try:
        from app.core import projects

        doc = projects.get_document(doc_id) or {}
    except Exception:  # noqa: BLE001 -- a lookup failure only means "unknown"
        _LOG.debug("answer_exit: document lookup failed for %s", doc_id, exc_info=True)
        return ""
    return _display_name(str(doc.get("original_name") or ""))


class CitationIndex:
    """Which document and page a cited chunk number or document id stands for."""

    def __init__(self, places: Iterable[Place] = (), *, lookup: bool = True) -> None:
        self.places = [p for p in places if p.name or p.doc_id]
        self._lookup = lookup
        self._looked_up: dict[str, str] = {}

    def _by_doc(self, doc: str) -> list[Place]:
        doc = doc.lower()
        out = []
        for p in self.places:
            pid = p.doc_id.lower()
            if pid and (pid == doc or (len(doc) >= 6 and pid.startswith(doc))):
                out.append(p)
        return out

    def _name_for_doc(self, doc: str, places: list[Place]) -> str:
        names = [p.name for p in places if p.name and not _looks_like_id(p.name)]
        if names:
            return max(names, key=len)
        if not self._lookup:
            return ""
        if doc not in self._looked_up:
            self._looked_up[doc] = _document_name(doc)
        return self._looked_up[doc]

    def known_names(self) -> list[str]:
        return [p.name for p in self.places if p.name]

    def is_document_id(self, token: str) -> bool:
        return bool(token) and bool(self._by_doc(token))

    def resolve(
        self,
        *,
        doc: str = "",
        name: str = "",
        chunks: Iterable[int] = (),
        pages: Iterable[int] = (),
    ) -> tuple[str, list[int]] | None:
        """``(document name, pages)`` for a citation, or None when nothing
        backs it. ``pages`` is empty when the document has no page there."""
        chunks = [c for c in chunks if c is not None]
        stated = sorted({p for p in (_page(x) for x in pages) if p})
        if doc and name and not self._by_doc(doc) and not self._name_for_doc(doc, []):
            doc = ""
        cands = self._by_doc(doc) if doc else list(self.places)
        if name:
            named = [p for p in cands if p.name and _names_match(p.name, name)]
            cands = named if (named or not doc) else cands
        matched = [p for p in cands if p.chunk in chunks] if chunks else []
        if chunks and not matched and not doc and not name:
            return None
        pool = matched or cands
        if doc:
            shown = self._name_for_doc(doc, pool)
        else:
            names = {p.name for p in pool if p.name and not _looks_like_id(p.name)}
            if name and not names:
                shown = _display_name(name)
            elif not names:
                return None
            else:
                keys = {_name_key(n) for n in names}
                if len(keys) > 1 and not name:
                    return None
                shown = max(names, key=len)
        if not shown or _looks_like_id(shown):
            return None
        found = sorted({p.page for p in matched if p.page})
        if not found and not chunks and (doc or name):
            # The document is cited without a chunk: its page is known only
            # when the turn read it at one page.
            pages_read = {p.page for p in pool if p.page}
            found = sorted(pages_read) if len(pages_read) == 1 else []
        return shown, (stated or found)


def render_place(name: str, pages: list[int]) -> str:
    if not pages:
        return name
    if len(pages) == 1:
        return f"{name}, p. {pages[0]}"
    return f"{name}, pp. {', '.join(str(p) for p in pages)}"


# ── Citation grammar ─────────────────────────────────────────────────────────

_NUMS = r"\d+(?:\s*(?:,|and|&|[-–—]|to)\s*\d+)*"
_DOC_KEY = r"\bdoc(?:ument)?[ _-]?id\b"
_DOC_TOKEN_RE = re.compile(rf"{_DOC_KEY}\s*[=:#]?\s*(?P<doc>{_HEX_ID})", re.IGNORECASE)
_CHUNK_TOKEN_RE = re.compile(rf"\bchunks?\b(?:[ \t]*index)?[ \t]*[=:#]?[ \t]*#?[ \t]*(?P<nums>{_NUMS})",
                             re.IGNORECASE)
_PAGE_TOKEN_RE = re.compile(rf"(?:\bpages?\b|\bpp?\.)[ \t]*[=:#]?[ \t]*(?P<nums>{_NUMS})", re.IGNORECASE)
_SRC_TOKEN_RE = re.compile(r"\bsrc\s*=\s*(?P<src>.+?)(?=\s+[a-z_]+\s*=|\s*[\]】)]|$)", re.IGNORECASE)
_TELEMETRY_KEYS = tuple(k for k in MARKER_KEYS if k not in ("doc_id", "chunk", "page", "src"))
#: Marker telemetry: a key written ``key=value``, or a score written with a
#: space. "Class A" or "Rev 3" inside a document name is not telemetry.
_TELEMETRY_RE = re.compile(
    rf"\b(?:{'|'.join(_TELEMETRY_KEYS)}|cosine|rank|relevance)\b[ \t]*[=:][ \t]*"
    r"(?:-?\d+(?:\.\d+)?|[A-Za-z_][\w-]*)"
    r"|\b(?:score|cosine|relevance)\b[ \t]+-?\d+(?:\.\d+)?|\bSUPERSEDED\b",
    re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(r"^\s*\**\s*(?:sources?|cited|citations?|ref(?:erence)?s?)\s*\**\s*[:=-]\s*",
                              re.IGNORECASE)
_CUE_RE = re.compile(r"^(?:see|per|from|in|at|cf\.?|ref\.?|refer\s+to|according\s+to|as\s+stated\s+in)\b\s*",
                     re.IGNORECASE)
#: A retrieval key strong enough to make a span a citation.
_CITATION_KEY_RE = re.compile(
    rf"{_DOC_KEY}\s*[=:#]?\s*[0-9a-f]{{4}}|\bchunks?\b(?:[ \t]*index)?[ \t]*[=:#]?[ \t]*#?[ \t]*\d"
    r"|\bsrc\s*=|\bscore\s*[=:]\s*-?\d",
    re.IGNORECASE,
)


def _numbers(blob: str) -> list[int]:
    out: list[int] = []
    for m in re.finditer(r"(\d+)\s*(?:[-–—]|to)\s*(\d+)|(\d+)", blob or ""):
        if m.group(3):
            out.append(int(m.group(3)))
            continue
        lo, hi = int(m.group(1)), int(m.group(2))
        if 0 <= hi - lo <= 50:
            out.extend(range(lo, hi + 1))
        else:
            out.extend((lo, hi))
    return out


@dataclass
class _Cite:
    doc: str = ""
    name: str = ""
    chunks: list[int] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    labelled: bool = False

    @property
    def machine(self) -> bool:
        return bool(self.doc or self.chunks)


def _parse_cite(inner: str, index: CitationIndex, *, labelled: bool = False) -> _Cite:
    """Pull doc id, chunk numbers, pages and a document name out of one citation.
    ``labelled``: the span was introduced as a source, so its remaining text
    is the document's name."""
    cite = _Cite(labelled=labelled)
    rest = inner or ""
    label = _SOURCE_LABEL_RE.match(rest)
    if label:
        cite.labelled = True
        rest = rest[label.end():]
    m = _SRC_TOKEN_RE.search(rest)
    if m:
        cite.name = m.group("src").strip().rstrip(".,;")
        rest = rest[:m.start()] + " " + rest[m.end():]
    for m in _DOC_TOKEN_RE.finditer(rest):
        cite.doc = cite.doc or m.group("doc")
    rest = _DOC_TOKEN_RE.sub(" ", rest)
    for m in _CHUNK_TOKEN_RE.finditer(rest):
        cite.chunks.extend(_numbers(m.group("nums")))
    rest = _CHUNK_TOKEN_RE.sub(" ", rest)
    for m in _PAGE_TOKEN_RE.finditer(rest):
        cite.pages.extend(_numbers(m.group("nums")))
    rest = _PAGE_TOKEN_RE.sub(" ", rest)
    rest = _TELEMETRY_RE.sub(" ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" ,;:.-–—|\"'“”‘’`*()[]【】")
    rest = _CUE_RE.sub("", rest).strip(" ,;:.-–—")
    if not cite.name and rest:
        if _looks_like_id(rest):
            cite.doc = cite.doc or rest
        elif (cite.labelled or _EXT_RE.search(rest)
              or any(_names_match(n, rest) for n in index.known_names())):
            cite.name = rest
    return cite


def _resolve(cite: _Cite, index: CitationIndex) -> str | None:
    hit = index.resolve(doc=cite.doc, name=cite.name, chunks=cite.chunks, pages=cite.pages)
    return render_place(*hit) if hit else None


_OPEN_TO_CLOSE = {"[": "]", "(": ")", "【": "】"}
_BRACKET_OPEN_RE = re.compile(r"[\[(【]")
#: The run of retrieval tokens an unclosed bracket carries.
_UNCLOSED_RUN_RE = re.compile(
    rf"(?:[ \t,;:|]*(?:{_DOC_KEY}\s*[=:#]?\s*{_HEX_ID}"
    rf"|\bchunks?\b(?:[ \t]*index)?[ \t]*[=:#]?[ \t]*#?[ \t]*{_NUMS}"
    rf"|(?:\bpages?\b|\bpp?\.)[ \t]*[=:#]?[ \t]*{_NUMS}"
    r"|\bsrc\s*=\s*[^\n\]】)]+?(?=[ \t]+[a-z_]+\s*=|[.;]?\s*$)"
    rf"|{_TELEMETRY_RE.pattern}|(?:sources?)\s*:))+",
    re.IGNORECASE,
)


def _rewrite_brackets(text: str, index: CitationIndex) -> str:
    """Bracketed citations, closed or not, become ``(Name, p. N)`` or go."""
    out: list[str] = []
    pos = 0
    while True:
        m = _BRACKET_OPEN_RE.search(text, pos)
        if m is None:
            out.append(text[pos:])
            break
        start = m.start()
        opener = m.group(0)
        closer = _OPEN_TO_CLOSE[opener]
        line_end = text.find("\n", start)
        line_end = len(text) if line_end == -1 else line_end
        close = _matching_close(text, start, line_end, opener, closer)
        if close is not None:
            inner = text[start + 1:close]
            end = close + 1
        else:
            run = _UNCLOSED_RUN_RE.match(text, start + 1)
            inner = run.group(0) if run else ""
            end = run.end() if run else start + 1
        if not _CITATION_KEY_RE.search(inner) or len(inner) > 600:
            out.append(text[pos:start + 1])
            pos = start + 1
            continue
        out.append(text[pos:start])
        cite = _parse_cite(inner, index)
        place = _resolve(cite, index)
        if place:
            lead = "Source: " if cite.labelled else ""
            out.append(f"({lead}{place})")
        else:
            out.append(_MARK)
        pos = end
    return "".join(out)


def _matching_close(text: str, start: int, limit: int, opener: str, closer: str) -> int | None:
    depth = 0
    for i in range(start, limit):
        ch = text[i]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


_SOURCE_LINE_RE = re.compile(
    r"(?P<lead>(?:^|(?<=\n))[ \t]*(?:[-*•][ \t]+)?\**[ \t]*|(?<=[\s(]))"
    r"(?P<label>Sources?)[ \t]*\**[ \t]*:[ \t]*\**[ \t]*"
    r"(?P<body>[^\n]*?)(?P<tail>\**[ \t]*(?:\.(?=\s|$)|$|(?=\n)))",
    re.IGNORECASE,
)


def _rewrite_source_mentions(text: str, index: CitationIndex) -> str:
    """``Source: <name>, chunk 0`` style mentions carry a retrieval key; each
    part becomes ``Name, p. N`` or goes. A mention with no key is left alone
    (a calculator credit is one)."""

    def repl(m: re.Match[str]) -> str:
        body = m.group("body")
        if not _CITATION_KEY_RE.search(body or ""):
            return m.group(0)
        kept: list[str] = []
        for part in re.split(r"\s*;\s*", body):
            if not part.strip():
                continue
            cite = _parse_cite(part, index, labelled=True)
            place = _resolve(cite, index) if (cite.machine or cite.name) else None
            if place and place not in kept:
                kept.append(place)
        if not kept:
            return m.group("lead") + _MARK
        return f"{m.group('lead')}{m.group('label')}: {'; '.join(kept)}{m.group('tail')}"

    return _SOURCE_LINE_RE.sub(repl, text)


_CHUNK_TAIL = rf"[ \t]*,?[ \t]*\bchunks?\b(?:[ \t]*index)?[ \t]*[=:#]?[ \t]*#?[ \t]*(?P<nums>{_NUMS})"
_FILE_CHUNK_RE = re.compile(
    rf"(?P<name>(?<![\w./-])[\w][\w.&()'+-]{{0,160}}?\.[A-Za-z0-9]{{2,5}}){_CHUNK_TAIL}",
    re.IGNORECASE,
)
_INLINE_DOC_RE = re.compile(
    rf"(?:\b(?:see|per|from|in)\s+)?{_DOC_KEY}\s*[=:#]?\s*(?P<doc>{_HEX_ID})"
    rf"(?:[ \t]*[,;]?[ \t]*\bchunks?\b(?:[ \t]*index)?[ \t]*[=:#]?[ \t]*#?[ \t]*(?P<nums>{_NUMS}))?",
    re.IGNORECASE,
)
_INLINE_CHUNK_RE = re.compile(
    rf"(?P<cue>\b(?:see|per|from|in|at|of)\s+)?\bchunks?\b(?:[ \t]*index)?[ \t]*[=:#]?[ \t]*#?[ \t]*"
    rf"(?P<nums>{_NUMS})",
    re.IGNORECASE,
)


def _rewrite_inline(text: str, index: CitationIndex) -> str:
    """Retrieval tokens loose in prose: a named file's chunk, a document id,
    a bare chunk number, marker telemetry, a raw id."""

    def file_chunk(m: re.Match[str]) -> str:
        name = m.group("name").strip()
        hit = index.resolve(name=name, chunks=_numbers(m.group("nums")))
        return render_place(*hit) if hit else _display_name(name)

    for known in sorted(set(index.known_names()), key=len, reverse=True):
        stem = _EXT_RE.sub("", known)
        if len(stem) < 5:
            continue
        pattern = rf"(?P<name>{re.escape(stem)}(?:\.[A-Za-z0-9]{{2,5}})?){_CHUNK_TAIL}"
        text = re.sub(pattern, file_chunk, text, flags=re.IGNORECASE)
    text = _FILE_CHUNK_RE.sub(file_chunk, text)

    def doc_ref(m: re.Match[str]) -> str:
        hit = index.resolve(doc=m.group("doc"), chunks=_numbers(m.group("nums") or ""))
        return render_place(*hit) if hit else _MARK

    text = _INLINE_DOC_RE.sub(doc_ref, text)

    def chunk_ref(m: re.Match[str]) -> str:
        hit = index.resolve(chunks=_numbers(m.group("nums")))
        if not hit:
            return _MARK
        return (m.group("cue") or "") + render_place(*hit)

    text = _INLINE_CHUNK_RE.sub(chunk_ref, text)
    text = re.sub(
        rf"\b(?:{'|'.join(_TELEMETRY_KEYS)}|src)\s*=\s*[^\s,;)\]】]+", _MARK, text, flags=re.IGNORECASE,
    )

    def raw_id(m: re.Match[str]) -> str:
        hit = index.resolve(doc=m.group(0))
        return hit[0] if hit else _MARK

    text = _UUID_RE.sub(raw_id, text)

    def known_id(m: re.Match[str]) -> str:
        token = m.group(0)
        if not (_looks_like_id(token) and re.search(r"[a-f]", token, re.IGNORECASE)):
            return token
        # A short hex token may be a code or a colour; one of id length is an
        # id whether or not this turn read it.
        if not (index.is_document_id(token) or len(token.replace("-", "")) >= _ID_LENGTH):
            return token
        hit = index.resolve(doc=token)
        return hit[0] if hit else _MARK

    return re.sub(rf"(?<![\w./-]){_HEX_ID}(?![\w/-])(?!\.\w)", known_id, text, flags=re.IGNORECASE)


# ── Identifiers and dumps ────────────────────────────────────────────────────

#: A code name: lowercase segments joined by ``_`` or ``.``. It is internal
#: only when it has an underscore (dotted alone is also a web domain) and one
#: segment is a word of three letters (``f_ck`` is an engineering symbol).
_IDENT = r"[a-z][a-z0-9]*(?:[_.][a-z0-9]+)+"
_LITERAL = (r"\{\s*\}|\[\s*\]|\{[^{}\n]{0,240}\}|\[[^\[\]\n]{0,240}\]|"
            r"true\b|false\b|null\b|none\b|nan\b|undefined\b")
_DUMP_RE = re.compile(
    rf"(?<![\w\\./@-])`?(?P<key>{_IDENT}|[a-z]{{3,}})`?"
    rf"(?:[ \t]*(?P<sep>=|:)[ \t]*|[ \t]+)(?P<val>{_LITERAL})",
    re.IGNORECASE,
)
_ASSIGN_RE = re.compile(rf"(?<![\w\\./@-])(?P<key>{_IDENT})=(?P<val>[^\s,;)\]]+)")
_IDENT_RE = re.compile(rf"(?<![\w\\./@#-])(?P<id>{_IDENT})(?![\w/@-])(?!\.[A-Za-z0-9])")
_PROTECTED_RE = re.compile(
    r"https?://\S+|www\.\S+|\S+@\S+\.\w+|```.*?```"
    r"|(?<![\w-])[\w][\w.&()'+-]{0,160}?\.(?:pdf|docx?|xlsx?|xlsm|csv|txt|md|pptx?|dwg|dxf|ifc|xer|xml|json|zip|rar|png|jpe?g)\b",
    re.IGNORECASE | re.DOTALL,
)


def _is_word_ident(token: str, *, dotted: bool = False) -> bool:
    if "_" not in token and not dotted:
        return False
    return any(len(seg) >= 3 and seg.isalpha() for seg in re.split(r"[_.]", token))


def _words(token: str) -> str:
    return re.sub(r"[_.]+", " ", token).strip()


def _protect(text: str, index: CitationIndex) -> tuple[str, list[str]]:
    """Hide URLs, file names and code fences behind placeholders."""
    held: list[str] = []

    def hold(m: re.Match[str]) -> str:
        held.append(m.group(0))
        return f"\x01{len(held) - 1}\x02"

    text = _PROTECTED_RE.sub(hold, text)
    for name in sorted({n for n in index.known_names() if "_" in n}, key=len, reverse=True):
        stem = _EXT_RE.sub("", name)
        if len(stem) >= 5:
            text = re.sub(re.escape(stem), lambda m: hold(m), text)
    return text, held


def _restore(text: str, held: list[str]) -> str:
    return re.sub(r"\x01(\d+)\x02", lambda m: held[int(m.group(1))], text)


def _drop_dumps(text: str) -> str:
    def dump(m: re.Match[str]) -> str:
        key = m.group("key")
        plain_key = "_" not in key and "." not in key
        if plain_key and m.group("sep") != "=":
            return m.group(0)
        if plain_key and m.group("val").lower() in ("none", "nan", "undefined"):
            return m.group(0)
        if not plain_key and not _is_word_ident(key, dotted=True):
            return m.group(0)
        val = m.group("val")
        if val.startswith(("{", "[")) and len(val) > 2 and not re.search(r"[\"':]|^\[\s*\d", val):
            return m.group(0)
        return _MARK

    return _DUMP_RE.sub(dump, text)


def _plain_identifiers(text: str) -> str:
    from app.lib.source_labels import plain_registry_text

    text = plain_registry_text(_drop_dumps(text))

    def assign(m: re.Match[str]) -> str:
        key = m.group("key")
        if not _is_word_ident(key):
            return m.group(0)
        return f"{_words(key)} {m.group('val')}"

    text = _ASSIGN_RE.sub(assign, text)

    def ident(m: re.Match[str]) -> str:
        token = m.group("id")
        return _words(token) if _is_word_ident(token) else token

    return _IDENT_RE.sub(ident, text)


# ── Raw tool errors ──────────────────────────────────────────────────────────

_INTERNAL_TOKEN_RE = re.compile(
    rf"(?<![\w./-])(?:{_IDENT})(?![\w/-])|[{{}}\[\]]|\w=\S|\b\w+(?:Error|Exception)\b|[\\/][\w.-]+[\\/]",
)
_STRUCTURAL_ERROR_RE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]+)?\**[ \t]*(?:error|exception|traceback)\b[ \t]*\**[ \t]*[:=]"
    r"|\bTraceback \(most recent call last\)"
    r"|\b[A-Z][A-Za-z]*(?:Error|Exception)\b[ \t]*[:(]"
    r"|[\"']?status[\"']?[ \t]*[:=][ \t]*[\"']?error\b",
    re.IGNORECASE | re.MULTILINE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])[ \t]+(?=\S)")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower().rstrip(".")


def _error_sentence(label: str, err: str) -> str:
    from app.lib.source_labels import _registry_maps, parameter_words

    _displays, params, _units = _registry_maps()
    needs = []
    for token in re.findall(rf"(?<![\w.]){_IDENT}(?![\w])", err or "", re.IGNORECASE):
        low = token.lower()
        if low in params:
            words = parameter_words(low, params[low])
            if words and words not in needs:
                needs.append(words)
    head = f"{label} did not return a result." if label else "One step of this request did not return a result."
    if needs:
        head += f" It needs: {', '.join(needs)}."
    return head


def _replace_tool_errors(text: str, turn: Turn | None) -> str:
    errors = [(label, err) for label, err in (turn.tool_errors if turn else [])
              if _INTERNAL_TOKEN_RE.search(err)]
    lines = []
    said: set[str] = set()
    for line in text.split("\n"):
        parts = _SENTENCE_SPLIT_RE.split(line)
        kept: list[str] = []
        for part in parts:
            body = _norm(part)
            plain = None
            for label, err in errors:
                norm_err = _norm(err)
                pieces = [_norm(p) for p in _SENTENCE_SPLIT_RE.split(err) if len(_norm(p)) >= 16]
                if len(body) >= 16 and (body in norm_err or any(p in body for p in pieces)):
                    plain = _error_sentence(label, err)
                    whole = re.search(r"\s+".join(map(re.escape, err.split())), part)
                    if whole and part[whole.end():].strip(" .") and plain not in said:
                        # The error ran into the next sentence: only its own
                        # words are replaced.
                        said.add(plain)
                        part = f"{part[:whole.start()]}{plain} {part[whole.end():].lstrip(' .')}".strip()
                        plain = None
                    break
            if plain is None and _STRUCTURAL_ERROR_RE.search(part):
                plain = _error_sentence("", "")
            if plain is None:
                kept.append(part)
                continue
            if plain not in said:
                said.add(plain)
                lead = re.match(r"[ \t]*(?:[-*•][ \t]+)?", part)
                kept.append((lead.group(0) if lead else "") + plain)
        lines.append(" ".join(kept) if kept else "")
    return "\n".join(lines)


# ── Machinery wording ────────────────────────────────────────────────────────

_MACHINE_ADJ = r"(?:retrieved|provided|supplied|injected|returned|rag|search|retrieval|indexed)"
_MACHINE_PHRASE_RE = re.compile(
    r"(?P<det>\b(?:the|a|an|any|no|this|that|these|those|each|every|some|all(?:\s+of)?(?:\s+the)?)\s+)?"
    rf"(?P<adjs>(?:(?:{_MACHINE_ADJ}|available|relevant|above|given|attached|top)\s+){{0,3}})"
    r"(?P<mid>(?:(?:document|project|corpus|context|knowledge[- ]base)\s+){0,2})"
    r"(?P<noun>excerpts?|chunks?|snippets?|passages?|context|search\s+results?|retrieval\s+results?)\b"
    rf"(?P<post>\s+{_MACHINE_ADJ}\b(?!\s+(?:to|by|in)\b))?",
    re.IGNORECASE,
)
_STRONG_NOUNS = ("excerpt", "chunk", "snippet")
_TOOL_PHRASE_RE = re.compile(
    r"\b(?:tool|function)\s+(?P<what>calls?|results?|outputs?|responses?)\b", re.IGNORECASE,
)
_PARAM_PHRASE_RE = re.compile(
    rf"\b(?P<adj>{_MACHINE_ADJ}|passed|required|missing|given)\s+parameters?\b"
    r"|\bparameters?\s+(?P<post>supplied|provided|passed|given)\b",
    re.IGNORECASE,
)


def _cap_like(original: str, new: str) -> str:
    return new[:1].upper() + new[1:] if original[:1].isupper() else new


def _machine_wording(text: str) -> str:
    def phrase(m: re.Match[str]) -> str:
        noun = m.group("noun").lower()
        adjs = (m.group("adjs") or "").lower()
        machine = bool(re.search(rf"\b{_MACHINE_ADJ}\b", adjs) or m.group("post"))
        det = (m.group("det") or "").strip().lower()
        if noun.startswith(("search", "retrieval")):
            machine = True
        if (m.group("mid") or "").strip() and noun.startswith(_STRONG_NOUNS):
            machine = True
        # "the excerpts" is the context's own label for what it holds;
        # "concrete chunks" or "the chunks of slab" is about the works.
        strong = (noun.startswith(_STRONG_NOUNS) and bool(det)
                  and not re.match(r"\s+of\b", m.string[m.end():]))
        if not (strong or machine):
            return m.group(0)
        plural = noun.endswith("s") or noun.startswith(("search", "retrieval"))
        if noun == "context":
            new = f"{det if det in ('no', 'any', 'all the') else 'the'} project material"
        elif plural:
            keep = det if det in ("no", "any", "some", "all", "all the", "all of the") else "the"
            new = f"{keep} project document sections"
        else:
            keep = {"an": "a", "": "a", "these": "the", "those": "the"}.get(det, det)
            new = f"{keep} project document section"
        return _cap_like(m.group(0), new)

    text = _MACHINE_PHRASE_RE.sub(phrase, text)
    def tool_phrase(m: re.Match[str]) -> str:
        what = m.group("what").lower()
        noun = "platform step" if what.startswith("call") else "platform result"
        return _cap_like(m.group(0), noun + ("s" if what.endswith("s") else ""))

    text = _TOOL_PHRASE_RE.sub(tool_phrase, text)
    return _PARAM_PHRASE_RE.sub(
        lambda m: _cap_like(m.group(0), (f"{m.group('adj')} inputs" if m.group("adj") else
                                         f"inputs {m.group('post')}")),
        text,
    )


# ── The check ────────────────────────────────────────────────────────────────

_JOINED_MARKS_RE = re.compile(rf"{_MARK}(?:[ \t]*(?:[,;&]|\band\b|\bor\b)?[ \t]*{_MARK})+", re.IGNORECASE)
#: A sentence that opens with removed text and runs on in lower case: its
#: subject was the internal text, so what is left says nothing to a reader.
_HEADLESS_SENTENCE_RE = re.compile(
    rf"(?:(?<=^)|(?<=\n)|(?<=[.!?:][ \t]))[ \t]*{_MARK}[ \t]*(?:[,;:]?[ \t]*[a-z][^.!?\n]*)?[.!?]?[ \t]*(?=\S|$)",
)


def _tidy(text: str) -> str:
    if _MARK not in text:
        return text
    from app.agents.citation_provenance import SENTINEL
    from app.agents.citation_provenance import _tidy as tidy

    text = _JOINED_MARKS_RE.sub(_MARK, text)
    text = _HEADLESS_SENTENCE_RE.sub(_MARK, text)
    return tidy(text.replace(_MARK, SENTINEL))


def build_index(
    turn: Turn | None = None,
    sources: Iterable[dict[str, Any]] | None = None,
    *,
    lookup: bool = True,
) -> CitationIndex:
    places = list(turn.places) if turn else []
    places.extend(places_from_sources(sources))
    return CitationIndex(places, lookup=lookup)


def check_text(
    text: str,
    *,
    turn: Turn | None = None,
    sources: Iterable[dict[str, Any]] | None = None,
    index: CitationIndex | None = None,
) -> str:
    """The answer as it may leave: citations resolved to name and page or
    removed, no chunk numbers, ids, dumps, raw tool errors or machinery words."""
    if not isinstance(text, str) or not text.strip():
        return text
    try:
        turn = turn if turn is not None else _TURN.get()
        index = index or build_index(turn, sources)
        trailing = text[len(text.rstrip()):]
        out = _rewrite_source_mentions(text, index)
        out = _rewrite_brackets(out, index)
        out = _rewrite_inline(out, index)
        out = _replace_tool_errors(out, turn)
        out, held = _protect(out, index)
        out = _plain_identifiers(out)
        out = _machine_wording(out)
        out = _restore(out, held)
        out = _tidy(out)
        if out == text:
            return text
        out = re.sub(r"[ \t]+\n", "\n", out)
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
        return out + trailing if out else out
    except Exception:  # noqa: BLE001 -- the exit must never break an answer
        _LOG.exception("answer_exit: check failed; answer passed through")
        return text


def remove_dumps(text: str) -> str:
    """``text`` without ``key=literal`` dumps (``delta_ok=false``,
    ``summary {}``). For a step that rewrites ``key=value`` as words before
    the exit: a dump read as words is still a dump."""
    if not isinstance(text, str) or not text:
        return text
    try:
        held_text, held = _protect(text, CitationIndex(lookup=False))
        out = _drop_dumps(held_text)
        if out == held_text:
            return text
        return _tidy(_restore(out, held))
    except Exception:  # noqa: BLE001 -- the exit must never break an answer
        _LOG.exception("answer_exit: dump removal failed; text passed through")
        return text


def check_text_or_fallback(text: str, **kwargs: Any) -> str:
    """``check_text`` for a whole answer: one that was nothing but internals
    becomes the platform's empty-turn reply, never an empty bubble (the client
    would show the raw streamed tokens in its place)."""
    checked = check_text(text, **kwargs)
    if isinstance(text, str) and text.strip() and not (checked or "").strip():
        from app.agents.runtime import _EMPTY_RESPONSE_FALLBACK

        return _EMPTY_RESPONSE_FALLBACK
    return checked


def check_sources(
    rows: list[dict[str, Any]] | None,
    *,
    turn: Turn | None = None,
    index: CitationIndex | None = None,
) -> list[dict[str, Any]] | None:
    """Sources rows as they may leave: a document name, and the page or nothing."""
    if not isinstance(rows, list):
        return rows
    try:
        turn = turn if turn is not None else _TURN.get()
        index = index or build_index(turn, rows)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            fixed = dict(row)
            name = _display_name(str(row.get("doc_name") or ""))
            doc_id = str(row.get("doc_id") or "")
            if not name or _looks_like_id(name):
                hit = index.resolve(doc=doc_id or name) if (doc_id or name) else None
                name = hit[0] if hit else ""
            if not name:
                _LOG.info("answer_exit: Sources row with no document name dropped (doc %s)", doc_id)
                continue
            fixed["doc_name"] = name
            page = _page(row.get("page"))
            section = str(row.get("page_or_section") or "")
            if page:
                fixed["page_or_section"] = f"p. {page}"
            elif _CITATION_KEY_RE.search(section) or re.search(r"\bchunk\b|#", section, re.IGNORECASE):
                fixed["page_or_section"] = ""
            elif re.fullmatch(r"\s*\d+\s*", section):
                fixed["page_or_section"] = f"p. {int(section)}"
            else:
                fixed["page_or_section"] = check_text(section, turn=turn, index=index) if section else section
            for key in ("layer_label", "source_class_label"):
                if isinstance(fixed.get(key), str):
                    fixed[key] = check_text(fixed[key], turn=turn, index=index)
            out.append(fixed)
        return out
    except Exception:  # noqa: BLE001 -- the exit must never break an answer
        _LOG.exception("answer_exit: Sources check failed; rows passed through")
        return rows


def check_result(result: Any, turn: Turn | None = None) -> Any:
    """A whole-turn result (``answer`` and ``sources``) as it may leave."""
    if not isinstance(result, dict):
        return result
    fixed = dict(result)
    rows = fixed.get("sources")
    index = build_index(turn, rows if isinstance(rows, list) else None)
    if isinstance(fixed.get("answer"), str):
        fixed["answer"] = check_text_or_fallback(fixed["answer"], turn=turn, index=index)
    if isinstance(rows, list):
        fixed["sources"] = check_sources(rows, turn=turn, index=index)
    return fixed


def check_end_event(event: dict[str, Any], streamed: str = "", turn: Turn | None = None) -> dict[str, Any]:
    """The terminal stream event as it may leave. ``streamed`` is the text the
    tokens carried; the client shows ``content`` in its place, so an event
    without content gets the checked streamed text."""
    if not isinstance(event, dict) or event.get("type") != "end":
        return event
    turn = turn if turn is not None else _TURN.get()
    rows = event.get("sources")
    index = build_index(turn, rows if isinstance(rows, list) else None)
    fixed = dict(event)
    content = event.get("content")
    if not isinstance(content, str) or not content.strip():
        content = streamed
    if isinstance(content, str) and content.strip():
        checked = check_text_or_fallback(content, turn=turn, index=index)
        if checked != content or "content" in event or streamed:
            fixed["content"] = checked
    if isinstance(rows, list):
        fixed["sources"] = check_sources(rows, turn=turn, index=index)
    return fixed
