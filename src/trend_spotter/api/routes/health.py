"""Health check endpoint."""

from fastapi import APIRouter

from ...persistence.prediction_store import PredictionStore
from ..models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    store = PredictionStore()
    return HealthResponse(prediction_count=store.get_prediction_count())
