"""Adaptive weight tuning for Trend Spotter (Phase 6).

This module computes updated durability signal weights based on
outcome data from evaluated predictions. Signals that correlate
with correct 90d predictions are upweighted; uncorrelated signals
are downweighted. All weights are clamped to [0.5x, 2x] of the
hand-tuned baseline and re-normalised to sum to 1.0.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..scoring.durability import DURABILITY_WEIGHTS

logger = logging.getLogger(__name__)

# Tuning constraints (from roadmap)
MIN_SAMPLE_SIZE = 50
WEIGHT_FLOOR_MULT = 0.5
WEIGHT_CEILING_MULT = 2.0


def compute_updated_weights(
    signal_correlation: Dict[str, Dict[str, float]],
    current_weights: Dict[str, float],
    min_sample_size: int = MIN_SAMPLE_SIZE,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Compute new weights from signal correlation data.

    Args:
        signal_correlation: Output of evaluation.compute_signal_correlation().
            Maps signal name -> {avg_correct, avg_incorrect, delta,
            sample_correct, sample_incorrect}.
        current_weights: The weights currently in use (may be hand-tuned
            baseline or a previous tuned version).
        min_sample_size: Minimum total correct + incorrect samples
            required before tuning is allowed.

    Returns:
        Tuple of (new_weights, changelog). If sample size is insufficient,
        returns current_weights unchanged with a changelog explaining why.
    """
    changelog: Dict[str, Any] = {
        "applied": False,
        "reason": None,
        "changes": {},
    }

    # Check minimum sample size across all signals
    total_samples = 0
    for sig_name, stats in signal_correlation.items():
        total_samples += stats.get("sample_correct", 0)
        total_samples += stats.get("sample_incorrect", 0)
    avg_samples = total_samples / max(len(signal_correlation), 1)

    if avg_samples < min_sample_size:
        changelog["reason"] = (
            f"Insufficient samples: avg {avg_samples:.0f} per signal, "
            f"need {min_sample_size}"
        )
        return dict(current_weights), changelog

    # Compute scaling factors from correlation deltas
    raw_weights: Dict[str, float] = {}
    for sig_name in DURABILITY_WEIGHTS:
        stats = signal_correlation.get(sig_name)
        if stats is None:
            raw_weights[sig_name] = current_weights.get(
                sig_name, DURABILITY_WEIGHTS[sig_name]
            )
            continue

        delta = stats.get("delta", 0)
        base_weight = current_weights.get(
            sig_name, DURABILITY_WEIGHTS[sig_name]
        )

        # Scale: delta is on a 0-100 scale (score differences).
        # A delta of +10 means correct predictions averaged 10 points
        # higher on this signal. Convert to a modest multiplier.
        scale_factor = 1.0 + delta / 100.0
        new_weight = base_weight * scale_factor

        # Clamp to [0.5x, 2x] of the hand-tuned baseline
        baseline = DURABILITY_WEIGHTS[sig_name]
        floor = baseline * WEIGHT_FLOOR_MULT
        ceiling = baseline * WEIGHT_CEILING_MULT
        new_weight = max(floor, min(ceiling, new_weight))

        raw_weights[sig_name] = new_weight

    # Re-normalise to sum to 1.0
    total = sum(raw_weights.values())
    if total <= 0:
        changelog["reason"] = "All weights zeroed out; keeping current weights"
        return dict(current_weights), changelog

    new_weights = {
        name: round(w / total, 4) for name, w in raw_weights.items()
    }

    # Verify no weight exceeds its ceiling post-normalisation
    # (normalisation can push weights up if others were clamped down)
    for sig_name in new_weights:
        baseline = DURABILITY_WEIGHTS[sig_name]
        ceiling = baseline * WEIGHT_CEILING_MULT
        if new_weights[sig_name] > ceiling:
            new_weights[sig_name] = round(ceiling, 4)

    # Re-normalise once more after ceiling enforcement
    total = sum(new_weights.values())
    new_weights = {
        name: round(w / total, 4) for name, w in new_weights.items()
    }

    # Build changelog
    for sig_name in DURABILITY_WEIGHTS:
        old = current_weights.get(sig_name, DURABILITY_WEIGHTS[sig_name])
        new = new_weights[sig_name]
        if abs(old - new) > 0.0001:
            changelog["changes"][sig_name] = {
                "old": round(old, 4),
                "new": round(new, 4),
                "delta": round(new - old, 4),
                "correlation_delta": signal_correlation.get(
                    sig_name, {}
                ).get("delta", 0),
            }

    if changelog["changes"]:
        changelog["applied"] = True
        changelog["reason"] = (
            f"Updated {len(changelog['changes'])} weights "
            f"based on {int(avg_samples)} avg samples per signal"
        )
    else:
        changelog["reason"] = "No weight changes needed (all deltas negligible)"

    return new_weights, changelog
