"""Need plan: what a question needs, and where code fetches each piece from.

Before an answer is written, one structured completion lists every piece of
information the question needs and its KIND:

* ``user_value``   -- a value the user typed in the question itself;
* ``project_fact`` -- something the project's own documents state;
* ``code_rule``    -- something a code, standard or reference work states;
* ``computed``     -- a number a registered calculator produces from inputs;
* ``missing_input``-- something the question needs that nobody has given.

The model only DECLARES the needs. Code fetches each one from its home and
records where it came from:

* precedence for any input, enforced here: a value the user typed wins (and
  only if it is literally in the question); else the project documents; else
  general knowledge, marked as a general assumption. When the project and
  general knowledge disagree, the project value is used and the difference is
  recorded;
* a value read from a document counts only if its quoted words are really in
  the retrieved chunk and the number is in the quote;
* a computed number comes only from ``run_calculation`` on the resolved
  inputs; the calculator's own result is the figure;
* an input no layer can supply stays missing, and the answer asks for it.

Routing is by the KIND of information, never by topic words. The kinds, the
calculator registry and the retrieval layers are the only vocabulary here.
Kill switch: ``NEED_PLAN=0``.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

KINDS = ("user_value", "project_fact", "code_rule", "computed", "missing_input")

#: Source kinds a provenance entry can carry.
SOURCE_USER = "user_input"
SOURCE_PROJECT = "project_document"
SOURCE_GENERAL = "general_knowledge"
SOURCE_CALCULATOR = "calculator"

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def enabled() -> bool:
    return os.getenv("NEED_PLAN", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class Need:
    id: str
    kind: str
    what: str
    value: Optional[str] = None
    unit: Optional[str] = None
    calculation: Optional[str] = None
    inputs: Dict[str, str] = field(default_factory=dict)


@dataclass
class Fact:
    """One resolved piece of information and where it came from."""

    need_id: str
    what: str
    source: str
    value: Optional[str] = None
    unit: Optional[str] = None
    doc_id: Optional[str] = None
    doc_name: Optional[str] = None
    page: Optional[int] = None
    chunk_id: Optional[str] = None
    quote: Optional[str] = None
    formula: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    note: Optional[str] = None
    #: The other layer's value when the two disagree (stated, not used).
    alternative: Optional[Dict[str, Any]] = None


@dataclass
class NeedContext:
    needs: List[Need] = field(default_factory=list)
    facts: List[Fact] = field(default_factory=list)
    missing: List[Need] = field(default_factory=list)
    question: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "needs": [asdict(n) for n in self.needs],
            "facts": [asdict(f) for f in self.facts],
            "missing": [asdict(n) for n in self.missing],
        }

    def calculator_facts(self) -> List[Fact]:
        return [f for f in self.facts if f.source == SOURCE_CALCULATOR]


#: The need context of the turn being answered (set by the runtime, read by
#: the provenance gate and the stream / message store).
CURRENT: contextvars.ContextVar[Optional[NeedContext]] = contextvars.ContextVar(
    "need_plan_current", default=None,
)


# ── the plan ──────────────────────────────────────────────────────────────

PLANNER_SYSTEM = (
    "You plan how a question will be answered. Do NOT answer it. List every "
    "piece of information the answer needs, each with exactly one kind:\n"
    "- user_value: a value the user typed in the question (copy it exactly, "
    "with its unit);\n"
    "- project_fact: something only the user's project documents can state "
    "(their contract, specification, drawings, schedule, bill);\n"
    "- code_rule: something a published code, standard or reference work "
    "states;\n"
    "- computed: a number obtained by running one of the listed calculators "
    "on other needs (give the calculator name and map each of its parameters "
    "to a need id or to a literal the user typed);\n"
    "- missing_input: something the answer needs that the user did not give "
    "and documents are unlikely to state.\n"
    "Return JSON only: {\"needs\": [{\"id\": \"n1\", \"kind\": \"...\", "
    "\"what\": \"short description\", \"value\": \"only for user_value\", "
    "\"unit\": \"...\", \"calculation\": \"only for computed\", "
    "\"inputs\": {\"param\": \"n2\"}}]}. Use as few needs as the question "
    "really requires. If the question asks for no number, list no computed "
    "need."
)


async def make_plan(
    question: str,
    *,
    calculators: List[str],
    complete_json: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
) -> List[Need]:
    """Ask for the structured plan. Returns [] when no plan can be parsed."""
    if complete_json is None:
        from app.core.llm_client import complete_json as _cj
        complete_json = _cj
    user = (
        "Calculators available (name only):\n" + ", ".join(sorted(calculators))
        + "\n\nQuestion:\n" + (question or "")
    )
    try:
        raw = await complete_json(PLANNER_SYSTEM, user, max_tokens=700)
    except Exception:  # noqa: BLE001 — no plan means the old path answers
        logger.warning("need_plan: planner call failed", exc_info=True)
        return []
    return parse_plan(raw, calculators)


def parse_plan(raw: Any, calculators: List[str]) -> List[Need]:
    """Keep only well-formed needs; drop unknown kinds and unknown calculators."""
    out: List[Need] = []
    seen: set = set()
    known = set(calculators or [])
    for item in (raw or {}).get("needs") or []:
        if not isinstance(item, dict):
            continue
        nid = str(item.get("id") or f"n{len(out) + 1}").strip()
        kind = str(item.get("kind") or "").strip()
        if kind not in KINDS or nid in seen:
            continue
        calc = (item.get("calculation") or None)
        if kind == "computed" and calc not in known:
            # A computed need with no real calculator behind it cannot be
            # computed; it is a missing input until one is named.
            kind, calc = "missing_input", None
        inputs = item.get("inputs") if isinstance(item.get("inputs"), dict) else {}
        out.append(Need(
            id=nid, kind=kind, what=str(item.get("what") or "").strip(),
            value=(str(item["value"]).strip() if item.get("value") not in (None, "") else None),
            unit=(str(item["unit"]).strip() if item.get("unit") else None),
            calculation=calc,
            inputs={str(k): str(v) for k, v in inputs.items()},
        ))
        seen.add(nid)
    return out


# ── fetching ──────────────────────────────────────────────────────────────

def numbers_in(text: str) -> List[float]:
    out: List[float] = []
    for tok in _NUM_RE.findall(text or ""):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            continue
    return out


def _first_number(text: str) -> Optional[float]:
    nums = numbers_in(text)
    return nums[0] if nums else None


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, abs(b) * 1e-6)


def user_typed(value: Optional[str], question: str) -> bool:
    """A user value counts only when its number is literally in the question."""
    v = _first_number(value or "")
    if v is None:
        return bool(value) and value.strip().lower() in (question or "").lower()
    return any(_same(v, q) for q in numbers_in(question))


@dataclass
class Found:
    """A value read from one retrieved chunk, with its proof."""

    value: str
    unit: Optional[str]
    quote: str
    doc_id: Optional[str]
    doc_name: Optional[str]
    page: Optional[int]
    chunk_id: Optional[str]


Fetch = Callable[[str], List[Any]]  # what -> chunks (layer already applied)
Extract = Callable[[str, List[Any]], Awaitable[Dict[str, Any]]]
RunCalc = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def verified(found: Dict[str, Any], chunks: List[Any]) -> Optional[Found]:
    """Accept an extracted value only if its quote is in the chunk it names and
    the value's number is in the quote. Anything else is not evidence."""
    if not isinstance(found, dict):
        return None
    try:
        idx = int(found.get("excerpt"))
    except (TypeError, ValueError):
        logger.info("need_plan: extraction named no usable excerpt: %r", found.get("excerpt"))
        return None
    if idx < 0 or idx >= len(chunks):
        return None
    chunk = chunks[idx]
    quote = " ".join(str(found.get("quote") or "").split())
    text = " ".join(str(getattr(chunk, "text", "") or "").split())
    if not quote or quote.lower() not in text.lower():
        return None
    value = str(found.get("value") or "").strip()
    v = _first_number(value)
    if v is not None and not any(_same(v, q) for q in numbers_in(quote)):
        return None
    if v is None and value.lower() not in quote.lower():
        return None
    return Found(
        value=value, unit=(found.get("unit") or None), quote=quote,
        doc_id=getattr(chunk, "doc_id", None),
        doc_name=(getattr(chunk, "source_name", None) or None),
        page=getattr(chunk, "page", None),
        chunk_id=getattr(chunk, "chunk_id", None),
    )


