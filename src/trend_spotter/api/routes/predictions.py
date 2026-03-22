"""Prediction endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...persistence.prediction_store import PredictionStore
from ..auth import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/predictions")
async def list_predictions():
    """List all stored predictions with evaluation status."""
    store = PredictionStore()
    preds = store.get_all_predictions()
    summary = []
    for pred in preds:
        summary.append({
            "prediction_id": pred["prediction_id"],
            "field": pred["field"],
            "trend": pred["trend_label"],
            "classification": pred["classification"],
            "trajectory": pred["trajectory"],
            "scores": {
                "acceleration": pred["acceleration_score"],
                "durability": pred["durability_score"],
            },
            "created_at": pred["created_at"],
            "evaluation_30d": pred.get("evaluation_30d"),
            "evaluation_90d": pred.get("evaluation_90d"),
        })
    return {"count": len(summary), "predictions": summary}


@router.get("/predictions/{prediction_id}")
async def get_prediction(prediction_id: str):
    """Get a single prediction by ID."""
    store = PredictionStore()
    preds = store.get_all_predictions()
    for pred in preds:
        if pred["prediction_id"] == prediction_id:
            return pred
    raise HTTPException(status_code=404, detail="Prediction not found")
