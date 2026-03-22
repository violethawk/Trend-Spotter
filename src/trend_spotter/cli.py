"""Command line interface for Trend Spotter.

Thin CLI layer that delegates to pipeline.run_pipeline() and other
subcommand handlers. All reusable logic lives in dedicated modules.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Dict, List

from .config import load_config
from .persistence.prediction_store import PredictionStore
from .evaluation.evaluator import (
    evaluate_prediction,
    compute_accuracy_metrics,
    compute_signal_correlation,
    check_thresholds,
)
from .evaluation.scheduler import run_schedule
from .cross_domain import detect_cross_domain_trends
from .pipeline import run_pipeline

logger = logging.getLogger(__name__)


def run(field: str, window: str, descriptions: bool = False) -> None:
    """Run the full Trend Spotter pipeline and print output as JSON."""
    try:
        config = load_config()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    output = run_pipeline(field, window, config, generate_descriptions=descriptions)
    print(json.dumps(output, indent=2))


def run_evaluate(horizon: str) -> None:
    """Evaluate matured predictions and print results as JSON."""
    try:
        config = load_config()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    prediction_store = PredictionStore()
    matured = prediction_store.get_matured_predictions(horizon)

    if not matured:
        output = {
            "horizon": horizon,
            "evaluated": 0,
            "message": f"No predictions have matured at {horizon}. "
                       "Predictions need to be at least "
                       f"{'30' if horizon == '30d' else '90'} days old.",
            "results": [],
        }
        print(json.dumps(output, indent=2))
        return

    results = []
    for prediction in matured:
        try:
            eval_result = evaluate_prediction(prediction, horizon, config)
            prediction_store.write_evaluation(
                eval_result.prediction_id,
                eval_result.horizon,
                eval_result.outcome,
                eval_result.reasoning,
            )
            results.append({
                "prediction_id": eval_result.prediction_id,
                "trend": prediction["trend_label"],
                "field": prediction["field"],
                "classification": prediction["classification"],
                "outcome": eval_result.outcome,
                "current_signals": eval_result.current_signal_count,
                "original_signals": eval_result.original_signal_count,
                "growth_delta": eval_result.growth_delta,
                "signals_with_growth": eval_result.signals_with_growth,
                "reasoning": eval_result.reasoning,
            })
        except Exception as exc:
            logger.warning("Failed to evaluate prediction %s: %s",
                           prediction["prediction_id"], exc)
            results.append({
                "prediction_id": prediction["prediction_id"],
                "trend": prediction["trend_label"],
                "error": str(exc),
            })

    output = {
        "horizon": horizon,
        "evaluated": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2))


def run_accuracy(horizon: str) -> None:
    """Print accuracy metrics for evaluated predictions."""
    prediction_store = PredictionStore()
    evaluated = prediction_store.get_evaluated_predictions(horizon)

    if not evaluated:
        output = {
            "horizon": horizon,
            "message": f"No evaluated predictions at {horizon} yet.",
            "total_predictions": prediction_store.get_prediction_count(),
        }
        print(json.dumps(output, indent=2))
        return

    metrics = compute_accuracy_metrics(evaluated, horizon)

    if horizon == "90d" and len(evaluated) >= 10:
        metrics["signal_correlation"] = compute_signal_correlation(evaluated)

    warnings = check_thresholds(metrics, horizon)
    if warnings:
        metrics["warnings"] = warnings

    all_preds = prediction_store.get_all_predictions()
    eval_key = f"evaluation_{horizon}"
    pending = sum(1 for p in all_preds if not p.get(eval_key))
    metrics["pending_evaluations"] = pending
    metrics["total_predictions"] = len(all_preds)

    print(json.dumps(metrics, indent=2))


def run_predictions() -> None:
    """Print all stored predictions with their evaluation status."""
    prediction_store = PredictionStore()
    all_preds = prediction_store.get_all_predictions()

    if not all_preds:
        print(json.dumps({"message": "No predictions stored yet.", "count": 0}, indent=2))
        return

    summary = []
    for pred in all_preds:
        entry = {
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
        }
        if pred.get("evaluation_30d_reasoning"):
            entry["reasoning_30d"] = pred["evaluation_30d_reasoning"]
        if pred.get("evaluation_90d_reasoning"):
            entry["reasoning_90d"] = pred["evaluation_90d_reasoning"]
        summary.append(entry)

    output = {
        "count": len(summary),
        "predictions": summary,
    }
    print(json.dumps(output, indent=2))


def run_cross_domain(fields: List[str], lookback: int, scan_first: bool) -> None:
    """Detect trends emerging across multiple domains."""
    try:
        config = load_config()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    store = PredictionStore()

    # Optionally scan fields that lack recent predictions
    if scan_first:
        recent = store.get_recent_predictions(lookback)
        fields_with_data = {p["field"] for p in recent}
        for field in fields:
            if field not in fields_with_data:
                logger.info("Scanning %s (no recent predictions)", field)
                run_pipeline(field, "7d", config)

    results = detect_cross_domain_trends(store, config, fields, lookback)

    if not results:
        output = {
            "cross_domain_trends": [],
            "fields_analyzed": fields,
            "lookback_days": lookback,
            "message": "No cross-domain patterns detected. "
                       "Ensure multiple fields have recent predictions.",
        }
    else:
        output = {
            "cross_domain_trends": [t.to_dict() for t in results],
            "fields_analyzed": fields,
            "lookback_days": lookback,
        }

    print(json.dumps(output, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trend Spotter: discover emerging trends from multiple data sources"
    )
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Scan for trends in a field")
    scan_parser.add_argument("field", help="Field or topic to search for (e.g. 'AI agents')")
    scan_parser.add_argument(
        "--window", default="7d", choices=["1d", "7d", "30d"],
        help="Time window to consider (default: 7d)",
    )
    scan_parser.add_argument(
        "--descriptions", action="store_true",
        help="Generate LLM descriptions for trends (costs extra tokens)",
    )

    eval_parser = subparsers.add_parser("evaluate", help="Evaluate matured predictions")
    eval_parser.add_argument(
        "--horizon", default="30d", choices=["30d", "90d"],
        help="Evaluation horizon (default: 30d)",
    )

    acc_parser = subparsers.add_parser("accuracy", help="Show accuracy metrics")
    acc_parser.add_argument(
        "--horizon", default="30d", choices=["30d", "90d"],
        help="Evaluation horizon (default: 30d)",
    )

    subparsers.add_parser("predictions", help="List all stored predictions")

    subparsers.add_parser("schedule", help="Run scheduled evaluation and weight tuning (cron)")

    serve_parser = subparsers.add_parser("serve", help="Start the REST API server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")

    cd_parser = subparsers.add_parser(
        "cross-domain", help="Detect trends emerging across multiple domains"
    )
    cd_parser.add_argument(
        "--field", action="append", required=True, dest="fields",
        help="Field to include (use multiple times, e.g. --field AI --field fintech)",
    )
    cd_parser.add_argument(
        "--lookback", type=int, default=30,
        help="Days to look back for predictions (default: 30)",
    )
    cd_parser.add_argument(
        "--scan", action="store_true",
        help="Run scans for fields missing recent predictions before comparing",
    )

    args = parser.parse_args()

    if args.command == "scan":
        run(args.field, args.window, args.descriptions)
    elif args.command == "evaluate":
        run_evaluate(args.horizon)
    elif args.command == "accuracy":
        run_accuracy(args.horizon)
    elif args.command == "predictions":
        run_predictions()
    elif args.command == "schedule":
        summary = run_schedule()
        print(json.dumps(summary, indent=2))
    elif args.command == "cross-domain":
        run_cross_domain(args.fields, args.lookback, args.scan)
    elif args.command == "serve":
        import uvicorn
        from .api.app import app
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.command is None:
        compat_parser = argparse.ArgumentParser(
            description="Trend Spotter: discover emerging trends"
        )
        compat_parser.add_argument("field", help="Field or topic to search for")
        compat_parser.add_argument(
            "--window", default="7d", choices=["1d", "7d", "30d"],
            help="Time window (default: 7d)",
        )
        try:
            compat_args = compat_parser.parse_args()
            run(compat_args.field, compat_args.window)
        except SystemExit:
            parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
