"""Mentions scoring for Trend Spotter.

Computes weighted mention counts per cluster, normalised to 0-100.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..signal import RawSignal


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
    # Normalise to 0-100
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
