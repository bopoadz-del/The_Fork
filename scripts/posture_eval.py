#!/usr/bin/env python3
"""Side-by-side posture eval through the Fork path.

Reads two sealed sets if they are present and does not write them back:

- Fork READY-gate unseen set (``FORK_READY_GATE_UNSEEN``, default
  ``data/eval/ready_gate_unseen.jsonl``)
- cerebrum-slm blind set (``SLM_BLIND_SET``, or under ``CEREBRUM_SLM_ROOT``)

Each item is executed on the current path and the posture path. A schema
change drops stored rows and runs both paths again. Stdout carries counts
and short grader metrics only — never item text.

``--mock`` runs the same driver with a deterministic executor so CI can
exercise the harness when ``TINKER_API_KEY`` and the sealed sets are
absent. ``--mock`` still refuses to invent a missing sealed set: point it
at files you already have, or pass ``--items`` for a local synthetic file
that you do not treat as sealed.

Live sampling uses ``app.agents.posture_final.run_paired_paths`` after
``rag_inject`` attaches retrieval context. That module is the final-answer
PR. This harness does not change retrieval or the grounding gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
READY_SET = "ready_gate_unseen"
BLIND_SET = "slm_blind"

Execute = Callable[..., dict[str, Any]]


class PostureEvalWall(RuntimeError):
    """The run cannot score sealed items. Nothing was invented."""


def default_ready_path() -> Path:
    override = (os.getenv("FORK_READY_GATE_UNSEEN") or "").strip()
    if override:
        return Path(override)
    return ROOT / "data" / "eval" / "ready_gate_unseen.jsonl"


def default_blind_path() -> Path:
    override = (os.getenv("SLM_BLIND_SET") or "").strip()
    if override:
        return Path(override)
    root = (os.getenv("CEREBRUM_SLM_ROOT") or "").strip()
    if root:
        base = Path(root)
        for rel in (
            "data/blind.jsonl",
            "eval/blind.jsonl",
            "data/eval/blind.jsonl",
        ):
            candidate = base / rel
            if candidate.is_file():
                return candidate
        return base / "data" / "blind.jsonl"
    return ROOT / "data" / "eval" / "slm_blind.jsonl"


def question_sha(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def load_sealed(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Read a jsonl set. Returns items and the sha256 of the raw bytes."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    items: list[dict[str, Any]] = []
    for line_no, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not str(row.get("question") or "").strip():
            raise PostureEvalWall(
                f"{path.name}:{line_no} has no question; the set was not modified"
            )
        items.append(row)
    return items, digest


def assert_unchanged(path: Path, digest: str) -> None:
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    if current != digest:
        raise PostureEvalWall(f"{path} changed during the run")


def _public_metric(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and len(value) <= 32 and "\n" not in value:
        return value
    return "recorded"


def public_graders(graders: Any) -> dict[str, Any]:
    if not isinstance(graders, dict):
        return {"available": False}
    out: dict[str, Any] = {}
    for key, value in graders.items():
        if isinstance(value, dict):
            out[key] = {k: _public_metric(v) for k, v in value.items()}
        else:
            out[key] = _public_metric(value)
    return out


def strip_item_text(record: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that would reprint a sealed item."""
    blocked = {"question", "prompt", "expected", "answer_key", "gold"}
    return {k: v for k, v in record.items() if k not in blocked}


def evaluate_items(
    items: list[dict[str, Any]],
    *,
    execute: Execute,
    previous: dict[str, Any] | None,
    schema_version: int = SCHEMA_VERSION,
) -> list[dict[str, Any]]:
    """Run both paths. A schema mismatch discards previous rows and re-samples."""
    stored: dict[str, dict[str, Any]] = {}
    if previous and previous.get("schema_version") == schema_version:
        for row in previous.get("rows") or []:
            if isinstance(row, dict) and row.get("question_sha"):
                stored[str(row["question_sha"])] = row
    rows: list[dict[str, Any]] = []
    for item in items:
        sha = question_sha(str(item.get("question") or ""))
        if sha in stored:
            rows.append(stored[sha])
            continue
        produced = execute(item, paths=("current", "posture"))
        produced = strip_item_text(produced)
        produced["question_sha"] = sha
        produced["paths"] = ["current", "posture"]
        rows.append(produced)
    return rows


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    escalations = sum(1 for row in rows if row.get("escalation"))
    grader_keys: dict[str, dict[str, list]] = {"current": {}, "posture": {}}
    available = False
    for row in rows:
        graders = row.get("graders")
        if isinstance(graders, dict) and graders.get("available") is True:
            available = True
        if not isinstance(graders, dict):
            continue
        for key, value in graders.items():
            if key == "available" or key == "reason":
                continue
            if isinstance(value, dict):
                for side in ("current", "posture"):
                    metric = value.get(side)
                    if isinstance(metric, (int, float)) and not isinstance(metric, bool):
                        grader_keys[side].setdefault(key, []).append(metric)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                grader_keys["posture"].setdefault(key, []).append(value)
    sides = {}
    for side, keys in grader_keys.items():
        sides[side] = {
            key: (sum(vals) / len(vals) if vals else None) for key, vals in keys.items()
        }
    return {
        "n": len(rows),
        "escalations": escalations,
        "graders_available": available,
        "metrics": sides,
    }


