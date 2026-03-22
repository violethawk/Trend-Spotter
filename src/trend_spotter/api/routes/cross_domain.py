"""Cross-domain analysis endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...config import load_config
from ...cross_domain import detect_cross_domain_trends
from ...persistence.prediction_store import PredictionStore
from ...pipeline import run_pipeline
from ..auth import verify_api_key
from ..models import CrossDomainRequest

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/cross-domain")
async def analyze_cross_domain(req: CrossDomainRequest):
    """Detect trends emerging across multiple domains."""
    config = load_config()
    store = PredictionStore()

    # Optionally scan fields missing recent data
    if req.scan_missing:
        recent = store.get_recent_predictions(req.lookback_days)
        fields_with_data = {p["field"] for p in recent}
        for field in req.fields:
            if field not in fields_with_data:
                run_pipeline(field, "7d", config)

    results = detect_cross_domain_trends(
        store, config, req.fields, req.lookback_days
    )

    return {
        "cross_domain_trends": [t.to_dict() for t in results],
        "fields_analyzed": req.fields,
        "lookback_days": req.lookback_days,
    }
