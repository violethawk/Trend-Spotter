"""Core scan pipeline for Trend Spotter.

This module contains the reusable pipeline logic that powers both
the CLI and the API. It orchestrates signal collection, clustering,
scoring, durability analysis, classification, and persistence.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

from .config import Config
from .ingestion.query_router import collect_signals
from .ingestion.clustering import cluster_signals
from .scoring.mentions import compute_mentions_scores
from .scoring.acceleration import compute_acceleration_scores
from .scoring.durability import compute_durability_scores
from .classification import classify_trends
from .ranking import rank_clusters
from .persistence.snapshot import SnapshotStore
from .persistence.prediction_store import PredictionStore
from .signal import RawSignal

logger = logging.getLogger(__name__)


def run_pipeline(field: str, window: str, config: Config) -> Dict[str, Any]:
    """Run the full Trend Spotter pipeline and return structured output.

    Args:
        field: Field or topic to search for.
        window: Time window (1d, 7d, 30d).
        config: Runtime configuration.

    Returns:
        Dict with field, time_window, generated_at, trends, run_data_gaps.
    """
    run_start_monotonic = time.monotonic()
    run_start_iso = datetime.now(timezone.utc).isoformat()

    # --- Phase 1: Collect signals ---
    signals, run_data_gaps = collect_signals(
        field, window, config, start_time=run_start_monotonic
    )

    if not signals and all(src in run_data_gaps for src in ["web", "github", "hn"]):
        return {
            "error": "All data sources failed. Check API keys and network.",
            "run_data_gaps": run_data_gaps,
        }

    hard_timeout = "hard_timeout" in run_data_gaps

    # --- Phase 1: Cluster signals ---
    clusters: List[Dict] = []
    if not hard_timeout:
        clusters = cluster_signals(signals, field, config)

    # --- Phase 1-2: Score (mentions + acceleration) ---
    mentions_scores: Dict[str, tuple] = {}
    acceleration_scores: Dict[str, tuple] = {}
    per_trend_gaps: Dict[str, List[str]] = {}
    snapshot_store = SnapshotStore()
    prediction_store: PredictionStore | None = None

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
        description = _generate_description(
            label, titles, field, config.openai_key
        ) if not hard_timeout else f"{label} is an emerging topic within {field}."

        sources = _get_sources_for_cluster(cluster, signals)

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
                    evidence = _get_sources_for_cluster(cluster, signals)
                    signal_count = len(cluster.get("signal_ids", []))
                    prediction_store.write_prediction(
                        ct, dur_result, accel_score,
                        field, window, run_start_iso, evidence,
                        original_signal_count=signal_count,
                    )
        except Exception:
            pass

    return {
        "field": field,
        "time_window": window,
        "generated_at": run_start_iso,
        "trends": trends_output,
        "run_data_gaps": run_data_gaps,
    }


def _generate_description(
    label: str, signal_titles: List[str], field: str, openai_key: str
) -> str:
    """Generate a human-readable description of a trend using OpenAI."""
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


def _get_sources_for_cluster(
    cluster: Dict, signals: List[RawSignal]
) -> List[Dict]:
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