EXTRACT_SYSTEM = (
    "You read numbered excerpts and report what they state about one item. "
    "Return JSON only: {\"excerpt\": <index>, \"value\": \"...\", \"unit\": "
    "\"...\", \"quote\": \"the exact words from that excerpt that state it\"} "
    "or {} when no excerpt states it. Never compute, never infer."
)


async def default_extract(what: str, chunks: List[Any]) -> Dict[str, Any]:
    from app.core.llm_client import complete_json

    body = "\n\n".join(
        f"[{i}] {' '.join(str(getattr(c, 'text', '') or '').split())[:1200]}"
        for i, c in enumerate(chunks)
    )
    try:
        return await complete_json(
            EXTRACT_SYSTEM, f"Item: {what}\n\nExcerpts:\n{body}", max_tokens=300,
        )
    except Exception:  # noqa: BLE001 — an unread value is simply not found
        logger.warning("need_plan: extraction call failed", exc_info=True)
        return {}


async def _read(what: str, fetch: Fetch, extract: Extract) -> Optional[Found]:
    try:
        got = fetch(what)
        if hasattr(got, "__await__"):
            got = await got  # type: ignore[misc]
        chunks = list(got or [])[:6]
    except Exception:  # noqa: BLE001 — a failed fetch is a silent layer
        logger.warning("need_plan: fetch failed for %r", what, exc_info=True)
        return None
    if not chunks:
        return None
    return verified(await extract(what, chunks), chunks)


