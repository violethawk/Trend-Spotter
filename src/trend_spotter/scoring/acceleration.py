"""Acceleration scoring for Trend Spotter.

Computes rate-of-change scores by comparing current signal counts
against snapshot baselines. Raw log-delta values are stored for the
feedback loop; normalised 0-100 scores use a fixed scale so they
are comparable across runs.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from ..signal import RawSignal
from ..persistence.snapshot import SnapshotStore

# Fixed reference range for normalisation.
# A raw log-delta of 0 = no change = score 50.
# A raw log-delta of +RAW_CEILING = score 100 (strong growth).
# A raw log-delta of -RAW_CEILING = score 0 (strong decline).
# log(6) - log(1) ≈ 1.79 (going from 0 to 5 signals), so 2.0 is a
# reasonable ceiling that captures most real acceleration.
RAW_CEILING = 2.0


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
    0-100 using a fixed scale (not relative to other clusters in the
    same run), so they are comparable across runs.

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
    scores: Dict[str, Tuple[int, float]] = {}
    for cluster in clusters:
        label = cluster["label"]
        current_count = len(cluster["signal_ids"])
        prev_count = snapshot_store.get_previous_signal_count(
            field, label, window, captured_before=run_start_time_iso
        )
        if prev_count is None:
            per_trend_gaps.setdefault(label, []).append(f"no_baseline:{label}")
            # No baseline: raw=0, score=50 (unknown, not a false spike)
            scores[label] = (50, 0.0)
            continue

        raw_val = math.log(current_count + 1) - math.log(prev_count + 1)

        # Fixed-scale normalisation: clamp to [-RAW_CEILING, +RAW_CEILING]
        # then map to [0, 100] where 50 = no change
        clamped = max(-RAW_CEILING, min(RAW_CEILING, raw_val))
        norm = int(round(50 + (clamped / RAW_CEILING) * 50))
        norm = max(0, min(100, norm))

        scores[label] = (norm, raw_val)

    return scores
