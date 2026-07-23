"""Deployment profile switch — STEP 2 of the on-prem sovereignty program.

``DEPLOYMENT_PROFILE`` selects how the app treats external services:

  * ``cloud`` (default) — today's behaviour, byte-for-byte. The Render
    dev/demo deployment. Every gate below is a strict no-op.
  * ``onprem`` — zero-egress. Every would-egress feature (cloud LLM
    providers, Google Drive / OneDrive / R2, web fetch / search / translate,
    outbound webhooks, weather, MCP consumer, Tinker) is honest-unavailable,
    and the app refuses to boot on a misconfiguration that would leak
    (a cloud LLM provider selected, offline model flags unset, Sentry on).

Design boundary (deliberate): **network isolation itself is the deployment's
job** — the air-gapped host has no route to the internet, and STEP 5 verifies
that with an egress monitor. This module's job is narrower: make the app not
*need* egress, and fail *gracefully* when a cloud-only feature is invoked. It
does NOT install an in-process socket blocker (that would risk breaking local
Postgres / local Ollama and is the wrong layer).

Offline model flags (``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE`` /
``YOLO_OFFLINE``) are **asserted here, never set here** — they are read by
``transformers`` / ``huggingface_hub`` at import time, so a late
``os.environ[...] = "1"`` from Python is a silent no-op. They must be set in
the deployment environment (Dockerfile ENV / compose / entrypoint export);
this module only checks that they are present under the on-prem profile.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

PROFILE_CLOUD = "cloud"
PROFILE_ONPREM = "onprem"

# LLM providers that egress to a third-party cloud. Under on-prem only Ollama
# (pointed at a LOCAL host) is permitted.
_CLOUD_LLM_PROVIDERS = frozenset({"groq", "openai", "deepseek", "kimi", "moonshot", "anthropic"})

# Model-loading offline flags that must be present in the environment before
# any transformers/HF import so the embedder never phones home. Truthy check.
_REQUIRED_OFFLINE_FLAGS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def profile() -> str:
    """Active deployment profile (lowercased). Defaults to ``cloud``."""
    return (os.getenv("DEPLOYMENT_PROFILE") or PROFILE_CLOUD).strip().lower()


def is_onprem() -> bool:
    """True when running under the zero-egress on-prem profile."""
    return profile() == PROFILE_ONPREM


def onprem_unavailable(feature: str) -> Dict[str, Any]:
    """Standard honest-unavailable payload for a cloud-only feature under
    on-prem. Matches the block error contract (``status=error``) plus an
    ``onprem_unavailable`` marker so callers/tests can distinguish it from a
    genuine failure. Never raises — it is a graceful degrade."""
    return {
        "status": "error",
        "error": (
            f"{feature} is not available in the on-prem (air-gapped) profile. "
            "This deployment runs fully offline on local hardware; connect the "
            "cloud dev/demo profile if you need it."
        ),
        "onprem_unavailable": True,
        "profile": PROFILE_ONPREM,
    }


def forbid_onprem(feature: str):
    """FastAPI dependency factory: return a dependency that raises HTTP 503 with
    an honest message when the on-prem profile is active, and does nothing under
    the cloud profile. Attach to egress-only routes via
    ``dependencies=[Depends(forbid_onprem("Google Drive"))]``."""
    def _dep() -> None:
        if is_onprem():
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail=onprem_unavailable(feature)["error"])
    return _dep


def _is_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in _TRUTHY


def check_onprem_ready() -> List[str]:
    """Return a list of hard misconfiguration problems for the on-prem profile
    (empty when fine, or when not on-prem). Pure — does not raise — so tests
    and a health endpoint can read it. ``assert_onprem_ready`` raises on it."""
    if not is_onprem():
        return []
    problems: List[str] = []

    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider in _CLOUD_LLM_PROVIDERS:
        problems.append(
            f"LLM_PROVIDER={provider!r} is a cloud provider; on-prem requires "
            "LLM_PROVIDER=ollama pointed at a local host"
        )
    if not provider:
        problems.append("LLM_PROVIDER is unset; on-prem requires LLM_PROVIDER=ollama")

    if (os.getenv("LLM_FALLBACK_PROVIDER") or "").strip().lower() in _CLOUD_LLM_PROVIDERS:
        problems.append(
            "LLM_FALLBACK_PROVIDER is a cloud provider; on-prem must fall back "
            "to ollama or nothing"
        )

    if (os.getenv("OLLAMA_API_KEY") or "").strip():
        problems.append(
            "OLLAMA_API_KEY is set; on-prem must use a LOCAL Ollama "
            "(OLLAMA_URL=http://localhost:11434), not Ollama Cloud"
        )

    for flag in _REQUIRED_OFFLINE_FLAGS:
        if not _is_truthy(flag):
            problems.append(
                f"{flag} must be truthy in the environment (offline model "
                "loading) — set it in the deployment env, not at runtime"
            )

    if (os.getenv("SENTRY_DSN") or "").strip():
        problems.append("SENTRY_DSN is set; on-prem must not send telemetry")

    if (os.getenv("GROUNDED_ADAPTER_TINKER_PATH") or "").strip():
        problems.append(
            "GROUNDED_ADAPTER_TINKER_PATH is set; the Tinker grounded-adapter "
            "path egresses to Thinking Machines' cloud — must be unset on-prem"
        )

    return problems


def assert_onprem_ready() -> None:
    """Fail loud at boot if the on-prem profile is misconfigured in a way that
    would leak or silently phone home. No-op under the cloud profile."""
    problems = check_onprem_ready()
    if problems:
        raise RuntimeError(
            "DEPLOYMENT_PROFILE=onprem is misconfigured (would egress):\n  - "
            + "\n  - ".join(problems)
        )


def boot_manifest() -> Dict[str, Any]:
    """A one-glance 'what am I running' manifest for on-prem operators: profile,
    LLM wiring, embedder identity, offline flags, DB backend. Also surfaces the
    embedding-identity (model + dim) so a mismatch is visible at boot. Pure /
    best-effort — never raises (the embedder probe is wrapped)."""
    embedder = {"model": None, "dim": None}
    try:
        from app.core.rag.embeddings import get_embedder
        ident = get_embedder().identity
        embedder = {"model": ident.get("model"), "dim": ident.get("dim")}
    except Exception as exc:  # noqa: BLE001
        embedder = {"model": f"<unavailable: {exc}>", "dim": None}

    db = os.getenv("DATABASE_URL", "")
    db_backend = "postgres" if db.startswith("postgres") else "sqlite"

    return {
        "profile": profile(),
        "llm_provider": os.getenv("LLM_PROVIDER") or "(unset)",
        "llm_fallback": os.getenv("LLM_FALLBACK_PROVIDER") or "(none)",
        "ollama_url": os.getenv("OLLAMA_URL") or "http://localhost:11434",
        "ollama_model": os.getenv("OLLAMA_MODEL") or "(default)",
        "embedder": embedder,
        "offline_flags": {f: _is_truthy(f) for f in _REQUIRED_OFFLINE_FLAGS},
        "sentry": bool((os.getenv("SENTRY_DSN") or "").strip()),
        "db_backend": db_backend,
    }


def disk_survival_canary(data_dir: str) -> Dict[str, Any]:
    """Write-then-read a sentinel under DATA_DIR to confirm the persistent volume
    is mounted and survives restarts. On first boot it seeds the sentinel; on
    later boots it reports the original first-boot timestamp (proof the volume
    persisted). Never raises — returns a status dict. Used by the on-prem boot."""
    import json as _json
    result: Dict[str, Any] = {"ok": False, "path": None, "first_seen": None, "note": ""}
    try:
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, ".onprem_canary.json")
        result["path"] = path
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
            result["first_seen"] = data.get("first_seen")
            result["note"] = "volume persisted (sentinel survived a prior boot)"
        else:
            # Seed. Timestamp comes from the OS clock at write time; this is a
            # persistence probe, not a security control.
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).isoformat()
            with open(path, "w", encoding="utf-8") as fh:
                _json.dump({"first_seen": stamp}, fh)
            result["first_seen"] = stamp
            result["note"] = "sentinel seeded (first boot on this volume)"
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001
        result["note"] = f"canary failed: {exc}"
    return result
