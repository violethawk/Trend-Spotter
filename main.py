"""Command line interface for Trend Spotter.

This module wires together configuration loading, query routing,
clustering, scoring, ranking and snapshot persistence. It exposes a
CLI that accepts a field and time window and outputs a JSON summary
of the top trends.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List

from .config import load_config
from .query_router import collect_signals
from .clustering import cluster_signals
from .scoring import compute_mentions_scores, compute_acceleration_scores
from .ranking import rank_clusters
from .snapshot import SnapshotStore
from .signal import RawSignal

import requests


def generate_description(label: str, signal_titles: List[str], field: str, openai_key: str) -> str:
    """Generate a human readable description of a trend using OpenAI.

    If the API call fails, a generic description is returned.

    Args:
        label: Cluster label.
        signal_titles: List of titles from supporting signals.
        field: Field of interest (used to contextualise generic fallback).
        openai_key: OpenAI API key.

    Returns:
        A 1–2 sentence description.
    """
    system_prompt = (
        "You are a technical analyst. Write a 1–2 sentence description of this emerging trend. "
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
        # Strip leading/trailing whitespace
        return content.strip()
    except Exception:
        # Fallback generic description
        return f"{label} is an emerging topic within {field}."


def get_sources_for_cluster(cluster: Dict, signals: List[RawSignal]) -> List[Dict]:
    """Select representative source objects for a cluster.

    Args:
        cluster: Cluster dictionary with ``source_breakdown`` and ``signal_ids``.
        signals: List of all raw signals.

    Returns:
        A list of source objects, at least two if available.
    """
    sig_map = {sig.id: sig for sig in signals}
    sources = []
    # Sorting order to prioritise GitHub and HN then web for representation
    priority = ["github", "hn", "web"]
    for source in priority:
        if source in cluster.get("source_breakdown", {}):
            # Pick the signal with highest value for this source
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
    # Limit to at most three sources; ensure at least two if possible
    return sources[:3]


def run(field: str, window: str) -> None:
    """Run the full Trend Spotter pipeline and print output as JSON."""
    try:
        config = load_config()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    # Start time markers
    run_start_monotonic = time.monotonic()
    run_start_iso = datetime.now(timezone.utc).isoformat()
    # Collect signals across query variants
    signals, run_data_gaps = collect_signals(
        field, window, config, start_time=run_start_monotonic
    )
    # If all sources fail, abort with error
    if not signals and all(src in run_data_gaps for src in ["web", "github", "hn"]):
        err = {
            "error": "All data sources failed. Check API keys and network.",
            "run_data_gaps": run_data_gaps,
        }
        print(json.dumps(err, indent=2))
        return
    # If hard timeout occurred during collection, no more processing
    hard_timeout = "hard_timeout" in run_data_gaps
    # Cluster signals
    clusters = []
    if not hard_timeout:
        clusters = cluster_signals(signals, field, config)
    # Compute scores if clusters exist
    mentions_scores: Dict[str, tuple] = {}
    acceleration_scores: Dict[str, tuple] = {}
    per_trend_gaps: Dict[str, List[str]] = {}
    if clusters:
        mentions_scores = compute_mentions_scores(clusters, signals)
        # Initialize snapshot store
        snapshot_store = SnapshotStore()
        acceleration_scores = compute_acceleration_scores(
            clusters,
            field,
            window,
            snapshot_store,
            run_start_iso,
            per_trend_gaps=per_trend_gaps,
        )
        # Rank clusters
        selected = rank_clusters(clusters, mentions_scores, acceleration_scores)
    else:
        selected = []
    # Build final trends list
    trends_output = []
    for cluster in selected:
        label = cluster["label"]
        mention_score = mentions_scores.get(label, (0, 0))[0]
        accel_score = acceleration_scores.get(label, (0, 0))[0]
        data_gaps = per_trend_gaps.get(label, [])
        # Gather titles of supporting signals
        titles = []
        for sid in cluster["signal_ids"]:
            for sig in signals:
                if sig.id == sid:
                    titles.append(sig.title)
                    break
        description = generate_description(
            label, titles, field, config.openai_key
        ) if not hard_timeout else f"{label} is an emerging topic within {field}."
        trend_obj = {
            "name": label,
            "description": description,
            "mentions_score": mention_score,
            "acceleration_score": accel_score,
            "data_gaps": data_gaps,
            "sources": get_sources_for_cluster(cluster, signals),
        }
        trends_output.append(trend_obj)
    # If no trends selected but we have clusters but they failed to meet criteria, include message
    if not trends_output and clusters:
        run_data_gaps.append("no_valid_trends")
    # Persist snapshots only if at least one cluster exists
    if clusters:
        snapshot_store = SnapshotStore()
        # Persist aggregated metrics and cluster counts
        try:
            snapshot_store.write_snapshots(field, window, signals, clusters)
        except Exception:
            # Silently ignore snapshot persistence errors
            pass
    # Compose output
    output = {
        "field": field,
        "time_window": window,
        "generated_at": run_start_iso,
        "trends": trends_output,
        "run_data_gaps": run_data_gaps,
    }
    print(json.dumps(output, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Trend Spotter: discover emerging trends from multiple data sources")
    parser.add_argument("field", help="Field or topic to search for (e.g. 'AI agents')")
    parser.add_argument(
        "--window",
        default="7d",
        choices=["1d", "7d", "30d"],
        help="Time window to consider (default: 7d)",
    )
    args = parser.parse_args()
    run(args.field, args.window)


if __name__ == "__main__":
    main()