def _fact_from(need: Need, found: Found, source: str, note: Optional[str] = None) -> Fact:
    return Fact(
        need_id=need.id, what=need.what, source=source, value=found.value,
        unit=found.unit or need.unit, doc_id=found.doc_id, doc_name=found.doc_name,
        page=found.page, chunk_id=found.chunk_id, quote=found.quote, note=note,
    )


async def resolve(
    needs: List[Need],
    question: str,
    *,
    fetch_project: Fetch,
    fetch_general: Fetch,
    extract: Extract = default_extract,
    run_calc: Optional[RunCalc] = None,
) -> NeedContext:
    """Fetch every need from its home, in code, with precedence enforced."""
    if run_calc is None:
        from app.lib.construction_formulas import run_calculation as run_calc  # type: ignore[assignment]
    ctx = NeedContext(needs=list(needs), question=question)
    by_id: Dict[str, Fact] = {}

    for need in needs:
        if need.kind == "computed":
            continue
        if need.kind == "user_value" and user_typed(need.value, question):
            fact = Fact(need_id=need.id, what=need.what, source=SOURCE_USER,
                        value=need.value, unit=need.unit)
            ctx.facts.append(fact)
            by_id[need.id] = fact
            continue
        if need.kind == "code_rule":
            found = await _read(need.what, fetch_general, extract)
            if found:
                fact = _fact_from(need, found, SOURCE_GENERAL)
                ctx.facts.append(fact)
                by_id[need.id] = fact
            else:
                ctx.missing.append(need)
            continue
        # project_fact, missing_input, and a user_value the user did not
        # actually type: project first, else general knowledge as a marked
        # assumption; a disagreement between the two is recorded.
        project = await _read(need.what, fetch_project, extract)
        general = await _read(need.what, fetch_general, extract)
        if project:
            note = None
            if general and _values_differ(project.value, general.value):
                note = (f"general knowledge gives {general.value}"
                        + (f" {general.unit}" if general.unit else "")
                        + (f" ({general.doc_name}" + (f", p. {general.page}" if general.page else "") + ")"
                           if general.doc_name else "")
                        + "; the project value is used")
            fact = _fact_from(need, project, SOURCE_PROJECT, note)
            if note and general:
                fact.alternative = {"source": SOURCE_GENERAL, "value": general.value,
                                    "unit": general.unit, "doc_id": general.doc_id,
                                    "doc_name": general.doc_name, "page": general.page}
        elif general:
            fact = _fact_from(need, general, SOURCE_GENERAL,
                              "general assumption: the project documents do not state this")
        else:
            ctx.missing.append(need)
            continue
        ctx.facts.append(fact)
        by_id[need.id] = fact

    for need in needs:
        if need.kind != "computed":
            continue
        params: Dict[str, Any] = {}
        unresolved = []
        for param, ref in need.inputs.items():
            if ref in by_id:
                num = _first_number(by_id[ref].value or "")
                params[param] = num if num is not None else by_id[ref].value
            elif user_typed(ref, question):
                num = _first_number(ref)
                params[param] = num if num is not None else ref
            else:
                unresolved.append(param)
        if unresolved:
            ctx.missing.append(Need(id=need.id, kind="missing_input",
                                    what=f"{need.what}: {', '.join(unresolved)}"))
            continue
        try:
            result = run_calc(need.calculation or "", params)
        except Exception as exc:  # noqa: BLE001 — a failed run is not a figure
            result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        if (result or {}).get("status") == "error":
            ctx.missing.append(Need(id=need.id, kind="missing_input",
                                    what=f"{need.what} ({(result or {}).get('error', 'calculator error')})"))
            continue
        ctx.facts.append(Fact(
            need_id=need.id, what=need.what, source=SOURCE_CALCULATOR,
            formula=need.calculation, inputs=params, result=result,
            value=_result_value(result), unit=need.unit,
        ))
    return ctx


