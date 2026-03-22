"""Scoring utilities for Trend Spotter.

This module provides functions to compute mentions and acceleration
scores for clusters. Scores are normalised to the 0–100 range.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from .signal import RawSignal
from .snapshot import SnapshotStore


# Weights for mentions scoring per source
MENTION_WEIGHTS = {
    "github": 1.5,
    "hn": 1.3,
    "web": 1.0,
}


def compute_mentions_scores(
    clusters: List[Dict], signals: List[RawSignal]
) -> Dict[str, Tuple[int, float]]:
    """Compute raw and normalised mentions scores for each cluster.

    Args:
        clusters: List of cluster dicts with ``signal_ids``.
        signals: All raw signals for this run.

    Returns:
        Mapping from cluster label to a tuple of (normalised_score, raw_score).
        Normalised_score is an integer between 0 and 100. raw_score is the
        weighted sum before normalisation.
    """
    # Map signal id to RawSignal for quick lookup
    sig_map = {sig.id: sig for sig in signals}
    raw_scores: Dict[str, float] = {}
    for cluster in clusters:
        label = cluster["label"]
        ids = cluster["signal_ids"]
        score = 0.0
        for sid in ids:
            sig = sig_map.get(sid)
            if not sig:
                continue
            weight = MENTION_WEIGHTS.get(sig.source, 1.0)
            score += weight
        raw_scores[label] = score
    # Normalise to 0–100
    if not raw_scores:
        return {}
    max_score = max(raw_scores.values())
    # Avoid division by zero
    if max_score <= 0:
        normalised = {label: (0, score) for label, score in raw_scores.items()}
    else:
        normalised = {
            label: (int(round((score / max_score) * 100)), score)
            for label, score in raw_scores.items()
        }
    return normalised


def compute_acceleration_scores(
    clusters: List[Dict],
    field: str,
    window: str,
    snapshot_store: SnapshotStore,
    run_start_time_iso: str,
    *,
    per_trend_gaps: Dict[str, List[str]],
) -> Dict[str, Tuple[int, float]]:
    """Compute acceleration scores for each cluster.

    This function looks up the previous signal count for each cluster
    label and computes the log difference. Scores are normalised to
    0–100 across all clusters.

    Args:
        clusters: List of cluster dicts.
        field: Field of interest.
        window: Time window token.
        snapshot_store: Store used to read previous snapshots.
        run_start_time_iso: ISO timestamp marking the beginning of the run.
        per_trend_gaps: Dict mapping cluster labels to list of gaps; this
            function will append "no_baseline:{label}" when no prior
            snapshot exists.

    Returns:
        Mapping from cluster label to (normalised_score, raw_acceleration).
    """
    raw_acc: Dict[str, float] = {}
    for cluster in clusters:
        label = cluster["label"]
        current_count = len(cluster["signal_ids"])
        prev_count = snapshot_store.get_previous_signal_count(
            field, label, window, captured_before=run_start_time_iso
        )
        if prev_count is None:
            # No baseline; acceleration score will be 0 and flagged later
            per_trend_gaps.setdefault(label, []).append(f"no_baseline:{label}")
            prev_count = 0
        # Compute log difference
        raw_val = math.log(current_count + 1) - math.log(prev_count + 1)
        raw_acc[label] = raw_val
    if not raw_acc:
        return {}
    values = list(raw_acc.values())
    min_val = min(values)
    max_val = max(values)
    scores: Dict[str, Tuple[int, float]] = {}
    if max_val == min_val:
        for label, val in raw_acc.items():
            scores[label] = (50, val)
    else:
        for label, val in raw_acc.items():
            norm = int(round(((val - min_val) / (max_val - min_val)) * 100))
            scores[label] = (norm, val)
    return scores