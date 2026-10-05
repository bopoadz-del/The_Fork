"""Final-answer posture path.

Call order, and only at this step:

1. retrieval context already attached by Fork retrieval
2. grounding gate
3. escalation policy (frontier answer remains the escalation target)
4. posture model sample
5. constrained decoding on citation ids from the attached context
6. citation resolution
7. render

The model is called only with that attached context. The system prompt is
read from the serve config's ``system_prompt_file``. This module does not
edit the prompt, the retriever, or the grounding gate.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_LOG = logging.getLogger("fork.posture")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FLAG_PATH = PROJECT_ROOT / "configs" / "posture" / "flag.json"
MODES = ("off", "shadow", "on")

_CITE_RE = re.compile(r"\[cite:([^\]]+)\]", re.IGNORECASE)
_FORMULA_TOOLS = frozenset({
    "formula_executor",
    "formula_executor_v2",
    "construction_calc",
})
_GRADER_FUNCS = (
    "grade_refusal_qualification",
    "grade_fact_leak",
    "grade_routing",
    "grade_citation_validity",
)
_FALLBACK_PIN = {
    "distribution": "cerebrum-slm",
    "git_sha": "4e167a7",
    "serve_config": "configs/serve/posture-epoch2.json",
    "system_prompt_key": "system_prompt_file",
    "checkpoint": (
        "tinker://d33e27e4-5533-5af4-9039-c9bf8254d4b7:train:0/"
        "sampler_weights/posture-qwen3-8b-lora-r16-epoch2"
    ),
}

GroundingGate = Callable[[str, dict[str, Any] | None, list[dict[str, Any]]], str]
Sampler = Callable[..., str]


class PostureUnavailable(RuntimeError):
    """The posture client or its serve config cannot be used."""


@dataclass
class AttachedContext:
    text: str
    chunks: list[dict[str, Any]]
    citation_ids: list[str]


@dataclass
class PostureResult:
    answer: str | None
    escalation: str | None
    system_prompt: str = ""
    context: str = ""
    sample_ms: float | None = None
    steps: list[str] = field(default_factory=list)


def _coerce_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    if mode in MODES:
        return mode
    return "off"


def _flag_path() -> Path:
    override = (os.getenv("FORK_POSTURE_FLAG_FILE") or "").strip()
    if override:
        return Path(override)
    return FLAG_PATH


def load_flag_config() -> dict[str, Any]:
    path = _flag_path()
    if not path.is_file():
        return {"default": "off", "projects": {}}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return {"default": "off", "projects": {}}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
    return {"default": _coerce_mode(str(data.get("default") or "off")), "projects": projects}


def posture_mode(project_id: str | None) -> str:
    """Resolve ``FORK_POSTURE_MODEL`` for one project. Default is off."""
    cfg = load_flag_config()
    pid = (project_id or "").strip()
    projects = cfg.get("projects") or {}
    if pid and pid in projects:
        return _coerce_mode(str(projects[pid]))
    env = os.getenv("FORK_POSTURE_MODEL", "").strip().lower()
    if env:
        return _coerce_mode(env)
    return _coerce_mode(str(cfg.get("default") or "off"))


def client_pin() -> dict[str, Any]:
    """Store lock when that module is installed, otherwise the client pin."""
    try:
        from app.core.store_pins import posture_client_pin
    except ImportError:
        return dict(_FALLBACK_PIN)
    pin = posture_client_pin()
    merged = dict(_FALLBACK_PIN)
    for key, value in pin.items():
        if value not in (None, ""):
            merged[key] = value
    return merged


def attached_retrieval(
    rag_sys_msg: dict[str, Any] | None,
    audit_rec: dict[str, Any] | None,
) -> AttachedContext:
    """Context the retriever already attached. Empty when nothing was attached."""
    content = ""
    if isinstance(rag_sys_msg, dict):
        content = str(rag_sys_msg.get("content") or "").strip()
    chunks: list[dict[str, Any]] = []
    if isinstance(audit_rec, dict):
        raw = audit_rec.get("chunks")
        if isinstance(raw, list):
            chunks = [c for c in raw if isinstance(c, dict)]
    if not content:
        return AttachedContext("", [], [])
    ids: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        cid = str(chunk.get("chunk_id") or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return AttachedContext(content, chunks, ids)


def formula_fact_text(messages: list[dict[str, Any]] | None) -> str:
    """Formula-tool payloads already on the turn. Not a substitute for retrieval."""
    blocks: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("name") not in _FORMULA_TOOLS:
            continue
        body = str(msg.get("content") or "").strip()
        if body:
            blocks.append(body)
    return "\n\n".join(blocks)


def operator_question(messages: list[dict[str, Any]] | None, fallback: str = "") -> str:
    for msg in reversed(messages or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = str(msg.get("content") or "").strip()
        if text.startswith("PLATFORM PRE-DISPATCH:"):
            continue
        if text:
            return text
    return fallback


def _default_grounding_gate(
    text: str,
    rag_sys_msg: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> str:
    from app.agents.runtime import _cost_grounding_gate

    return _cost_grounding_gate(text, rag_sys_msg, messages)


def constrain_citations(text: str, allowed_ids: set[str]) -> str:
    """Drop citation markers whose id is not in the attached context."""

    def repl(match: re.Match[str]) -> str:
        cid = match.group(1).strip()
        if cid in allowed_ids:
            return match.group(0)
        return ""

    return _CITE_RE.sub(repl, text or "")


def resolve_citations(text: str, chunks: list[dict[str, Any]]) -> str:
    """Replace an allowed ``[cite:id]`` with the chunk's own source label."""
    by_id: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        cid = str(chunk.get("chunk_id") or "").strip()
        if cid and cid not in by_id:
            by_id[cid] = chunk

    def repl(match: re.Match[str]) -> str:
        cid = match.group(1).strip()
        chunk = by_id.get(cid)
        if chunk is None:
            return ""
        name = str(chunk.get("source_name") or chunk.get("doc_id") or "").strip()
        if not name:
            return match.group(0)
        index = chunk.get("chunk_index")
        if isinstance(index, int):
            return f"[source: {name}, chunk {index}]"
        return f"[source: {name}]"

    return _CITE_RE.sub(repl, text or "")


