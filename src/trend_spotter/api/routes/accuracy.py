"""Accuracy metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...evaluation.evaluator import (
    check_thresholds,
    compute_accuracy_metrics,
    compute_signal_correlation,
)
from ...persistence.prediction_store import PredictionStore
from ..auth import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/accuracy")
async def get_accuracy(
    horizon: str = Query("30d", pattern="^(30d|90d)$"),
):
    """Get accuracy metrics for evaluated predictions."""
    store = PredictionStore()
    evaluated = store.get_evaluated_predictions(horizon)

    if not evaluated:
        return {
            "horizon": horizon,
            "message": f"No evaluated predictions at {horizon} yet.",
            "total_predictions": store.get_prediction_count(),
        }

    metrics = compute_accuracy_metrics(evaluated, horizon)

    if horizon == "90d" and len(evaluated) >= 10:
        metrics["signal_correlation"] = compute_signal_correlation(evaluated)

    warnings = check_thresholds(metrics, horizon)
    if warnings:
        metrics["warnings"] = warnings

    return metrics
