from datetime import datetime, timezone
from fastapi import APIRouter, Response

from app.blocks import BLOCK_REGISTRY, FAILED_BLOCKS
from app.core.health_probes import probe_database, probe_embedder
from app.dependencies import block_instances, MONITORING_AVAILABLE, get_monitoring_block
from app.infra.monitoring import get_observability_health_payload

router = APIRouter()


def _evaluate_health() -> dict:
    """Build the health payload from EVALUATED capability, not hardcoded state
    (audit §8.5/§8.6). ``checks.database`` is a real SELECT 1; ``checks.embedder``
    reports warm-load state without triggering a load. ``status`` is
    ``healthy`` only when the DB round-trips — otherwise ``degraded`` — instead
    of the previous hardcoded ``"healthy"``.
    """
    db = probe_database()
    emb = probe_embedder()
    return {
        # LIVENESS status: the process is up. DB reachability is reflected
        # truthfully as degraded rather than failing this endpoint — /health is
        # Render's liveness probe, so a transient DB blip must NOT restart a
        # live service mid-incident. Use /ready for a hard readiness gate.
        "status": "healthy" if db["ok"] else "degraded",
        "checks": {
            "database": db,
            "embedder": emb,
        },
        "blocks_loaded": len(block_instances),
        "blocks_available": len(BLOCK_REGISTRY),
        "blocks_failed": {
            name: reason for name, reason in sorted(FAILED_BLOCKS.items())
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/livez")
def livez():
    """Process liveness ONLY — deliberately touches no database.

    Exists because the database moved to Neon (2026-08-09), which bills by
    compute-time and suspends when idle. ``/health`` and ``/ready`` both run
    a real ``SELECT 1``, and Render polls its health path continuously — so
    pointing the platform health check at either would wake Neon on every
    probe and hold it awake around the clock, which is precisely the cost
    that motivated leaving Render Postgres.

    This answers the only question a liveness probe should ask: is the
    process up and serving HTTP? Dependency health is ``/health``
    (evaluated, always 200) and ``/ready`` (fail-closed 503). Keep this
    handler free of I/O — every dependency added here becomes a wake-up
    on a timer.
    """
    return {"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/health")
def health():
    """Liveness + evaluated component health.

    Reports a real DB round-trip and embedder warm-load state (not a hardcoded
    "healthy"). Always HTTP 200 — this is the liveness probe Render is wired to
    (``healthCheckPath: /health``); a DB blip surfaces as ``status:"degraded"``
    rather than a non-200 that would restart the live service. ``blocks_failed``
    still surfaces optional-dep drops.
    """
    return _evaluate_health()


@router.get("/ready")
def ready(response: Response):
    """Readiness gate: HTTP 503 when a required dependency (the database) is
    unreachable, 200 otherwise. Use this for deploy gates / load-balancer
    readiness / the smoke suite — it fails closed, unlike /health (liveness).
    """
    payload = _evaluate_health()
    payload["ready"] = payload["checks"]["database"]["ok"]
    if not payload["ready"]:
        response.status_code = 503
    return payload


@router.get("/stats")
def stats():
    """Platform stats."""
    return {
        "blocks": [name for name in BLOCK_REGISTRY.keys() if not name.startswith("container_")],
        "total_blocks": len(BLOCK_REGISTRY),
        "version": "2.0.0",
    }


@router.get("/v1/health")
async def health_v1():
    """Health check (v1 API) with observability enrichment."""
    payload = health()
    payload.update(await get_observability_health_payload())
    return payload


@router.get("/v1/system/health")
async def full_health():
    """Complete system health with predictions."""
    if not MONITORING_AVAILABLE:
        return await health_v1()
    block = get_monitoring_block()
    return await block.execute({"action": "health_report"})