def _serve_config_path(pin: dict[str, Any]) -> Path | None:
    override = (os.getenv("FORK_POSTURE_SERVE_CONFIG") or "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    rel = str(pin.get("serve_config") or "configs/serve/posture-epoch2.json")
    candidates: list[Path] = []
    root = (os.getenv("CEREBRUM_SLM_ROOT") or "").strip()
    if root:
        candidates.append(Path(root) / rel)
    try:
        module = importlib.import_module("posture_model")
    except ImportError:
        module = None
    if module is not None and getattr(module, "__file__", None):
        pkg = Path(module.__file__).resolve().parent
        candidates.append(pkg.parents[1] / rel)
        candidates.append(pkg / rel)
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_system_prompt(pin: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return ``(system_prompt, checkpoint)`` from the serve config file."""
    pin = pin or client_pin()
    path = _serve_config_path(pin)
    if path is None:
        raise PostureUnavailable(
            "posture serve config is not installed "
            f"(cerebrum-slm@{pin.get('git_sha')} {pin.get('serve_config')})"
        )
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise PostureUnavailable(f"serve config {path} is not an object")
    key = str(pin.get("system_prompt_key") or cfg.get("system_prompt_key") or "system_prompt_file")
    prompt_name = cfg.get(key)
    if not isinstance(prompt_name, str) or not prompt_name.strip():
        raise PostureUnavailable(f"serve config {path} has no {key}")
    prompt_path = Path(prompt_name)
    if not prompt_path.is_file():
        prompt_path = path.parent / prompt_name
    if not prompt_path.is_file():
        raise PostureUnavailable(f"system prompt file {prompt_name} is not beside {path}")
    prompt = prompt_path.read_text(encoding="utf-8")
    checkpoint = str(cfg.get("checkpoint") or cfg.get("model_path") or pin.get("checkpoint") or "")
    return prompt, checkpoint


def _load_slm_sampler() -> Sampler:
    if not (os.getenv("TINKER_API_KEY") or "").strip():
        raise PostureUnavailable("TINKER_API_KEY is not set")
    module = None
    for name in ("posture_model", "posture_model.client"):
        try:
            module = importlib.import_module(name)
            break
        except ImportError:
            continue
    if module is None:
        raise PostureUnavailable("cerebrum-slm posture_model is not installed")
    sample = getattr(module, "sample", None)
    if sample is None:
        client_cls = getattr(module, "PostureClient", None)
        if client_cls is not None:
            client = client_cls()
            sample = getattr(client, "sample", None)
    if not callable(sample):
        raise PostureUnavailable("posture_model has no sample callable")
    return sample


def sample_posture(
    *,
    system_prompt: str,
    context: str,
    user_message: str,
    allowed_citation_ids: list[str],
    checkpoint: str,
    sampler: Sampler | None = None,
) -> str:
    if not (context or "").strip():
        raise PostureUnavailable("refusing to sample without attached context")
    fn = sampler or _load_slm_sampler()
    out = fn(
        system_prompt=system_prompt,
        context=context,
        user_message=user_message,
        allowed_citation_ids=list(allowed_citation_ids),
        checkpoint=checkpoint,
    )
    return "" if out is None else str(out)


def _import_graders():
    for name in ("posture_model.graders", "posture_model.eval.graders"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


def grade_pair(
    *,
    question: str,
    current: str,
    posture: str | None,
    context: str,
    graders: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if graders is not None:
        return graders(
            question=question,
            current=current,
            posture=posture,
            context=context,
        )
    module = _import_graders()
    if module is None:
        return {"available": False, "reason": "cerebrum-slm graders are not installed"}
    out: dict[str, Any] = {"available": True}
    for name in _GRADER_FUNCS:
        fn = getattr(module, name, None)
        if not callable(fn):
            out[name] = {"available": False}
            continue
        out[name] = fn(
            question=question,
            answer=posture,
            baseline=current,
            context=context,
        )
    return out


def _shadow_path() -> Path:
    data = (os.getenv("DATA_DIR") or "").strip() or str(PROJECT_ROOT / "data")
    return Path(data) / "posture_shadow.jsonl"


def log_posture_record(record: dict[str, Any]) -> None:
    line = json.dumps(record, default=str, ensure_ascii=False)
    _LOG.info("posture_final %s", line)
    path = _shadow_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _question_sha(question: str) -> str:
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()[:16]


def run_posture_path(
    current: str,
    *,
    rag_sys_msg: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None,
    audit_rec: dict[str, Any] | None,
    question: str = "",
    sampler: Sampler | None = None,
    grounding_gate: GroundingGate | None = None,
) -> PostureResult:
    """Run steps 1-7. Does not decide which answer is served."""
    steps: list[str] = []
    gate = grounding_gate or _default_grounding_gate
    attached = attached_retrieval(rag_sys_msg, audit_rec)
    steps.append("retrieval_context")
    try:
        gate(current or "", rag_sys_msg, list(messages or []))
    except Exception:
        _LOG.exception("grounding gate failed on the current answer")
        steps.append("grounding_gate")
        return PostureResult(
            answer=None,
            escalation="grounding_gate_failed",
            steps=steps,
        )
    steps.append("grounding_gate")

    if not attached.text:
        _LOG.info("posture_escalation reason=no_attached_context")
        steps.append("escalation")
        return PostureResult(
            answer=None,
            escalation="no_attached_context",
            steps=steps,
        )

    facts = formula_fact_text(messages)
    context = attached.text if not facts else attached.text + "\n\n" + facts
    ask = question or operator_question(messages)
    try:
        system_prompt, checkpoint = load_system_prompt()
    except PostureUnavailable as exc:
        _LOG.info("posture_escalation reason=client_unavailable detail=%s", exc)
        steps.append("escalation")
        return PostureResult(
            answer=None,
            escalation="client_unavailable",
            context=context,
            steps=steps,
        )

    steps.append("escalation_policy")
    started = time.perf_counter()
    try:
        draft = sample_posture(
            system_prompt=system_prompt,
            context=context,
            user_message=ask,
            allowed_citation_ids=attached.citation_ids,
            checkpoint=checkpoint,
            sampler=sampler,
        )
    except PostureUnavailable as exc:
        _LOG.info("posture_escalation reason=client_unavailable detail=%s", exc)
        steps.append("escalation")
        return PostureResult(
            answer=None,
            escalation="client_unavailable",
            system_prompt=system_prompt,
            context=context,
            steps=steps,
        )
    except Exception:
        _LOG.exception("posture sample failed")
        steps.append("escalation")
        return PostureResult(
            answer=None,
            escalation="sample_failed",
            system_prompt=system_prompt,
            context=context,
            steps=steps,
        )
    sample_ms = (time.perf_counter() - started) * 1000.0
    steps.append("posture_model")
    constrained = constrain_citations(draft, set(attached.citation_ids))
    steps.append("constrained_decoding")
    resolved = resolve_citations(constrained, attached.chunks)
    steps.append("citation_resolution")
    try:
        rendered = gate(resolved, rag_sys_msg, list(messages or []))
    except Exception:
        _LOG.exception("grounding gate failed on the posture draft")
        return PostureResult(
            answer=None,
            escalation="grounding_gate_failed",
            system_prompt=system_prompt,
            context=context,
            sample_ms=sample_ms,
            steps=steps,
        )
    steps.append("render")
    return PostureResult(
        answer=rendered,
        escalation=None,
        system_prompt=system_prompt,
        context=context,
        sample_ms=sample_ms,
        steps=steps,
    )


def run_paired_paths(
    *,
    question: str,
    project_id: str | None,
    current_answer: str,
    rag_sys_msg: dict[str, Any] | None,
    audit_rec: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None = None,
    sampler: Sampler | None = None,
    grounding_gate: GroundingGate | None = None,
    graders: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Current path (already produced) beside one posture pass. Does not serve."""
    result = run_posture_path(
        current_answer,
        rag_sys_msg=rag_sys_msg,
        messages=messages,
        audit_rec=audit_rec,
        question=question,
        sampler=sampler,
        grounding_gate=grounding_gate,
    )
    verdicts = grade_pair(
        question=question,
        current=current_answer,
        posture=result.answer,
        context=result.context,
        graders=graders,
    )
    return {
        "project_id": project_id,
        "question_sha": _question_sha(question),
        "current_answer": current_answer,
        "posture_answer": result.answer,
        "escalation": result.escalation,
        "graders": verdicts,
        "sample_ms": result.sample_ms,
        "steps": result.steps,
        "served": "current",
    }


def apply_posture_at_final_answer(
    current: str,
    *,
    rag_sys_msg: dict[str, Any] | None,
    messages: list[dict[str, Any]] | None,
    project_id: str | None,
    audit_rec: dict[str, Any] | None,
    sampler: Sampler | None = None,
    grounding_gate: GroundingGate | None = None,
    graders: Callable[..., dict[str, Any]] | None = None,
) -> str:
    """Flag switch at the end of final-answer post-processing.

    ``off`` returns ``current`` and does not sample. ``shadow`` samples,
    logs both answers and grader verdicts, and still returns ``current``.
    ``on`` returns the posture render, or ``current`` when the path escalates.
    """
    mode = posture_mode(project_id)
    if mode == "off":
        return current
    question = operator_question(messages)
    try:
        paired = run_paired_paths(
            question=question,
            project_id=project_id,
            current_answer=current,
            rag_sys_msg=rag_sys_msg,
            audit_rec=audit_rec,
            messages=messages,
            sampler=sampler,
            grounding_gate=grounding_gate,
            graders=graders,
        )
    except Exception:
        _LOG.exception("posture final-answer failed")
        log_posture_record({
            "mode": mode,
            "project_id": project_id,
            "question_sha": _question_sha(question),
            "served": "current",
            "escalation": "sample_failed",
            "current_answer": current,
            "posture_answer": None,
            "graders": {"available": False, "reason": "posture path failed"},
        })
        return current
    paired["mode"] = mode
    if mode == "on" and paired.get("posture_answer") is not None and not paired.get("escalation"):
        paired["served"] = "posture"
        log_posture_record(paired)
        return str(paired["posture_answer"])
    paired["served"] = "current"
    log_posture_record(paired)
    return current
