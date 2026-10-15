"""Auto-deploy runs only for a push to this repository's main.

A fork pull request whose branch is named main must not satisfy the
workflow_run path. Manual dispatch stays. The post-deploy check waits for
ECS stability and for /livez to report the shipped commit, using a
repository variable for the origin.
"""
from __future__ import annotations

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy-aws.yml"


def test_workflow_run_deploys_only_a_same_repo_push_to_main():
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "aws ecs wait services-stable" in text
    assert "timeout 600 aws ecs wait services-stable" in text
    assert "vars.PUBLIC_BASE_URL" in text
    assert "/livez" in text
    assert "build_sha" in text
    assert "FACTORY_DEPLOY_KEY" not in text
    assert "https://theshovel.ai" not in text
