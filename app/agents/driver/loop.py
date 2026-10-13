"""A driver turn: the model calls tools until it answers, within the
configured number of steps. Events have the shapes the chat stream already
uses (status, tool_call, tool_result, token, end)."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from app.agents import driver
from app.agents.driver import context, llm, render, routes, sources, tools
from app.core import turn_progress
from app.core.offload import off_loop

_LOG = logging.getLogger(__name__)

#: Where a streamed answer may be cut into checkable pieces: after a
#: sentence's closing mark, or at a line break (list items, table rows).
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n")
_HEADING = re.compile(r"^\s*#{1,6}\s+\S")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

#: How much of one tool result the model is given.
_TOOL_RESULT_CHARS = 20000

_STOP = ("I could not finish this answer within the steps a turn may take. "
         "Please narrow the question or split it into parts.")


def exit_check(answer: str, msgs: List[Dict[str, Any]], shared: bool = False) -> tuple:
    """The one check before a driver answer leaves: an attribution no
    evidence record backs is removed (citation_provenance.gate), and every
    figure is credited to the user's words, a tool run or a retrieved
    passage -- one that matches none is removed (figure_provenance). The
    evidence is this turn's own messages: the user's words and the tool
    runs. Returns (answer, provenance trail, report): the report names the
    figures removed and counts the passages read, for the end event."""
    from app.agents import citation_provenance as cp

    # No registry identifier leaves: each becomes the name a user reads.
    answer, identifiers = render.readable(answer)
    if shared:
        # The model's words also pass the platform's shared exit
        # (answer_exit) here, so the stream shows what the final answer
        # shows: no chunk numbers, ids or markers that point at nothing.
        from app.agents import answer_exit

        answer = answer_exit.check_text(answer)
        # A library file the turn read is named by its title, never by its
        # stored file name.
        for stored, title in sorted(sources.library_names(msgs).items(), key=lambda kv: -len(kv[0])):
            answer = answer.replace(stored, title)
    checked = cp.gate(answer, None, msgs, tool_passages=True, annotate=False)
    _, every = cp.figure_provenance(checked, None, msgs, enforce=False, tool_passages=True)
    out, trail = cp.figure_provenance(checked, None, msgs, enforce=True, tool_passages=True)
    out = _tidy(out)
    report = {
        "identifiers_replaced": identifiers,
        "figures_removed": [e["figure"] for e in every if not e.get("source")],
        "passages_read": sum(1 for r in cp.build_evidence(None, msgs, tool_passages=True).records
                             if r.kind == "retrieval"),
    }
    return out, trail, report


def _without_missing_pages(value: Any) -> Any:
    """A tool result as the model reads it, minus pages that are not pages
    (-1 / 0 / None mean "no page"): left in, they came back as "(source: -1)"."""
    if isinstance(value, dict):
        return {k: _without_missing_pages(v) for k, v in value.items()
                if not (k in ("page", "page_number") and (v is None or (isinstance(v, (int, float))
                                                                      and not isinstance(v, bool) and v <= 0)))}
    if isinstance(value, list):
        return [_without_missing_pages(v) for v in value]
    return value


#: Brackets left empty, or holding only separators, once the exit check has
#: removed an unbacked figure or attribution from them.
_EMPTY_BRACKETS = re.compile(r"\s*[(\[]\s*[,;:\-–—]*\s*[)\]]")


def _tidy(text: str) -> str:
    return _EMPTY_BRACKETS.sub("", text)


def _args(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    raw = (tool_call.get("function") or {}).get("arguments") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        _LOG.warning("driver: tool %r arguments are not JSON; calling it with none",
                     (tool_call.get("function") or {}).get("name"))
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


async def stream(agent: Any, user_message: str, history: Optional[list] = None,
                 project_id: Optional[str] = None, conversation_id: Optional[str] = None,
                 user_id: Optional[str] = None, api_key: Optional[str] = None,
                 **_ignored: Any) -> AsyncIterator[Dict[str, Any]]:
    t0 = time.monotonic()
    disciplines = [h["discipline"] for h in context.hats()]
    hat: Optional[str] = None
    # The profile lookup reads the database: in a thread, never on the event
    # loop (live: 0.4-1.1 s stalls at 5 driver users).
    msgs: List[Dict[str, Any]] = await off_loop(context.messages, user_message, history,
                                                 project_id, user_id, hat)
    used: List[str] = []
    ran: List[tuple] = []  # (tool, arguments) of every tool run, for the credit lines
    computed: List[tuple] = []  # (envelope, inputs) of every calculation that succeeded
    model_answered = False
    failed: List[tuple] = []  # (tool, its own error) of every run that failed
    succeeded = False  # any tool run this turn came back ok
    user_numbers = set(_NUMBER.findall(user_message or ""))
    evidence: List[Dict[str, Any]] = []  # tool results, whole, for the exit check
    exports: List[Dict[str, Any]] = []
    yield {"type": "start", "mode": "driver", "agent": agent.name}
    yield turn_progress.event("writing")
    answer_done = False
    provenance: List[Dict[str, Any]] = []
    check: Dict[str, Any] = {"figures_removed": [], "passages_read": 0}

    def _trail() -> List[Dict[str, Any]]:
        whole = iter(evidence)  # same order as the tool messages in msgs
        return [next(whole) if m.get("role") == "tool" else m for m in msgs]

    sent: List[str] = []

    def _release(piece: str, model_text: bool = True) -> Optional[Dict[str, Any]]:
        """Exit-check one finished piece of the answer and hand it out.
        ``model_text`` False: the platform's own words (a calculator's
        question, a computed result), which no failed run makes unbacked."""
        if not piece.strip():  # layout only (a blank line): nothing to check
            sent.append(piece)
            return {"type": "token", "content": piece}
        if model_text and failed and not succeeded and set(_NUMBER.findall(piece)) - user_numbers:
            # Every tool this turn failed: nothing backs a number the user
            # did not write, bare counts included ("0 items were found").
            return None
        checked, trail, report = exit_check(piece, _trail(), shared=model_text)
        # The checks tidy whitespace; the piece's own ending (a line break
        # between list items or paragraphs) belongs to the answer's layout.
        ending = piece[len(piece.rstrip()):]
        if checked.strip():
            checked = checked.rstrip() + ending
        sent.append(checked)
        provenance.extend(trail)
        check["figures_removed"].extend(report["figures_removed"])
        check["identifiers_replaced"] = check.get("identifiers_replaced", 0) + report["identifiers_replaced"]
        check["passages_read"] = report["passages_read"]
        return {"type": "token", "content": checked} if checked.strip() else None

    step = 0
    for step in range(driver.max_steps()):
        offered = tools.offered(agent, project_id, hat, disciplines)
        # Streamed: each finished sentence is checked and sent while the model
        # is still writing. One sentence is held back, so a short preamble
        # ("Let me look that up.") is dropped when the reply turns out to be
        # tool calls.
        buffer, held, result = "", None, None
        async for kind, value in llm.stream(agent, msgs, offered, model=driver.model()):
            if kind == "done":
                result = value
                break
            buffer += value
            while True:
                cut = _SENTENCE_END.search(buffer)
                if cut is None:
                    break
                sentence, buffer = buffer[:cut.end()], buffer[cut.end():]
                if held is not None:
                    event = _release(held)
                    if event:
                        yield event
                held = sentence
        if result is None or result["status"] != "success":
            yield {"type": "error", "message": (result or {}).get("error") or "The language model did not answer."}
            return
        message = result["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            for piece in (held, buffer):
                if piece:
                    event = _release(piece)
                    if event:
                        yield event
            answer_done = model_answered = True
            break
        msgs.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        questions: List[str] = []  # calculators that need an input this round
        for call in calls:
            name = (call.get("function") or {}).get("name") or ""
            args = _args(call)
            yield {"type": "tool_call", "tool": name, "name": name, "args_preview": json.dumps(args)[:200]}
            if name == tools.SELECT_HAT:
                chosen = str(args.get("hat") or "")
                if chosen in disciplines:
                    hat = chosen
                    msgs[0] = await off_loop(context.system_message, project_id, user_id, hat)
                    result_of_call: Dict[str, Any] = {"ok": True, "hat": hat}
                else:
                    result_of_call = {"ok": False,
                                      "error": f"No hat named {chosen!r}; choose one of {sorted(disciplines)}."}
            elif name == routes.RUN_WORKFLOW:
                used.append(f"{name}:{args.get('workflow')}")
                result_of_call = await routes.run(args, user_message, project_id, user_id, conversation_id)
                exports.extend(result_of_call.get("exports") or [])
            else:
                used.append(name)
                result_of_call = await agent._run_tool_call(call, api_key, project_id, conversation_id,
                                                            user_message=user_message, history=history)
                if result_of_call.get("ok", True):
                    ran.append((name, args))  # a failed run is credited with nothing
            ok = bool(result_of_call.get("ok", True))
            if name != tools.SELECT_HAT:
                if ok:
                    succeeded = True
                else:
                    from app.lib.source_labels import tool_label

                    shown_name = (str(args.get("workflow") or "").replace("_", " ").capitalize()
                                  if name == routes.RUN_WORKFLOW else tool_label(name))
                    error = str(result_of_call.get("error") or result_of_call.get("missing") or "").strip()
                    failed.append((shown_name, error or "it returned no result"))
            yield {"type": "tool_result", "tool": name, "name": name, "ok": ok}
            full = json.dumps(result_of_call, default=str)
            tool_msg = {"role": "tool", "name": name, "tool_call_id": call.get("id") or name}
            # The model gets at most _TOOL_RESULT_CHARS of a result; the exit
            # check reads it whole -- cut, the JSON no longer parses and every
            # figure it backs looks unsourced (live: 23 figures removed).
            # A calculation reaches the model as prose in the registry's own
            # words (render.render_calculation), never as raw keys it could
            # copy into the answer; the exit check still reads the full result.
            envelope = result_of_call.get("result") if isinstance(result_of_call, dict) else None
            if isinstance(envelope, dict) and envelope.get("calculation"):
                inputs = args.get("params") if isinstance(args.get("params"), dict) else args
                shown = render.render_calculation(envelope, inputs)
                if envelope.get("status") == "success":
                    computed.append((envelope, inputs))
                if envelope.get("status") != "success" and str(envelope.get("question") or "").strip():
                    questions.append(str(envelope["question"]).strip())
            else:
                shown = json.dumps(_without_missing_pages(result_of_call), default=str)[:_TOOL_RESULT_CHARS]
            msgs.append({**tool_msg, "content": shown})
            evidence.append({**tool_msg, "content": full})
        # A calculator that rejected or lacks an input ends the turn with its
        # own question: the model is never given the chance to compute a
        # figure the calculator refused (lane 2: it did).
        if questions:
            for question in dict.fromkeys(questions):
                event = _release(question + "\n", model_text=False)
                if event:
                    yield event
            answer_done = True
            break
    if not answer_done:
        event = _release(_STOP)
        if event:
            yield event
    if model_answered and failed and not succeeded:
        # The failure itself, in the tool's own words, when the answer does
        # not already say it: the user learns what could not be done and why.
        written = "".join(sent)
        for shown_name, error in dict.fromkeys(failed):
            if error.lower() in written.lower():
                continue
            line = f"{shown_name} could not complete: {error}"
            sent.append(("\n\n" if written.strip() else "") + line + "\n")
            yield {"type": "token", "content": sent[-1]}
            written = "".join(sent)
    if model_answered:
        # A result the platform computed reaches the user even when the
        # model's words stop short of it (a table header with no rows, a
        # heading with nothing under it): what the answer does not carry is
        # added in the renderer's words, under a heading left empty.
        for envelope, inputs in computed:
            written = "".join(sent)
            if render.carried(envelope, inputs, written):
                continue
            block = "\n".join(render.result_lines(envelope, inputs))
            if not block:
                continue
            last = written.rstrip("\n").rsplit("\n", 1)[-1]
            if _HEADING.match(last):
                piece = ("" if written.endswith("\n") else "\n") + block + "\n"
            else:
                piece = "\n\n" + block + "\n"
            event = _release(piece, model_text=False)
            if event:
                yield event
    from app.agents import citation_provenance as cp

    # Once, at the end: the calculator credit the answer does not carry, and
    # the note if any piece lost an unbacked attribution.
    notes = cp.closing_notes("".join(sent), _trail(), tool_passages=True,
                             removed=cp._REMOVED_IN_PIECE.get())
    credit = sources.tool_credit_lines(ran)
    if credit and credit not in "".join(sent):
        notes = "\n\n" + credit + notes
    if notes.strip():
        yield {"type": "token", "content": notes}
    yield {"type": "end", "complete": True, "mode": "driver", "hat": hat, "tools": used, "exports": exports,
           "provenance": provenance, "exit_check": check,
           "sources": sources.panel(_trail(), provenance, "".join(sent),
                                    calculated=any(n == "construction_calc" for n, _ in ran)),
           "steps": step + 1, "elapsed_s": round(time.monotonic() - t0, 2)}
