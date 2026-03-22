"""Latency metrics endpoint."""

from fastapi import APIRouter, Depends

from ...persistence.prediction_store import PredictionStore
from ..auth import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/metrics")
async def get_metrics():
    """Get pipeline latency percentiles (P50/P95) from recent runs."""
    store = PredictionStore()
    return store.get_latency_percentiles()
