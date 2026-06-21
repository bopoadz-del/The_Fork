from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.dependencies import (
    MONITORING_AVAILABLE,
    get_monitoring_block,
    require_api_key,
)
from app.infra.monitoring import block_metrics
from app.routers.health import health_v1

router = APIRouter()


@router.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus text-format exposition (PR #98).

    Intentionally unauthenticated — Prometheus scrapers typically don't
    auth, and the counters exposed here are non-sensitive request/
    response totals (method + status label set). Sensitive operational
    data stays behind /v1/metrics (admin-gated). When prometheus-client
    isn't installed, returns an empty 503 so the dev environment doesn't
    refuse to import this router.
    """
    try:
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    except Exception:
        raise HTTPException(status_code=503, detail="prometheus-client not installed")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


class RecordMetricsRequest(BaseModel):
    """Typed body for a provider-call metric. Only these fields reach the
    monitoring block — no arbitrary client dict is splatted into execute()."""
    provider: Optional[str] = None
    latency_ms: float = 0
    success: bool = True
    error_type: Optional[str] = None


@router.get("/v1/metrics")
def get_block_metrics(auth: dict = Depends(require_api_key)):
    """Per-block execution timings (from UniversalBlock.execute).

    Admin-only — the snapshot exposes execution counts, latencies, and
    error counts per block, which is operational data not safe to
    return to anonymous callers. PR #98 added the auth gate after the
    pilot-readiness audit flagged it. For unauthenticated scraping use
    the Prometheus ``/metrics`` endpoint instead, which intentionally
    exposes a narrower counter set.
    """
    return block_metrics.snapshot()


@router.get("/v1/leaderboard")
async def get_leaderboard(auth: dict = Depends(require_api_key)):
    """Provider reliability leaderboard"""
    if not MONITORING_AVAILABLE:
        raise HTTPException(status_code=503, detail="Monitoring not available")
    block = get_monitoring_block()
    return await block.execute({"action": "leaderboard"})


@router.get("/v1/recommend")
async def recommend_provider(auth: dict = Depends(require_api_key)):
    """AI-powered provider recommendation"""
    if not MONITORING_AVAILABLE:
        raise HTTPException(status_code=503, detail="Monitoring not available")
    block = get_monitoring_block()
    return await block.execute({"action": "recommend"})


@router.get("/v1/predict")
async def predictive_failover(auth: dict = Depends(require_api_key)):
    """Predict potential failures before they happen"""
    if not MONITORING_AVAILABLE:
        raise HTTPException(status_code=503, detail="Monitoring not available")
    block = get_monitoring_block()
    return await block.execute({"action": "predictive_failover"})


@router.post("/v1/metrics/record")
async def record_metrics(
    request: RecordMetricsRequest, auth: dict = Depends(require_api_key)
):
    """Record call metrics for tracking"""
    if not MONITORING_AVAILABLE:
        return {"status": "no_op"}
    block = get_monitoring_block()
    payload = {"action": "record_call", **request.model_dump(exclude_none=True)}
    return await block.execute(payload)