def _values_differ(a: Optional[str], b: Optional[str]) -> bool:
    x, y = _first_number(a or ""), _first_number(b or "")
    if x is not None and y is not None:
        return not _same(x, y)
    return (a or "").strip().lower() != (b or "").strip().lower()


def _result_value(result: Dict[str, Any]) -> Optional[str]:
    """The headline value of a calculator envelope, for display only. The
    provenance gate credits every number in the result, not just this one."""
    res = (result or {}).get("result")
    if isinstance(res, (int, float)):
        return str(res)
    if isinstance(res, dict):
        for v in res.values():
            if isinstance(v, (int, float)):
                return str(v)
    return None


# ── what the answer is told ───────────────────────────────────────────────

def facts_block(ctx: NeedContext) -> str:
    """The facts the answer must use, each with its source. Empty when the
    plan produced nothing usable."""
    if not ctx.facts and not ctx.missing:
        return ""
    lines = ["----- FACTS FETCHED FOR THIS QUESTION (use these; each states its source) -----"]
    for f in ctx.facts:
        src = _source_label(f)
        if f.source == SOURCE_CALCULATOR:
            lines.append(
                f"- {f.what}: CALCULATOR {f.formula} with {json.dumps(f.inputs)} returned "
                f"{json.dumps((f.result or {}).get('result'))}. State this result exactly as "
                "returned; explain it; do not recompute it and do not offer readings the "
                "question excluded."
            )
        else:
            lines.append(f"- {f.what}: {f.value}{(' ' + f.unit) if f.unit else ''} [{src}]"
                         + (f" NOTE: {f.note}." if f.note else ""))
    if ctx.missing:
        lines.append("MISSING INPUTS (no layer supplies them): " + "; ".join(n.what for n in ctx.missing)
                     + ". Ask the user for these. Do not state a figure that depends on them.")
    lines.append("Every figure you state must be one of the values above, the user's own, "
                 "or a value quoted from the excerpts; mark a general assumption as such.")
    return "\n".join(lines)


