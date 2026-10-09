"""scripts/retire_task_env.py: strip retired env / secret NAMES from an ECS
task definition so deploy-aws.yml can register a clean revision. Synthetic
values only."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "retire_task_env", ROOT / "scripts" / "retire_task_env.py")
retire_task_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retire_task_env)


def _described():
    return {"taskDefinition": {
        "taskDefinitionArn": "arn:aws:ecs:xx:1:task-definition/web:7",
        "family": "web",
        "revision": 7,
        "status": "ACTIVE",
        "requiresAttributes": [{"name": "x"}],
        "compatibilities": ["FARGATE"],
        "registeredAt": "2026-01-01T00:00:00Z",
        "registeredBy": "arn:aws:iam::1:user/x",
        "cpu": "1024",
        "containerDefinitions": [{
            "name": "web",
            "image": "repo/img:latest",
            "environment": [{"name": "KEEP_ME", "value": "1"},
                            {"name": "OLD_PLAIN", "value": "synthetic"}],
            "secrets": [{"name": "OLD_SECRET", "valueFrom": "arn:aws:secretsmanager:xx:1:secret:s"},
                        {"name": "KEEP_SECRET", "valueFrom": "arn:aws:secretsmanager:xx:1:secret:k"}],
        }],
    }}


def test_retire_strips_names_and_read_only_fields():
    src = _described()
    new, removed = retire_task_env.retire(src, ["OLD_SECRET", "OLD_PLAIN"])
    assert removed == ["OLD_PLAIN", "OLD_SECRET"]
    c = new["containerDefinitions"][0]
    assert [e["name"] for e in c["environment"]] == ["KEEP_ME"]
    assert [e["name"] for e in c["secrets"]] == ["KEEP_SECRET"]
    for field in retire_task_env.READ_ONLY_FIELDS:
        assert field not in new
    assert new["family"] == "web" and new["cpu"] == "1024"
    assert c["image"] == "repo/img:latest"
    # input untouched
    assert len(src["taskDefinition"]["containerDefinitions"][0]["secrets"]) == 2


def test_nothing_to_retire_returns_none():
    assert retire_task_env.retire(_described(), ["ABSENT"]) == (None, [])
    assert retire_task_env.retire(_described()["taskDefinition"], []) == (None, [])


def test_cli_writes_only_when_changed_and_prints_names_not_values(tmp_path, capsys):
    td = tmp_path / "td.json"
    td.write_text(json.dumps(_described()))
    retired = tmp_path / "retired.json"
    retired.write_text(json.dumps({"retired": [{"name": "OLD_PLAIN", "reason": "r"}]}))
    out = tmp_path / "new.json"
    assert retire_task_env.main(["--retired", str(retired), "--taskdef", str(td),
                                 "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "OLD_PLAIN" in printed and "synthetic" not in printed
    assert "OLD_PLAIN" not in out.read_text()

    out2 = tmp_path / "none.json"
    retired.write_text(json.dumps({"retired": [{"name": "ABSENT", "reason": "r"}]}))
    retire_task_env.main(["--retired", str(retired), "--taskdef", str(td), "--out", str(out2)])
    assert not out2.exists()


def test_repo_retired_list_carries_the_scrub_secret():
    spec = json.loads((ROOT / "infra" / "retired_task_env.json").read_text())
    assert "RAG_SCRUB_RULES" in retire_task_env.retired_names(spec)
    assert all(e.get("reason") for e in spec["retired"])
    wf = (ROOT / ".github" / "workflows" / "deploy-aws.yml").read_text()
    assert wf.index("scripts/retire_task_env.py") < wf.index("name: Roll the web service")
