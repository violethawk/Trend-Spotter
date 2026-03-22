"""Durability scoring for Trend Spotter (Phase 3).

This module computes a durability score (0-100) for each trend cluster
by evaluating six structural signals that approximate long-term
persistence. A sentiment filter is applied as a multiplicative penalty.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from .config import Config
from .signal import RawSignal

logger = logging.getLogger(__name__)


# Hand-tuned initial weights (Phase 6 will update from outcome data)
DURABILITY_WEIGHTS: Dict[str, float] = {
    "builder_activity": 0.20,
    "adoption_quality": 0.20,
    "discourse_depth": 0.15,
    "cross_platform_presence": 0.15,
    "problem_anchoring": 0.15,
    "composability": 0.15,
}

# Keywords used by individual signal functions
_IMPLEMENTATION_KEYWORDS = {
    "implementation", "architecture", "production", "deploy", "benchmark",
    "performance", "scalability", "migration", "integration", "tutorial",
    "how to", "step by step", "walkthrough", "setup", "configure",
}

_ANCHOR_KEYWORDS = {
    "production", "case study", "enterprise", "customer", "deployed",
    "in production", "real-world", "scale", "company", "use case",
    "revenue", "users", "adoption",
}

_COMPOSABILITY_KEYWORDS = {
    "plugin", "extension", "wrapper", "sdk", "integration", "ecosystem",
    "middleware", "adapter", "api", "library", "package", "module",
}


@dataclass
class DurabilityResult:
    """Result of durability scoring for a single cluster."""
    score: int
    signals: Dict[str, int]
    sentiment_multiplier: float


def compute_durability_scores(
    clusters: List[Dict],
    all_signals: List[RawSignal],
    config: Config,
    per_trend_gaps: Dict[str, List[str]],
) -> Dict[str, DurabilityResult]:
    """Compute durability scores for each cluster.

    Args:
        clusters: List of cluster dicts (with signal_ids, source_breakdown).
        all_signals: All raw signals for this run.
        config: Runtime configuration (needed for sentiment LLM call).
        per_trend_gaps: Mutable dict to append data gaps per cluster label.

    Returns:
        Mapping from cluster label to DurabilityResult.
    """
    sig_map = {sig.id: sig for sig in all_signals}
    results: Dict[str, DurabilityResult] = {}

    for cluster in clusters:
        label = cluster["label"]
        cluster_signals = [sig_map[sid] for sid in cluster["signal_ids"] if sid in sig_map]
        source_breakdown = cluster.get("source_breakdown", {})

        if not cluster_signals:
            results[label] = DurabilityResult(score=0, signals={}, sentiment_multiplier=1.0)
            per_trend_gaps.setdefault(label, []).append("no_signals_for_durability")
            continue

        # Compute each sub-signal
        sub_scores: Dict[str, int] = {
            "builder_activity": _score_builder_activity(cluster_signals),
            "adoption_quality": _score_adoption_quality(cluster_signals),
            "discourse_depth": _score_discourse_depth(cluster_signals),
            "cross_platform_presence": _score_cross_platform(source_breakdown),
            "problem_anchoring": _score_problem_anchoring(cluster_signals),
            "composability": _score_composability(cluster_signals),
        }

        # Sentiment filter
        sentiment_mult = _compute_sentiment_penalty(cluster_signals, config)

        # Weighted average
        weighted_sum = sum(
            sub_scores[name] * DURABILITY_WEIGHTS[name]
            for name in DURABILITY_WEIGHTS
        )
        final_score = int(round(weighted_sum * sentiment_mult))
        final_score = max(0, min(100, final_score))

        results[label] = DurabilityResult(
            score=final_score,
            signals=sub_scores,
            sentiment_multiplier=round(sentiment_mult, 2),
        )

    return results


# ---------------------------------------------------------------------------
# Individual signal scoring functions
# ---------------------------------------------------------------------------

def _score_builder_activity(signals: List[RawSignal]) -> int:
    """Score based on active building: forks, recent pushes, Show HN posts."""
    active_repos = 0
    show_hn_count = 0

    for sig in signals:
        if sig.source == "github":
            forks = sig.extras.get("forks_count", 0)
            pushed_at = sig.extras.get("pushed_at")
            # Count repos with meaningful fork activity and recent pushes
            if forks >= 5:
                active_repos += 1
            elif pushed_at:
                # Even without many forks, a recently pushed repo shows building
                active_repos += 0.5
        elif sig.source == "hn":
            if sig.title and sig.title.lower().startswith("show hn"):
                show_hn_count += 1

    raw = active_repos * 3 + show_hn_count * 2
    # Logistic scaling: approaches 100 as raw grows
    score = int(round(100 * (1 - math.exp(-raw / 5))))
    return max(0, min(100, score))


def _score_adoption_quality(signals: List[RawSignal]) -> int:
    """Score based on practitioner-vs-spectator engagement ratios."""
    hn_ratios = []
    gh_ratios = []

    for sig in signals:
        if sig.source == "hn":
            points = max(sig.value, 1)
            comments = sig.extras.get("num_comments", 0)
            hn_ratios.append(comments / points)
        elif sig.source == "github":
            stars = max(sig.value, 1)
            forks = sig.extras.get("forks_count", 0)
            gh_ratios.append(forks / stars)

    # Average ratios (higher = more practitioner engagement)
    avg_hn = sum(hn_ratios) / len(hn_ratios) if hn_ratios else 0
    avg_gh = sum(gh_ratios) / len(gh_ratios) if gh_ratios else 0

    # Weight and scale: HN comment ratio ~0.5 is high; fork ratio ~0.3 is high
    raw = avg_hn * 50 + avg_gh * 200
    score = int(round(min(100, raw)))

    # If no HN or GitHub data, default to neutral-low
    if not hn_ratios and not gh_ratios:
        return 30

    return max(0, min(100, score))


def _score_discourse_depth(signals: List[RawSignal]) -> int:
    """Score based on comment depth and implementation-language in snippets."""
    comment_counts = []
    keyword_hits = 0

    for sig in signals:
        if sig.source == "hn":
            comment_counts.append(sig.extras.get("num_comments", 0))
        # Scan title and snippet for implementation keywords
        text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
        for kw in _IMPLEMENTATION_KEYWORDS:
            if kw in text:
                keyword_hits += 1
                break  # Count once per signal

    avg_comments = sum(comment_counts) / len(comment_counts) if comment_counts else 0
    raw = min(avg_comments, 50) * 1.5 + keyword_hits * 5
    score = int(round(min(100, raw)))
    return max(0, min(100, score))


def _score_cross_platform(source_breakdown: Dict[str, int]) -> int:
    """Score based on how many distinct sources contribute to a cluster."""
    if not source_breakdown:
        return 0

    source_count = len(source_breakdown)
    counts = list(source_breakdown.values())
    balance = min(counts) / max(counts) if max(counts) > 0 else 0

    if source_count >= 3:
        base = 70
    elif source_count == 2:
        base = 40
    else:
        base = 10

    score = base + int(balance * 30)
    return max(0, min(100, score))


def _score_problem_anchoring(signals: List[RawSignal]) -> int:
    """Score based on presence of production use-case language."""
    anchored_count = 0
    for sig in signals:
        text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
        for kw in _ANCHOR_KEYWORDS:
            if kw in text:
                anchored_count += 1
                break  # Count once per signal

    score = min(100, anchored_count * 20)
    return score


def _score_composability(signals: List[RawSignal]) -> int:
    """Score based on ecosystem formation signals (forks, wrappers, plugins)."""
    total_forks = 0
    composability_hits = 0

    for sig in signals:
        if sig.source == "github":
            total_forks += sig.extras.get("forks_count", 0)

        text = ((sig.title or "") + " " + (sig.snippet or "")).lower()
        for kw in _COMPOSABILITY_KEYWORDS:
            if kw in text:
                composability_hits += 1
                break  # Count once per signal

    raw = math.log(total_forks + 1) * 15 + composability_hits * 10
    score = int(round(min(100, raw)))
    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Sentiment filter
# ---------------------------------------------------------------------------

def _compute_sentiment_penalty(
    signals: List[RawSignal],
    config: Config,
) -> float:
    """Use an LLM to classify cluster sentiment and return a penalty multiplier.

    Returns:
        1.0 for neutral/positive sentiment, 0.6 for negative sentiment.
    """
    # Build text sample from signal titles and snippets
    text_parts = []
    for sig in signals[:10]:  # Cap to avoid token overflow
        text_parts.append(sig.title or "")
        if sig.snippet:
            text_parts.append(sig.snippet)
    combined_text = "\n".join(text_parts)[:1500]  # Truncate

    if not combined_text.strip():
        return 1.0

    system_prompt = (
        "Classify the overall sentiment of these technology discussions. "
        "Return ONLY one word: negative, neutral, or positive. "
        "Negative means: complaints, warnings, avoid this, broken, deprecated. "
        "Neutral means: analytical, implementation-focused, factual. "
        "Positive means: enthusiastic praise without substance."
    )

    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": combined_text},
        ],
        "max_tokens": 10,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {config.openai_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=10,
        )
        if response.status_code != 200:
            logger.warning("Sentiment API returned %d; defaulting to neutral", response.status_code)
            return 1.0
        resp = response.json()
        sentiment = resp["choices"][0]["message"]["content"].strip().lower()
        if "negative" in sentiment:
            return 0.6
        return 1.0
    except Exception as exc:
        logger.warning("Sentiment call failed (%s); defaulting to neutral", exc)
        return 1.0
