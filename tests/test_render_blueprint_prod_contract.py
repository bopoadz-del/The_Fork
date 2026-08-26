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


def test_blueprint_chat_turn_idle_timeout_present():
    """A7 idle-between-progress watchdog must be declared on the web service."""
    env = _web_env()
    item = env.get("CHAT_TURN_IDLE_TIMEOUT_SEC")
    assert item is not None, "CHAT_TURN_IDLE_TIMEOUT_SEC missing from render.yaml"
    seconds = float(item["value"])
    assert seconds > 0, "idle timeout should be on in prod-like blueprint"
    assert seconds < float(env["CHAT_STREAM_TIMEOUT_SECONDS"]["value"]), (
        "idle must be stricter than wall-clock or it never fires first"
    )


def test_blueprint_auto_deploy_matches_the_live_service():
    """autoDeploy was switched ON in the dashboard on 2026-08-23.

    This test previously pinned it OFF ("git-push-equals-prod is off until CI
    is green"). That policy no longer holds, and a test that pins a policy the
    service has abandoned does not protect anything -- it just goes red on the
    commit that tells the truth.

    What it guards now is DRIFT: render.yaml is applied onto the running
    service, so a stale value here is an outage waiting for someone to click
    Apply Blueprint. Four values in this file have already disagreed with
    production (plan, upload cap, disk size, autoDeploy) and a fifth would
    have taken retrieval down -- see the embedding-model test below.

    CONSEQUENCE, recorded here because it changes how work must be done:
    Render starts building on the push, so GitHub Actions runs ALONGSIDE the
    deploy rather than gating it. A commit that goes red in CI is already live
    by the time the run finishes.
    """
    text = (REPO / "render.yaml").read_text(encoding="utf-8")
    flags = re.findall(r"^\s*autoDeploy:\s*(\S+)", text, re.MULTILINE)
    assert flags, "render.yaml declares no autoDeploy"
    for flag in flags:
        assert flag.lower() in {"true", "yes"}, (
            f"autoDeploy is {flag!r} but the live service has it ON. Applying "
            "this blueprint would silently switch shipping off while every "
            "push still looked like it was deploying."
        )


# The corpus is embedded with this model and every row carries the stamp.
# VectorStore._verify_embedding_identity refuses a namespace whose stamp
# disagrees with the configured model, so this is not a preference -- it is
# the identity of 172,809 existing vectors.
LIVE_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


def test_blueprint_embedding_model_matches_the_corpus():
    """render.yaml declared minishlab/potion-base-8M while production ran
    BAAI/bge-small-en-v1.5 and every chunk was stamped with it:

        BAAI/bge-small-en-v1.5   dim 384   normalized True   172,809 chunks

    Applying the blueprint would have set the wrong model, the identity guard
    would have refused the namespace, and retrieval would have gone down
    across the whole corpus until every chunk was re-embedded. The prose above
    that key in render.yaml describes this mechanism exactly -- and the value
    underneath it was still wrong, because nothing tested it.

    Changing this constant is a re-embedding decision, not a config edit.
    """
    env = _web_env()
    item = env.get("RAG_EMBEDDING_MODEL")
    assert item is not None, "RAG_EMBEDDING_MODEL missing from render.yaml"
    assert item.get("value") == LIVE_EMBEDDING_MODEL, (
        f"render.yaml declares {item.get('value')!r} but the live corpus is "
        f"embedded with {LIVE_EMBEDDING_MODEL!r}. Applying this blueprint "
        "would take retrieval down for every chunk."
    )
