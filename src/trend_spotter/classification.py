"""Classification and trajectory detection for Trend Spotter (Phase 4).

This module implements the 2x2 classification matrix (durability vs
acceleration) and trajectory detection. Each classified trend receives
a prediction_id for tracking in the prediction store.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .scoring.durability import DurabilityResult
from .persistence.snapshot import SnapshotStore


# Classification thresholds (tunable)
HIGH_THRESHOLD = 65

# Trajectory sensitivity: minimum raw acceleration delta to count as rising/declining
TRAJECTORY_DELTA = 0.1


@dataclass
class ClassifiedTrend:
    """A trend with classification, trajectory, and prediction ID."""
    label: str
    classification: str
    trajectory: str
    prediction_id: str


def classify(durability_score: int, acceleration_score: int) -> str:
    """Assign a 2x2 classification based on score thresholds.

    Returns one of: Compounding, Durable/Slow, Flash Trend, Ignore.
    """
    high_dur = durability_score >= HIGH_THRESHOLD
    high_acc = acceleration_score >= HIGH_THRESHOLD

    if high_dur and high_acc:
        return "Compounding"
    elif high_dur and not high_acc:
        return "Durable/Slow"
    elif not high_dur and high_acc:
        return "Flash Trend"
    else:
        return "Ignore"


def detect_trajectory(
    current_raw_accel: float,
    previous_raw_accel: Optional[float],
) -> str:
    """Determine if a trend is rising, stable, or declining.

    Args:
        current_raw_accel: Raw (pre-normalised) acceleration value for this run.
        previous_raw_accel: Raw acceleration from the previous run, or None.

    Returns:
        One of: rising, stable, declining.
    """
    if previous_raw_accel is None:
        return "stable"

    delta = current_raw_accel - previous_raw_accel
    if delta > TRAJECTORY_DELTA:
        return "rising"
    elif delta < -TRAJECTORY_DELTA:
        return "declining"
    return "stable"


def classify_trends(
    selected_clusters: List[Dict],
    acceleration_scores: Dict[str, Tuple[int, float]],
    durability_results: Dict[str, DurabilityResult],
    snapshot_store: SnapshotStore,
    field: str,
    window: str,
    run_start_iso: str,
    per_trend_gaps: Dict[str, List[str]],
) -> List[ClassifiedTrend]:
    """Classify each selected cluster and assign prediction IDs.

    Args:
        selected_clusters: Ranked clusters to classify.
        acceleration_scores: Mapping label -> (normalised, raw) acceleration.
        durability_results: Mapping label -> DurabilityResult.
        snapshot_store: For reading previous acceleration baselines.
        field: Field of interest.
        window: Time window.
        run_start_iso: ISO timestamp for this run.
        per_trend_gaps: Mutable dict to record trajectory data gaps.

    Returns:
        List of ClassifiedTrend objects.
    """
    classified = []

    for cluster in selected_clusters:
        label = cluster["label"]

        # Get scores
        accel_norm, accel_raw = acceleration_scores.get(label, (0, 0.0))
        dur_result = durability_results.get(label)
        dur_score = dur_result.score if dur_result else 0

        # Classification
        classification = classify(dur_score, accel_norm)

        # Trajectory: compare raw acceleration against previous run
        prev_raw = snapshot_store.get_previous_acceleration(
            field, label, window, captured_before=run_start_iso
        )
        if prev_raw is None:
            per_trend_gaps.setdefault(label, []).append("no_trajectory_baseline")

        trajectory = detect_trajectory(accel_raw, prev_raw)

        classified.append(ClassifiedTrend(
            label=label,
            classification=classification,
            trajectory=trajectory,
            prediction_id=str(uuid.uuid4()),
        ))

    return classified
