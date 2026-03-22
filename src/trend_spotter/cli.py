"""Command line interface for Trend Spotter.

This module wires together configuration loading, query routing,
clustering, scoring, durability analysis, classification, ranking,
prediction storage, evaluation and snapshot persistence. It exposes
a CLI with subcommands for trend discovery and prediction evaluation.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List

from .config import load_config
from .ingestion.query_router import collect_signals
from .ingestion.clustering import cluster_signals
from .scoring.mentions import compute_mentions_scores
from .scoring.acceleration import compute_acceleration_scores
from .scoring.durability import compute_durability_scores
from .classification import classify_trends
from .ranking import rank_clusters
from .persistence.snapshot import SnapshotStore
from .persistence.prediction_store import PredictionStore
from .evaluation.evaluator import evaluate_prediction, compute_accuracy_metrics, compute_signal_correlation, check_thresholds
from .evaluation.scheduler import run_schedule
from .signal import RawSignal

import requests

logger = logging.getLogger(__name__)


def generate_description(label: str, signal_titles: List[str], field: str, openai_key: str) -> str:
    """Generate a human readable description of a trend using OpenAI."""
    system_prompt = (
        "You are a technical analyst. Write a 1-2 sentence description of this emerging trend. "
        "Be specific. Do not use marketing language. Do not start with \"This trend\"."
    )
    user_content = f"Trend name: {label}\nSupporting signals:\n" + "\n".join(signal_titles)
    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 150,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=20,
        )
        if response.status_code != 200:
            raise RuntimeError(f"status {response.status_code}: {response.text}")
        resp = response.json()
        content = resp["choices"][0]["message"]["content"]
        return content.strip()
    except Exception:
        return f"{label} is an emerging topic within {field}."


def get_sources_for_cluster(cluster: Dict, signals: List[RawSignal]) -> List[Dict]:
    """Select representative source objects for a cluster."""
    sig_map = {sig.id: sig for sig in signals}
    sources = []
    priority = ["github", "hn", "web"]
    for source in priority:
        if source in cluster.get("source_breakdown", {}):
            selected = None
            max_value = -1
            for sid in cluster["signal_ids"]:
                sig = sig_map.get(sid)
                if sig and sig.source == source and sig.value > max_value:
                    selected = sig
                    max_value = sig.value
            if selected:
                if source == "github":
                    signal_label = "star_velocity"
                elif source == "hn":
                    signal_label = "point_velocity"
                else:
                    signal_label = "article_mention"
                sources.append({
                    "url": selected.url,
                    "source": source,
                    "signal": signal_label,
                })
    return sources[:3]


def run(field: str, window: str) -> None:
    """Run the full Trend Spotter pipeline and print output as JSON."""
    try:
        config = load_config()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    run_start_monotonic = time.monotonic()
    run_start_iso = datetime.now(timezone.utc).isoformat()

    # --- Phase 1: Collect signals ---
    signals, run_data_gaps = collect_signals(
        field, window, config, start_time=run_start_monotonic
    )

    if not signals and all(src in run_data_gaps for src in ["web", "github", "hn"]):
        err = {
            "error": "All data sources failed. Check API keys and network.",
            "run_data_gaps": run_data_gaps,
        }
        print(json.dumps(err, indent=2))
        return

    hard_timeout = "hard_timeout" in run_data_gaps

    # --- Phase 1: Cluster signals ---
    clusters = []
    if not hard_timeout:
        clusters = cluster_signals(signals, field, config)

    # --- Phase 1-2: Score (mentions + acceleration) ---
    mentions_scores: Dict[str, tuple] = {}
    acceleration_scores: Dict[str, tuple] = {}
    per_trend_gaps: Dict[str, List[str]] = {}
    snapshot_store = SnapshotStore()

    if clusters:
        mentions_scores = compute_mentions_scores(clusters, signals)
        acceleration_scores = compute_acceleration_scores(
            clusters, field, window, snapshot_store, run_start_iso,
            per_trend_gaps=per_trend_gaps,
        )

        # --- Phase 3: Durability scoring (with Phase 6 tuned weights) ---
        prediction_store = PredictionStore()
        tuned_weights = prediction_store.get_current_weights()
        try:
            durability_results = compute_durability_scores(
                clusters, signals, config, per_trend_gaps,
                weights=tuned_weights,
            )
        except Exception as exc:
            logger.warning("Durability scoring failed: %s", exc)
            durability_results = {}
            run_data_gaps.append("durability_scoring_failed")

        # --- Rank clusters ---
        selected = rank_clusters(clusters, mentions_scores, acceleration_scores)

        # --- Phase 4: Classification ---
        try:
            classified_trends = classify_trends(
                selected, acceleration_scores, durability_results,
                snapshot_store, field, window, run_start_iso, per_trend_gaps,
            )
        except Exception as exc:
            logger.warning("Classification failed: %s", exc)
            classified_trends = []
            run_data_gaps.append("classification_failed")

        classified_map = {ct.label: ct for ct in classified_trends}
    else:
        selected = []
        durability_results = {}
        classified_map = {}
        prediction_store = None

    # --- Build output ---
    trends_output = []
    for cluster in selected:
        label = cluster["label"]
        mention_score = mentions_scores.get(label, (0, 0))[0]
        accel_score = acceleration_scores.get(label, (0, 0))[0]
        data_gaps = per_trend_gaps.get(label, [])

        dur_result = durability_results.get(label)
        dur_score = dur_result.score if dur_result else 0
        dur_signals = dur_result.signals if dur_result else {}
        sentiment_penalty = dur_result.sentiment_multiplier if dur_result else 1.0

        ct = classified_map.get(label)
        classification = ct.classification if ct else "Ignore"
        trajectory = ct.trajectory if ct else "stable"
        prediction_id = ct.prediction_id if ct else None

        titles = []
        for sid in cluster["signal_ids"]:
            for sig in signals:
                if sig.id == sid:
                    titles.append(sig.title)
                    break
        description = generate_description(
            label, titles, field, config.openai_key
        ) if not hard_timeout else f"{label} is an emerging topic within {field}."

        sources = get_sources_for_cluster(cluster, signals)

        trend_obj = {
            "name": label,
            "description": description,
            "scores": {
                "mentions": mention_score,
                "acceleration": accel_score,
                "durability": dur_score,
            },
            "durability_signals": dur_signals,
            "sentiment_penalty": sentiment_penalty,
            "classification": classification,
            "trajectory": trajectory,
            "prediction_id": prediction_id,
            "data_gaps": data_gaps,
            "sources": sources,
        }
        trends_output.append(trend_obj)

    if not trends_output and clusters:
        run_data_gaps.append("no_valid_trends")

    # --- Persist snapshots and predictions ---
    if clusters:
        try:
            snapshot_store.write_snapshots(field, window, signals, clusters)
            trend_score_data = {}
            for cluster in clusters:
                label = cluster["label"]
                _, accel_raw = acceleration_scores.get(label, (0, 0.0))
                dur_result = durability_results.get(label)
                dur_score = dur_result.score if dur_result else 0
                trend_score_data[label] = (accel_raw, dur_score)
            snapshot_store.write_trend_scores(field, window, trend_score_data)
        except Exception:
            pass

    if classified_map:
        try:
            if not prediction_store:
                prediction_store = PredictionStore()
            for cluster in selected:
                label = cluster["label"]
                ct = classified_map.get(label)
                if ct:
                    dur_result = durability_results.get(label)
                    accel_score = acceleration_scores.get(label, (0, 0))[0]
                    evidence = get_sources_for_cluster(cluster, signals)
                    signal_count = len(cluster.get("signal_ids", []))
                    prediction_store.write_prediction(
                        ct, dur_result, accel_score,
                        field, window, run_start_iso, evidence,
                        original_signal_count=signal_count,
                    )
        except Exception:
            pass

    # --- Output ---
    output = {
        "field": field,
        "time_window": window,
        "generated_at": run_start_iso,
        "trends": trends_output,
        "run_data_gaps": run_data_gaps,
    }
    print(json.dumps(output, indent=2))


def run_evaluate(horizon: str) -> None:
    """Evaluate matured predictions and print results as JSON.

    Finds all predictions that have matured at the given horizon,
    re-queries current signals, applies correctness criteria, and
    writes evaluation results back to the prediction store.
    """
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
            # Write result back to store
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

    # Add signal correlation for 90d evaluations
    if horizon == "90d" and len(evaluated) >= 10:
        metrics["signal_correlation"] = compute_signal_correlation(evaluated)

    # Threshold warnings
    warnings = check_thresholds(metrics, horizon)
    if warnings:
        metrics["warnings"] = warnings

    # Add summary of pending evaluations
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

    # Summarize each prediction
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trend Spotter: discover emerging trends from multiple data sources"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Default scan command (also works without subcommand for backwards compat)
    scan_parser = subparsers.add_parser("scan", help="Scan for trends in a field")
    scan_parser.add_argument("field", help="Field or topic to search for (e.g. 'AI agents')")
    scan_parser.add_argument(
        "--window", default="7d", choices=["1d", "7d", "30d"],
        help="Time window to consider (default: 7d)",
    )

    # Evaluate subcommand
    eval_parser = subparsers.add_parser(
        "evaluate", help="Evaluate matured predictions"
    )
    eval_parser.add_argument(
        "--horizon", default="30d", choices=["30d", "90d"],
        help="Evaluation horizon (default: 30d)",
    )

    # Accuracy subcommand
    acc_parser = subparsers.add_parser(
        "accuracy", help="Show accuracy metrics for evaluated predictions"
    )
    acc_parser.add_argument(
        "--horizon", default="30d", choices=["30d", "90d"],
        help="Evaluation horizon (default: 30d)",
    )

    # Predictions subcommand
    subparsers.add_parser(
        "predictions", help="List all stored predictions and their evaluation status"
    )

    # Schedule subcommand (automated evaluation + tuning)
    subparsers.add_parser(
        "schedule",
        help="Run scheduled evaluation and weight tuning (designed for cron)",
    )

    args = parser.parse_args()

    if args.command == "scan":
        run(args.field, args.window)
    elif args.command == "evaluate":
        run_evaluate(args.horizon)
    elif args.command == "accuracy":
        run_accuracy(args.horizon)
    elif args.command == "predictions":
        run_predictions()
    elif args.command == "schedule":
        summary = run_schedule()
        print(json.dumps(summary, indent=2))
    elif args.command is None:
        # Backwards compatibility: if no subcommand, treat positional args as scan
        # Re-parse with the old-style parser
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