def _source_label(f: Fact) -> str:
    if f.source == SOURCE_USER:
        return "from your question"
    if f.source == SOURCE_PROJECT:
        return "project document " + (f.doc_name or f.doc_id or "") + (f", p. {f.page}" if f.page else "")
    if f.source == SOURCE_GENERAL:
        return "general knowledge " + (f.doc_name or f.doc_id or "") + (f", p. {f.page}" if f.page else "")
    return f.source


# ── runtime entry ─────────────────────────────────────────────────────────

#: Provenance of the answer just finished in this task (read by the message
#: store so the record is saved with the assistant message).
LAST_PROVENANCE: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "need_plan_last_provenance", default=None,
)


def _model_configured() -> bool:
    """A plan needs a model to write it. With no provider key configured (a
    test run, an offline box) there is nothing to plan with."""
    try:
        from app.agents.runtime import _llm_config

        cfg = _llm_config()
        key_env = cfg.get("env_key")
        return bool(not key_env or os.getenv(key_env))
    except Exception:  # noqa: BLE001 — no readable config: nothing to plan with
        logger.warning("need_plan: LLM config unreadable; planning off", exc_info=True)
        return False


def _calculator_names() -> List[str]:
    from app.lib.construction_formulas import CALCULATORS

    return list(CALCULATORS)


def _layer_fetchers(project_id: str):
    """Project-layer and general-knowledge-layer retrieval for one need."""
    from app.core.projects import general_knowledge_project_ids
    from app.core.rag.retriever import retrieve_with_filter

    gk_ids = set(general_knowledge_project_ids())

    def fetch_project(what: str) -> List[Any]:
        chunks, _ = retrieve_with_filter(what, project_id, k=6)
        return [c for c in chunks if getattr(c, "project_id", None) not in gk_ids]

    def fetch_general(what: str) -> List[Any]:
        out: List[Any] = []
        for gid in sorted(gk_ids):
            chunks, _ = retrieve_with_filter(what, gid, k=6)
            out.extend(chunks)
        out.sort(key=lambda c: -(getattr(c, "score", 0) or 0))
        return out[:6]

    return fetch_project, fetch_general


async def plan_and_fetch(question: str, project_id: Optional[str]) -> Optional[NeedContext]:
    """Plan the needs, fetch each from its home, publish the context for the
    turn. Returns None (and publishes nothing) when planning is off or fails;
    the answer then runs exactly as before."""
    import asyncio

    CURRENT.set(None)
    LAST_PROVENANCE.set(None)
    if not enabled() or not (question or "").strip() or not project_id:
        return None
    if not _model_configured():
        return None
    try:
        # The calculator registry import and the general-knowledge project
        # lookup are synchronous (module import, SQL): keep them off the loop.
        names = await asyncio.to_thread(_calculator_names)
        needs = await make_plan(question, calculators=names)
        if not needs:
            return None
        fetch_project, fetch_general = await asyncio.to_thread(_layer_fetchers, project_id)

        # Retrieval is synchronous (embedding + SQL): run it in a worker
        # thread so the event loop stays free.
        async def _project(what: str) -> List[Any]:
            return await asyncio.to_thread(fetch_project, what)

        async def _general(what: str) -> List[Any]:
            return await asyncio.to_thread(fetch_general, what)

        ctx = await asyncio.wait_for(
            resolve(needs, question, fetch_project=_project, fetch_general=_general),
            timeout=float(os.getenv("NEED_PLAN_TIMEOUT_S", "45")),
        )
        CURRENT.set(ctx)
        return ctx
    except Exception:  # noqa: BLE001 — planning must never break a turn
        logger.warning("need_plan: plan/fetch failed; answering without a plan", exc_info=True)
        CURRENT.set(None)
        return None
