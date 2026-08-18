"""Production blueprint must match the live Kimi + Groq ladder.

render.yaml is the reviewable production surface. A committed Ollama intent
model, an enabled ingest worker with no worker service, or a 90s stream
timeout would ship as "Apply Blueprint" and break chat / uploads. These
tests pin the contract that code review cannot see in a comment.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _web_env() -> dict[str, dict]:
    blueprint = yaml.safe_load((REPO / "render.yaml").read_text(encoding="utf-8"))
    web = next(s for s in blueprint["services"] if s.get("name") == "the-fork")
    out: dict[str, dict] = {}
    for item in web.get("envVars") or []:
        key = item.get("key")
        if key:
            out[key] = item
    return out


def test_blueprint_does_not_pin_an_ollama_intent_model():
    env = _web_env()
    intent = env.get("ORCHESTRATOR_INTENT_MODEL") or {}
    value = (intent.get("value") or "").strip()
    assert ":" not in value, (
        f"ORCHESTRATOR_INTENT_MODEL={value!r} is an Ollama-style id. Cloud "
        "prod is Kimi; Moonshot 400s that name every chat turn and predefined "
        "schedule routing never fires. Leave it unset (provider default) or "
        "pin a model the PRIMARY provider serves."
    )


def test_blueprint_does_not_commit_ollama_as_cloud_provider():
    env = _web_env()
    for key in ("LLM_PROVIDER", "LLM_FALLBACK_PROVIDER"):
        value = ((env.get(key) or {}).get("value") or "").strip().lower()
        assert value != "ollama", (
            f"{key}={value!r} in render.yaml. Cloud ladder is Kimi primary + "
            "Groq fallback; Ollama is on-prem only."
        )


def test_blueprint_ingest_worker_stays_off():
    env = _web_env()
    item = env.get("INGEST_WORKER_ENABLED")
    assert item is not None, (
        "INGEST_WORKER_ENABLED must be pinned false — a leftover dashboard "
        "true enqueues uploads onto a queue nobody drains."
    )
    value = str(item.get("value") or "").strip().lower()
    assert value in {"", "0", "false", "no"}, (
        f"INGEST_WORKER_ENABLED={value!r}; no worker service is deployed."
    )


def test_blueprint_public_base_url_is_the_client_hostname():
    env = _web_env()
    item = env.get("PUBLIC_BASE_URL")
    assert item is not None, "PUBLIC_BASE_URL missing from render.yaml"
    assert item.get("value") == "https://theshovel.ai"


def test_blueprint_chat_stream_timeout_covers_kimi_reasoning():
    env = _web_env()
    item = env.get("CHAT_STREAM_TIMEOUT_SECONDS")
    assert item is not None, "CHAT_STREAM_TIMEOUT_SECONDS missing from render.yaml"
    seconds = float(item["value"])
    assert seconds >= 240, (
        f"CHAT_STREAM_TIMEOUT_SECONDS={seconds} is below the 240s Kimi "
        "reasoning-burst floor (PLATFORM_HEALTH_REPORT)."
    )


def test_blueprint_auto_deploy_stays_off():
    text = (REPO / "render.yaml").read_text(encoding="utf-8")
    flags = re.findall(r"^\s*autoDeploy:\s*(\S+)", text, re.MULTILINE)
    assert flags, "render.yaml declares no autoDeploy"
    for flag in flags:
        assert flag.lower() in {"false", "no"}, (
            f"autoDeploy is {flag!r}; git-push-equals-prod is off until CI is green."
        )
