"""Feedback loop evaluation engine for Trend Spotter (Phase 5).

This module evaluates matured predictions by re-querying the same
signals and comparing current state against the original snapshot.
Each prediction is marked correct, incorrect, or ambiguous based on
classification-specific correctness criteria.

The evaluation engine also computes per-signal predictive correlation
as input for Phase 6 weight tuning.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..ingestion.retrieval import fetch_web, fetch_github, fetch_hn
from ..signal import RawSignal
from ..ingestion.clustering import cluster_signals
from ..scoring.durability import compute_durability_scores
from ..scoring.mentions import compute_mentions_scores

logger = logging.getLogger(__name__)


# Thresholds for correctness evaluation
PRESENCE_THRESHOLD = 3  # minimum signals to consider a trend "present"
GROWTH_THRESHOLD = 0.05  # log-delta above which growth is "sustained"
DECAY_THRESHOLD = -0.1  # log-delta below which a spike has "decayed"


@dataclass
class EvaluationResult:
    """Result of evaluating a single prediction."""
    prediction_id: str
    horizon: str  # "30d" or "90d"
    outcome: str  # "correct", "incorrect", "ambiguous"
    current_signal_count: int
    original_signal_count: int
    growth_delta: float
    signals_with_growth: int
    reasoning: str


def evaluate_prediction(
    prediction: Dict[str, Any],
    horizon: str,
    config: Config,
) -> EvaluationResult:
    """Evaluate a single matured prediction.

    Re-queries the same field using current data, checks whether the
    predicted trend is still present, and applies correctness criteria
    based on the original classification.

    Args:
        prediction: Row from the predictions table (as dict).
        horizon: "30d" or "90d".
        config: Runtime configuration for API access.

    Returns:
        An EvaluationResult with the outcome.
    """
    field = prediction["field"]
    window = prediction["window"]
    classification = prediction["classification"]
    original_accel = prediction["acceleration_score"]
    original_dur = prediction["durability_score"]
    trend_label = prediction["trend_label"]

    # Re-query signals for this field
    current_signals = _requery_signals(field, window, config)

    # Check if the trend is still present in current signals
    presence_count = _count_trend_presence(trend_label, current_signals)

    # Compute growth delta vs original
    original_count = _estimate_original_signal_count(prediction)
    growth_delta = math.log(presence_count + 1) - math.log(original_count + 1)

    # Count how many durability signal categories show growth
    signals_with_growth = _count_signals_with_growth(
        prediction, current_signals, config
    )

    # Apply correctness criteria
    outcome, reasoning = _apply_correctness_criteria(
        classification, horizon, presence_count, growth_delta,
        signals_with_growth, original_accel, original_dur,
    )

    return EvaluationResult(
        prediction_id=prediction["prediction_id"],
        horizon=horizon,
        outcome=outcome,
        current_signal_count=presence_count,
        original_signal_count=original_count,
        growth_delta=round(growth_delta, 4),
        signals_with_growth=signals_with_growth,
        reasoning=reasoning,
    )


def compute_accuracy_metrics(
    evaluated: List[Dict[str, Any]],
    horizon: str,
) -> Dict[str, Any]:
    """Compute accuracy metrics grouped by classification.

    Args:
        evaluated: List of prediction rows that have been evaluated
            (must have evaluation_30d or evaluation_90d populated).
        horizon: "30d" or "90d".

    Returns:
        Dict with overall and per-classification accuracy stats.
    """
    eval_key = f"evaluation_{horizon}"
    by_class: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "incorrect": 0, "ambiguous": 0, "total": 0}
    )
    totals = {"correct": 0, "incorrect": 0, "ambiguous": 0, "total": 0}

    for pred in evaluated:
        outcome = pred.get(eval_key)
        if not outcome:
            continue
        cls = pred["classification"]
        by_class[cls][outcome] += 1
        by_class[cls]["total"] += 1
        totals[outcome] += 1
        totals["total"] += 1

    # Compute accuracy rates
    def _rate(counts: Dict[str, int]) -> Optional[float]:
        if counts["total"] == 0:
            return None
        return round(counts["correct"] / counts["total"] * 100, 1)

    result = {
        "horizon": horizon,
        "overall": {
            **totals,
            "accuracy_pct": _rate(totals),
        },
        "by_classification": {},
    }
    for cls, counts in by_class.items():
        result["by_classification"][cls] = {
            **counts,
            "accuracy_pct": _rate(counts),
        }

    return result


def compute_signal_correlation(
    evaluated: List[Dict[str, Any]],
) -> Dict[str, Dict[str, float]]:
    """Compute per-durability-signal correlation with correct 90d outcomes.

    For each of the 6 durability signals, computes the average score
    for correct vs incorrect predictions. The difference indicates
    predictive strength. This is input for Phase 6 weight tuning.

    Args:
        evaluated: List of prediction rows with evaluation_90d populated.

    Returns:
        Dict mapping signal name to {avg_correct, avg_incorrect, delta,
        sample_correct, sample_incorrect}.
    """
    signal_names = [
        "builder_activity", "adoption_quality", "discourse_depth",
        "cross_platform_presence", "problem_anchoring", "composability",
    ]

    correct_scores: Dict[str, List[int]] = {s: [] for s in signal_names}
    incorrect_scores: Dict[str, List[int]] = {s: [] for s in signal_names}

    for pred in evaluated:
        outcome = pred.get("evaluation_90d")
        if outcome not in ("correct", "incorrect"):
            continue
        target = correct_scores if outcome == "correct" else incorrect_scores
        for sig_name in signal_names:
            val = pred.get(sig_name)
            if val is not None:
                target[sig_name].append(val)

    result = {}
    for sig_name in signal_names:
        c_scores = correct_scores[sig_name]
        i_scores = incorrect_scores[sig_name]
        avg_c = sum(c_scores) / len(c_scores) if c_scores else 0
        avg_i = sum(i_scores) / len(i_scores) if i_scores else 0
        result[sig_name] = {
            "avg_correct": round(avg_c, 1),
            "avg_incorrect": round(avg_i, 1),
            "delta": round(avg_c - avg_i, 1),
            "sample_correct": len(c_scores),
            "sample_incorrect": len(i_scores),
        }

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _requery_signals(field: str, window: str, config: Config) -> List[RawSignal]:
    """Re-query all three sources for the field. Failures return empty."""
    signals: List[RawSignal] = []
    fetchers = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fetch_web, field, window, config.serpapi_key): "web",
            executor.submit(fetch_github, field, window, config.github_token): "github",
            executor.submit(fetch_hn, field, window): "hn",
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                signals.extend(future.result())
            except Exception as exc:
                logger.warning("Re-query failed for %s: %s", source, exc)

    return signals


def _count_trend_presence(trend_label: str, signals: List[RawSignal]) -> int:
    """Count how many current signals match the trend label."""
    # Extract key terms from the label for fuzzy matching
    stopwords = {
        "the", "and", "for", "with", "that", "this", "of", "in", "on",
        "at", "to", "a", "an", "from", "by", "as", "is", "are", "new",
    }
    token_pattern = re.compile(r"[A-Za-z0-9]+")
    label_words = {
        t for t in token_pattern.findall(trend_label.lower())
        if t not in stopwords and len(t) > 2
    }

    if not label_words:
        return 0

    count = 0
    for sig in signals:
        text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
        sig_words = set(token_pattern.findall(text))
        # Match if at least half the label words appear in the signal
        overlap = len(label_words & sig_words)
        if overlap >= max(1, len(label_words) // 2):
            count += 1

    return count


def _estimate_original_signal_count(prediction: Dict[str, Any]) -> int:
    """Return the original signal count stored with the prediction.

    Falls back to 3 (typical cluster size) for predictions created
    before the original_signal_count column was added.
    """
    count = prediction.get("original_signal_count")
    if count is not None and count > 0:
        return count
    return 3


def _count_signals_with_growth(
    prediction: Dict[str, Any],
    current_signals: List[RawSignal],
    config: Config,
) -> int:
    """Count how many durability signal categories show growth.

    Compares original sub-scores against a quick re-evaluation of
    current signals to determine how many signals have grown.
    """
    signal_names = [
        "builder_activity", "adoption_quality", "discourse_depth",
        "cross_platform_presence", "problem_anchoring", "composability",
    ]

    # Quick heuristic: count signals where the original score was
    # above the median (50) as "present", then check if current
    # signals still support them.
    growth_count = 0
    trend_label = prediction["trend_label"]

    # Filter current signals to those matching the trend
    matching = _get_matching_signals(trend_label, current_signals)

    if not matching:
        return 0

    # Check each signal category for evidence of continued activity
    for sig_name in signal_names:
        original_score = prediction.get(sig_name, 0) or 0
        current_evidence = _has_signal_evidence(sig_name, matching)
        if current_evidence and original_score > 0:
            growth_count += 1

    return growth_count


def _get_matching_signals(
    trend_label: str, signals: List[RawSignal]
) -> List[RawSignal]:
    """Filter signals to those matching the trend label."""
    stopwords = {
        "the", "and", "for", "with", "that", "this", "of", "in", "on",
        "at", "to", "a", "an", "from", "by", "as", "is", "are", "new",
    }
    token_pattern = re.compile(r"[A-Za-z0-9]+")
    label_words = {
        t for t in token_pattern.findall(trend_label.lower())
        if t not in stopwords and len(t) > 2
    }

    if not label_words:
        return []

    matching = []
    for sig in signals:
        text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
        sig_words = set(token_pattern.findall(text))
        overlap = len(label_words & sig_words)
        if overlap >= max(1, len(label_words) // 2):
            matching.append(sig)

    return matching


def _has_signal_evidence(sig_name: str, signals: List[RawSignal]) -> bool:
    """Check if current signals provide evidence for a durability signal."""
    if sig_name == "builder_activity":
        # Look for GitHub repos with forks or recent pushes
        return any(
            sig.source == "github" and sig.extras.get("forks_count", 0) >= 3
            for sig in signals
        )
    elif sig_name == "adoption_quality":
        # Look for HN posts with substantial comments
        return any(
            sig.source == "hn" and sig.extras.get("num_comments", 0) >= 5
            for sig in signals
        )
    elif sig_name == "discourse_depth":
        # Look for implementation-related content
        impl_keywords = {"implementation", "architecture", "production", "deploy", "benchmark"}
        for sig in signals:
            text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
            if any(kw in text for kw in impl_keywords):
                return True
        return False
    elif sig_name == "cross_platform_presence":
        # Present on at least 2 platforms
        sources = {sig.source for sig in signals}
        return len(sources) >= 2
    elif sig_name == "problem_anchoring":
        # Production use-case keywords
        anchor_keywords = {"production", "case study", "enterprise", "deployed", "use case"}
        for sig in signals:
            text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
            if any(kw in text for kw in anchor_keywords):
                return True
        return False
    elif sig_name == "composability":
        # Ecosystem keywords or forks
        comp_keywords = {"plugin", "extension", "wrapper", "sdk", "ecosystem", "api"}
        for sig in signals:
            text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
            if any(kw in text for kw in comp_keywords):
                return True
            if sig.source == "github" and sig.extras.get("forks_count", 0) >= 10:
                return True
        return False
    return False


def _apply_correctness_criteria(
    classification: str,
    horizon: str,
    presence_count: int,
    growth_delta: float,
    signals_with_growth: int,
    original_accel: int,
    original_dur: int,
) -> Tuple[str, str]:
    """Apply classification-specific correctness criteria.

    Returns:
        Tuple of (outcome, reasoning) where outcome is one of
        "correct", "incorrect", "ambiguous".
    """
    is_present = presence_count >= PRESENCE_THRESHOLD
    is_growing = growth_delta > GROWTH_THRESHOLD
    has_decayed = growth_delta < DECAY_THRESHOLD
    is_absent = presence_count < 2

    if classification == "Compounding":
        if horizon == "30d":
            # Correct: growth sustained across >= 2 signals
            if is_growing and signals_with_growth >= 2:
                return "correct", f"Growth sustained (delta={growth_delta:.3f}, {signals_with_growth} signals growing)"
            elif is_present and signals_with_growth >= 1:
                return "ambiguous", f"Present but growth marginal (delta={growth_delta:.3f}, {signals_with_growth} signals)"
            else:
                return "incorrect", f"Growth not sustained (delta={growth_delta:.3f}, presence={presence_count})"
        else:  # 90d
            # Correct: growth sustained or accelerated
            if is_growing and signals_with_growth >= 2:
                return "correct", f"Growth sustained at 90d (delta={growth_delta:.3f}, {signals_with_growth} signals growing)"
            elif is_present and not has_decayed:
                return "ambiguous", f"Present but not clearly growing (delta={growth_delta:.3f})"
            else:
                return "incorrect", f"Failed to sustain growth at 90d (delta={growth_delta:.3f}, presence={presence_count})"

    elif classification == "Durable/Slow":
        if horizon == "30d":
            # Correct: still present, acceleration <= original
            if is_present and not is_growing:
                return "correct", f"Present and stable (presence={presence_count}, delta={growth_delta:.3f})"
            elif is_present and is_growing:
                return "ambiguous", f"Present but accelerating (may be upgrading to Compounding)"
            else:
                return "incorrect", f"No longer present (presence={presence_count})"
        else:  # 90d
            # Correct: still present at 90d
            if is_present:
                return "correct", f"Still present at 90d (presence={presence_count})"
            elif presence_count >= 1:
                return "ambiguous", f"Marginally present (presence={presence_count})"
            else:
                return "incorrect", f"Absent at 90d (presence={presence_count})"

    elif classification == "Flash Trend":
        if horizon == "30d":
            # Correct: spike has decayed
            if has_decayed or is_absent:
                return "correct", f"Spike decayed as predicted (delta={growth_delta:.3f}, presence={presence_count})"
            elif is_present and not is_growing:
                return "ambiguous", f"Still present but not growing (delta={growth_delta:.3f})"
            else:
                return "incorrect", f"Still growing — may not be a flash trend (delta={growth_delta:.3f})"
        else:  # 90d
            # Correct: largely absent
            if is_absent:
                return "correct", f"Largely absent at 90d (presence={presence_count})"
            elif not is_growing and presence_count <= PRESENCE_THRESHOLD:
                return "ambiguous", f"Fading but not fully absent (presence={presence_count})"
            else:
                return "incorrect", f"Persisted contrary to Flash Trend prediction (presence={presence_count})"

    elif classification == "Ignore":
        if horizon in ("30d", "90d"):
            # Correct: absent from top signals
            if is_absent:
                return "correct", f"Absent as predicted (presence={presence_count})"
            elif presence_count <= PRESENCE_THRESHOLD:
                return "ambiguous", f"Low presence but not fully absent (presence={presence_count})"
            else:
                return "incorrect", f"Unexpectedly present (presence={presence_count})"

    return "ambiguous", f"Unknown classification '{classification}'"


def check_thresholds(
    metrics: Dict[str, Any],
    horizon: str,
) -> List[str]:
    """Compare accuracy metrics against roadmap targets.

    Targets:
    - 30d overall accuracy >= 60%
    - 90d overall accuracy >= 65%
    - Per-classification ambiguous rate <= 20%
    - Flash Trend false positive rate <= 40%

    Returns:
        List of warning strings for any threshold violation.
    """
    warnings: List[str] = []

    target = 60.0 if horizon == "30d" else 65.0
    overall = metrics.get("overall", {})
    accuracy = overall.get("accuracy_pct")

    if accuracy is not None and accuracy < target:
        warnings.append(
            f"Overall {horizon} accuracy {accuracy}% is below "
            f"target {target}%"
        )

    by_class = metrics.get("by_classification", {})

    # Check ambiguous rates per classification
    for cls, counts in by_class.items():
        total = counts.get("total", 0)
        if total < 5:
            continue
        ambiguous_rate = counts.get("ambiguous", 0) / total * 100
        if ambiguous_rate > 20:
            warnings.append(
                f"{cls} ambiguous rate is {ambiguous_rate:.0f}% "
                f"(>{' '}20% threshold, n={total})"
            )

    # Flash Trend false positive check
    flash = by_class.get("Flash Trend", {})
    flash_total = flash.get("total", 0)
    if flash_total >= 5:
        fp_rate = flash.get("incorrect", 0) / flash_total * 100
        if fp_rate > 40:
            warnings.append(
                f"Flash Trend false positive rate is {fp_rate:.0f}% "
                f"(> 40% — acceleration threshold may be too low)"
            )

    return warnings
