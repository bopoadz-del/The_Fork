from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import (
    MONITORING_AVAILABLE,
    get_monitoring_block,
    require_api_key,
)
from app.routers.health import health_v1

router = APIRouter()


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
async def record_metrics(request: dict, auth: dict = Depends(require_api_key)):
    """Record call metrics for tracking"""
    if not MONITORING_AVAILABLE:
        return {"status": "no_op"}
    block = get_monitoring_block()
    return await block.execute({"action": "record_call", **request})