def format_summary(name: str, summary: dict[str, Any], status: str) -> str:
    metrics = summary.get("metrics") or {}
    parts = [
        f"set={name}",
        f"status={status}",
        f"n={summary.get('n', 0)}",
        f"escalations={summary.get('escalations', 0)}",
        f"graders={'available' if summary.get('graders_available') else 'unavailable'}",
    ]
    for side in ("current", "posture"):
        for key, value in (metrics.get(side) or {}).items():
            if value is None:
                continue
            parts.append(f"{side}.{key}={_public_metric(value)}")
    return " ".join(parts)


def mock_execute(item: dict[str, Any], *, paths: tuple[str, ...]) -> dict[str, Any]:
    """Deterministic stand-in. Does not call Tinker and does not echo the item."""
    if paths != ("current", "posture"):
        raise PostureEvalWall("both paths must be sampled together")
    attached = bool(item.get("context_attached", True))
    graders = item.get("mock_graders")
    if not isinstance(graders, dict):
        graders = {"available": False, "reason": "mock"}
    return {
        "current_answer": "mock-current",
        "posture_answer": None if not attached else "mock-posture",
        "escalation": None if attached else "no_attached_context",
        "graders": graders,
        "sample_ms": 0,
        "steps": [
            "retrieval_context",
            "grounding_gate",
            "escalation_policy",
            "posture_model",
            "constrained_decoding",
            "citation_resolution",
            "render",
        ],
    }


def frontier_answer(question: str, project_id: str | None) -> str:
    """Current path: one project-assistant turn. Does not invent an answer."""
    provider_keys = (
        "KIMI_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
    )
    if not any((os.getenv(key) or "").strip() for key in provider_keys):
        raise PostureEvalWall(
            "no frontier provider key is set; the current path was not run"
        )
    import asyncio

    from app.agents.runtime import get_agent

    agent = get_agent("project-assistant")
    if agent is None:
        raise PostureEvalWall("project-assistant is not registered")
    result = asyncio.run(agent.chat(question, project_id=project_id))
    answer = result.get("answer") if isinstance(result, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        raise PostureEvalWall("current path returned no answer")
    return answer


def full_fork_execute(item: dict[str, Any], *, paths: tuple[str, ...]) -> dict[str, Any]:
    """Retrieval attach, then both answers via the final-answer posture module."""
    if paths != ("current", "posture"):
        raise PostureEvalWall("both paths must be sampled together")
    if not (os.getenv("TINKER_API_KEY") or "").strip():
        raise PostureEvalWall(
            "TINKER_API_KEY is not set; live posture sampling was not run. "
            "Use --mock to exercise the harness."
        )
    try:
        from app.agents.posture_final import run_paired_paths
        from app.core.rag.inject import rag_inject
    except ImportError as exc:
        raise PostureEvalWall(
            "posture final-answer path is not importable on this revision"
        ) from exc
    question = str(item.get("question") or "")
    project_id = item.get("project_id")
    rag_sys_msg, audit = rag_inject(
        user_message=question,
        project_id=project_id,
        conversation_id=None,
        user_id=None,
        agent_name="posture-eval",
        history=None,
    )
    current = frontier_answer(question, project_id if isinstance(project_id, str) else None)
    return run_paired_paths(
        question=question,
        project_id=project_id,
        current_answer=current,
        rag_sys_msg=rag_sys_msg,
        audit_rec=audit,
        messages=[{"role": "user", "content": question}],
    )


def run_set(
    path: Path,
    *,
    execute: Execute,
    previous: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    if not path.is_file():
        summary = summarize_rows([])
        return "missing", summary, []
    items, digest = load_sealed(path)
    rows = evaluate_items(items, execute=execute, previous=previous)
    assert_unchanged(path, digest)
    return "scored", summarize_rows(rows), rows


def load_previous(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="side-by-side posture eval")
    parser.add_argument("--mock", action="store_true",
                        help="deterministic executor; does not call Tinker")
    parser.add_argument("--ready", type=Path, default=None)
    parser.add_argument("--blind", type=Path, default=None)
    parser.add_argument("--results", type=Path, default=ROOT / "data" / "eval" / "posture_eval_results.json")
    args = parser.parse_args(argv)

    execute: Execute = mock_execute if args.mock else full_fork_execute
    ready_path = args.ready or default_ready_path()
    blind_path = args.blind or default_blind_path()
    previous = load_previous(args.results)
    prev_sets = previous.get("sets") if isinstance(previous.get("sets"), dict) else {}
    if previous.get("schema_version") != SCHEMA_VERSION:
        prev_sets = {}

    report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "sets": {}}
    exit_code = 0
    for name, path in ((READY_SET, ready_path), (BLIND_SET, blind_path)):
        try:
            status, summary, rows = run_set(
                path,
                execute=execute,
                previous=prev_sets.get(name) if isinstance(prev_sets.get(name), dict) else None,
            )
        except PostureEvalWall as exc:
            print(f"WALL {name}: {exc}", file=sys.stderr)
            print(format_summary(name, summarize_rows([]), "wall"))
            exit_code = 2
            continue
        report["sets"][name] = {
            "status": status,
            "path": str(path),
            "summary": summary,
            "rows": rows,
            "schema_version": SCHEMA_VERSION,
        }
        print(format_summary(name, summary, status))
        if status == "missing":
            print(
                f"WALL {name}: sealed set not present at {path}. "
                "No items were invented.",
                file=sys.stderr,
            )
            exit_code = 2

    if report["sets"]:
        args.results.parent.mkdir(parents=True, exist_ok=True)
        args.results.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
