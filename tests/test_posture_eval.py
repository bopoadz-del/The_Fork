"""Posture eval harness: sealed files stay sealed; both paths re-sample together."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.posture_eval import (
    SCHEMA_VERSION,
    evaluate_items,
    load_sealed,
    main,
    mock_execute,
)


def _write_set(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_missing_sealed_sets_exit_wall_and_invent_nothing(tmp_path, capsys):
    ready = tmp_path / "ready.jsonl"
    blind = tmp_path / "blind.jsonl"
    code = main([
        "--mock",
        "--ready", str(ready),
        "--blind", str(blind),
        "--results", str(tmp_path / "out.json"),
    ])
    err = capsys.readouterr().err
    assert code == 2
    assert "No items were invented" in err
    assert not ready.exists()
    assert not blind.exists()
    saved = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert saved["sets"]["ready_gate_unseen"]["status"] == "missing"
    assert saved["sets"]["ready_gate_unseen"]["rows"] == []


def test_stdout_has_counts_not_item_text(tmp_path, capsys):
    question = "sealed-question-token-9f3a"
    ready = tmp_path / "ready.jsonl"
    blind = tmp_path / "blind.jsonl"
    _write_set(ready, [{"id": "r1", "question": question, "context_attached": True}])
    _write_set(blind, [{"id": "b1", "question": "other-sealed-token", "context_attached": False}])
    before = ready.read_bytes()
    code = main([
        "--mock",
        "--ready", str(ready),
        "--blind", str(blind),
        "--results", str(tmp_path / "out.json"),
    ])
    captured = capsys.readouterr()
    assert code == 0
    assert question not in captured.out
    assert question not in captured.err
    assert "other-sealed-token" not in captured.out
    assert "set=ready_gate_unseen" in captured.out
    assert "set=slm_blind" in captured.out
    assert "n=1" in captured.out
    assert ready.read_bytes() == before
    saved = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == SCHEMA_VERSION
    blob = json.dumps(saved)
    assert question not in blob
    assert saved["sets"]["ready_gate_unseen"]["rows"][0]["paths"] == ["current", "posture"]


def test_schema_change_resamples_both_paths(tmp_path):
    items, _ = load_sealed(_write_and_return(tmp_path))
    calls: list[tuple[str, ...]] = []

    def execute(item, *, paths):
        calls.append(paths)
        return mock_execute(item, paths=paths)

    first = evaluate_items(items, execute=execute, previous=None, schema_version=1)
    assert len(calls) == 1
    assert calls[0] == ("current", "posture")
    evaluate_items(
        items,
        execute=execute,
        previous={"schema_version": 1, "rows": first},
        schema_version=1,
    )
    assert len(calls) == 1
    evaluate_items(
        items,
        execute=execute,
        previous={"schema_version": 1, "rows": first},
        schema_version=2,
    )
    assert len(calls) == 2
    assert calls[1] == ("current", "posture")


def _write_and_return(tmp_path: Path) -> Path:
    path = tmp_path / "one.jsonl"
    _write_set(path, [{"question": "shape-only", "context_attached": True}])
    return path


def test_malformed_sealed_line_does_not_rewrite_the_file(tmp_path):
    path = tmp_path / "bad.jsonl"
    original = '{"note": "no question here"}\n'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(Exception):
        load_sealed(path)
    assert path.read_text(encoding="utf-8") == original


def test_mock_does_not_require_tinker(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TINKER_API_KEY", raising=False)
    ready = tmp_path / "ready.jsonl"
    _write_set(ready, [{
        "question": "q",
        "context_attached": True,
        "mock_graders": {
            "available": True,
            "fact_leak": {"current": 0, "posture": 0},
            "citation_validity": {"current": 1, "posture": 1},
        },
    }])
    blind = tmp_path / "blind.jsonl"
    _write_set(blind, [{"question": "q2", "context_attached": True}])
    code = main([
        "--mock",
        "--ready", str(ready),
        "--blind", str(blind),
        "--results", str(tmp_path / "out.json"),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "current.fact_leak=0" in out or "current.fact_leak=0.0" in out
    assert "posture.citation_validity=1" in out or "posture.citation_validity=1.0" in out
