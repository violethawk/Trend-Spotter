"""Automated evaluation and weight tuning scheduler (Phase 5/6).

This module is designed to be invoked as a cron job. Each invocation:
1. Evaluates all matured predictions at 30d and 90d horizons.
2. Runs accuracy threshold checks and records warnings.
3. Triggers weight tuning if enough 90d-evaluated predictions exist.

Usage (cron):
    0 3 * * * python -m trend_spotter schedule

The scheduler is idempotent: re-running processes only unevaluated
matured predictions and only tunes weights when sample size allows.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..config import load_config, Config
from .evaluator import (
    evaluate_prediction,
    compute_accuracy_metrics,
    compute_signal_correlation,
    check_thresholds,
)
from ..persistence.prediction_store import PredictionStore
from .weight_tuning import compute_updated_weights

logger = logging.getLogger(__name__)


def run_schedule() -> Dict[str, Any]:
    """Run the full scheduled evaluation and tuning cycle.

    Returns:
        Summary dict with evaluation results, warnings, and tuning status.
    """
    try:
        config = load_config()
    except RuntimeError as e:
        return {"error": str(e)}

    store = PredictionStore()
    summary: Dict[str, Any] = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "evaluations": {},
        "warnings": [],
        "weight_tuning": None,
    }

    # --- Evaluate matured predictions at both horizons ---
    for horizon in ("30d", "90d"):
        eval_summary = _evaluate_horizon(store, config, horizon)
        summary["evaluations"][horizon] = eval_summary

        # Run threshold checks if we have enough evaluated data
        evaluated = store.get_evaluated_predictions(horizon)
        if len(evaluated) >= 5:
            metrics = compute_accuracy_metrics(evaluated, horizon)
            warnings = check_thresholds(metrics, horizon)
            if warnings:
                summary["warnings"].extend(warnings)

            # Record the evaluation run
            store.write_evaluation_run(
                horizon=horizon,
                predictions_evaluated=eval_summary["evaluated"],
                correct=eval_summary["correct"],
                incorrect=eval_summary["incorrect"],
                ambiguous=eval_summary["ambiguous"],
                errors=eval_summary["errors"],
                warnings=warnings or None,
            )

    # --- Phase 6: Weight tuning (requires >= 50 evaluated 90d predictions) ---
    evaluated_90d = store.get_evaluated_predictions("90d")
    non_ambiguous = [
        p for p in evaluated_90d
        if p.get("evaluation_90d") in ("correct", "incorrect")
    ]

    if len(non_ambiguous) >= 50:
        summary["weight_tuning"] = _try_weight_tuning(
            store, evaluated_90d, non_ambiguous
        )
    else:
        summary["weight_tuning"] = {
            "status": "waiting",
            "reason": (
                f"Need 50 non-ambiguous 90d evaluations, "
                f"have {len(non_ambiguous)}"
            ),
        }

    return summary


def _evaluate_horizon(
    store: PredictionStore,
    config: Config,
    horizon: str,
) -> Dict[str, Any]:
    """Evaluate all matured predictions for a single horizon."""
    matured = store.get_matured_predictions(horizon)
    result = {
        "evaluated": 0,
        "correct": 0,
        "incorrect": 0,
        "ambiguous": 0,
        "errors": 0,
        "details": [],
    }

    if not matured:
        return result

    for prediction in matured:
        try:
            eval_result = evaluate_prediction(prediction, horizon, config)
            store.write_evaluation(
                eval_result.prediction_id,
                eval_result.horizon,
                eval_result.outcome,
                eval_result.reasoning,
            )
            result["evaluated"] += 1
            result[eval_result.outcome] += 1
            result["details"].append({
                "prediction_id": eval_result.prediction_id,
                "trend": prediction["trend_label"],
                "classification": prediction["classification"],
                "outcome": eval_result.outcome,
            })
        except Exception as exc:
            logger.warning(
                "Failed to evaluate %s: %s",
                prediction["prediction_id"], exc,
            )
            result["errors"] += 1
            result["details"].append({
                "prediction_id": prediction["prediction_id"],
                "error": str(exc),
            })

    return result


def _try_weight_tuning(
    store: PredictionStore,
    all_evaluated_90d: List[Dict[str, Any]],
    non_ambiguous: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Attempt weight tuning from 90d evaluation data."""
    correlation = compute_signal_correlation(all_evaluated_90d)

    # Get current weights (tuned or baseline)
    current_weights = store.get_current_weights()
    if current_weights is None:
        from ..scoring.durability import DURABILITY_WEIGHTS
        current_weights = dict(DURABILITY_WEIGHTS)

    # Compute accuracy before tuning
    accuracy_before = compute_accuracy_metrics(all_evaluated_90d, "90d")

    # Run tuning algorithm
    new_weights, changelog = compute_updated_weights(
        correlation, current_weights
    )

    if not changelog.get("applied"):
        return {
            "status": "skipped",
            "reason": changelog.get("reason", "No changes needed"),
        }

    # Persist new weights
    version = store.write_weight_version(
        weights=new_weights,
        correlation=correlation,
        sample_size=len(non_ambiguous),
        accuracy_before=accuracy_before,
    )

    return {
        "status": "applied",
        "version": version,
        "changes": changelog["changes"],
        "reason": changelog["reason"],
    }
