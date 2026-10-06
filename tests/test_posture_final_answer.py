"""FORK_POSTURE_MODEL off / shadow / on at the final-answer step."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from app.agents.posture_final import (
    apply_posture_at_final_answer,
    load_system_prompt,
    posture_mode,
    run_posture_path,
)

ROOT = Path(__file__).resolve().parents[1]


def _serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str = "POSTURE PROMPT\n") -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    cfg = tmp_path / "posture-epoch2.json"
    cfg.write_text(
        json.dumps({
            "system_prompt_file": prompt_path.name,
            "checkpoint": "tinker://test/sampler_weights/posture",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("FORK_POSTURE_SERVE_CONFIG", str(cfg))


def _flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, projects: dict, default: str = "off") -> None:
    path = tmp_path / "flag.json"
    path.write_text(json.dumps({"default": default, "projects": projects}), encoding="utf-8")
    monkeypatch.setenv("FORK_POSTURE_FLAG_FILE", str(path))
    monkeypatch.delenv("FORK_POSTURE_MODEL", raising=False)


def _ctx() -> tuple[dict, dict]:
    rag = {"role": "system", "content": "Attached retrieval text about the works."}
    audit = {
        "chunks": [
            {
                "chunk_id": "c1",
                "chunk_index": 3,
                "source_name": "spec.pdf",
                "doc_id": "doc-1",
            }
        ]
    }
    return rag, audit


def _gate_recorder():
    calls: list[str] = []

    def gate(text, rag, messages):
        calls.append(text)
        return text

    return calls, gate


def test_off_does_not_sample_or_log(tmp_path, monkeypatch):
    _flags(tmp_path, monkeypatch, {})
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    seen: list[str] = []

    def sampler(**kwargs):
        seen.append(kwargs["context"])
        return "should not be used"

    out = apply_posture_at_final_answer(
        "current answer",
        rag_sys_msg={"role": "system", "content": "ctx"},
        messages=[{"role": "user", "content": "question"}],
        project_id="p1",
        audit_rec={"chunks": [{"chunk_id": "c1"}]},
        sampler=sampler,
        grounding_gate=lambda text, rag, messages: text,
    )
    assert out == "current answer"
    assert seen == []
    assert not (tmp_path / "data" / "posture_shadow.jsonl").exists()


def test_shadow_serves_current_and_logs_both(tmp_path, monkeypatch):
    _flags(tmp_path, monkeypatch, {"p1": "shadow"})
    _serve(tmp_path, monkeypatch, prompt="EXACT PROMPT")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    rag, audit = _ctx()
    calls, gate = _gate_recorder()
    captured: dict = {}

    def sampler(**kwargs):
        captured.update(kwargs)
        return "posture says [cite:c1] and [cite:other]"

    verdicts = {"refusal_qualification": "pass", "fact_leak": 0,
                "routing": "pass", "citation_validity": 1.0}

    out = apply_posture_at_final_answer(
        "current answer",
        rag_sys_msg=rag,
        messages=[
            {"role": "user", "content": "What is recorded?"},
            {"role": "tool", "name": "formula_executor", "content": "{\"result\": 4}"},
        ],
        project_id="p1",
        audit_rec=audit,
        sampler=sampler,
        grounding_gate=gate,
        graders=lambda **kwargs: verdicts,
    )
    assert out == "current answer"
    assert captured["system_prompt"] == "EXACT PROMPT"
    assert "Attached retrieval text" in captured["context"]
    assert "{\"result\": 4}" in captured["context"]
    assert captured["allowed_citation_ids"] == ["c1"]
    assert calls[0] == "current answer"
    assert "posture_model" in run_posture_path(
        "current answer",
        rag_sys_msg=rag,
        messages=[],
        audit_rec=audit,
        sampler=sampler,
        grounding_gate=gate,
    ).steps
    line = json.loads((tmp_path / "data" / "posture_shadow.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["served"] == "current"
    assert line["current_answer"] == "current answer"
    assert "[source: spec.pdf, chunk 3]" in line["posture_answer"]
    assert "[cite:other]" not in line["posture_answer"]
    assert "other" not in line["posture_answer"]
    assert line["graders"] == verdicts
    assert "What is recorded?" not in (tmp_path / "data" / "posture_shadow.jsonl").read_text(encoding="utf-8")


def test_on_serves_constrained_posture(tmp_path, monkeypatch):
    _flags(tmp_path, monkeypatch, {"p1": "on"})
    _serve(tmp_path, monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    rag, audit = _ctx()

    def sampler(**kwargs):
        return "see [cite:c1] plus [cite:nope]"

    out = apply_posture_at_final_answer(
        "current answer",
        rag_sys_msg=rag,
        messages=[{"role": "user", "content": "q"}],
        project_id="p1",
        audit_rec=audit,
        sampler=sampler,
        grounding_gate=lambda text, rag, messages: text,
    )
    assert out == "see [source: spec.pdf, chunk 3] plus "
    assert "nope" not in out
    assert "current answer" not in out


def test_on_without_context_escalates_and_does_not_sample(tmp_path, monkeypatch):
    _flags(tmp_path, monkeypatch, {"p1": "on"})
    _serve(tmp_path, monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    seen: list[str] = []

    def sampler(**kwargs):
        seen.append("called")
        return "nope"

    out = apply_posture_at_final_answer(
        "frontier answer",
        rag_sys_msg=None,
        messages=[{"role": "user", "content": "q"}],
        project_id="p1",
        audit_rec={"chunks": []},
        sampler=sampler,
        grounding_gate=lambda text, rag, messages: text,
    )
    assert out == "frontier answer"
    assert seen == []
    line = json.loads((tmp_path / "data" / "posture_shadow.jsonl").read_text(encoding="utf-8"))
    assert line["escalation"] == "no_attached_context"
    assert line["served"] == "current"


def test_gate_runs_before_sample_and_can_replace_the_draft(tmp_path, monkeypatch):
    _flags(tmp_path, monkeypatch, {"p1": "on"})
    _serve(tmp_path, monkeypatch)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    rag, audit = _ctx()
    order: list[str] = []

    def gate(text, rag_msg, messages):
        order.append("gate:" + text[:12])
        if text.startswith("draft"):
            return "gate refusal"
        return text

    def sampler(**kwargs):
        order.append("sample")
        return "draft [cite:c1]"

    out = apply_posture_at_final_answer(
        "current",
        rag_sys_msg=rag,
        messages=[],
        project_id="p1",
        audit_rec=audit,
        sampler=sampler,
        grounding_gate=gate,
    )
    assert order[0].startswith("gate:")
    assert order[1] == "sample"
    assert out == "gate refusal"


def test_project_entry_beats_env_and_default_stays_off(tmp_path, monkeypatch):
    _flags(tmp_path, monkeypatch, {"kept": "shadow"})
    monkeypatch.setenv("FORK_POSTURE_MODEL", "on")
    assert posture_mode("kept") == "shadow"
    assert posture_mode("other") == "on"
    monkeypatch.delenv("FORK_POSTURE_MODEL")
    assert posture_mode("other") == "off"
    monkeypatch.setenv("FORK_POSTURE_MODEL", "sideways")
    assert posture_mode("other") == "off"


def test_shipped_flag_shadows_master_corpus_only():
    path = ROOT / "configs" / "posture" / "flag.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["default"] == "off"
    assert data["projects"]["master_corpus"] == "shadow"
    assert "on" not in data["projects"].values()


def test_system_prompt_is_the_file_bytes(tmp_path, monkeypatch):
    _serve(tmp_path, monkeypatch, prompt="DO NOT EDIT\n")
    prompt, checkpoint = load_system_prompt()
    assert prompt == "DO NOT EDIT\n"
    assert checkpoint == "tinker://test/sampler_weights/posture"


def test_missing_serve_config_escalates(tmp_path, monkeypatch):
    _flags(tmp_path, monkeypatch, {"p1": "on"})
    monkeypatch.setenv("FORK_POSTURE_SERVE_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    rag, audit = _ctx()

    def sampler(**kwargs):
        raise AssertionError("sampler must not run without a serve config")

    out = apply_posture_at_final_answer(
        "current",
        rag_sys_msg=rag,
        messages=[],
        project_id="p1",
        audit_rec=audit,
        sampler=sampler,
        grounding_gate=lambda text, rag, messages: text,
    )
    assert out == "current"
    line = json.loads((tmp_path / "data" / "posture_shadow.jsonl").read_text(encoding="utf-8"))
    assert line["escalation"] == "client_unavailable"


def test_postprocess_delegates_the_final_step():
    from app.agents.runtime import _postprocess_answer

    src = inspect.getsource(_postprocess_answer)
    assert src.strip().endswith(")")
    assert "apply_posture_at_final_answer" in src
