"""Acceleration scoring for Trend Spotter.

Computes rate-of-change scores by comparing current signal counts
against snapshot baselines, normalised to 0-100.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from ..signal import RawSignal
from ..persistence.snapshot import SnapshotStore


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
    0-100 across all clusters.

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
