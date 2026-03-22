"""Ranking and selection of trends.

This module provides utilities to combine mentions and acceleration
scores, enforce diversity and ensure that each returned trend is
supported by at least two sources. The goal is to select up to three
coherent, non-overlapping trends.
"""

from __future__ import annotations

import re
from typing import Dict, List

from .scoring.mentions import MENTION_WEIGHTS


def rank_clusters(
    clusters: List[Dict],
    mentions: Dict[str, tuple],
    acceleration: Dict[str, tuple],
    *,
    max_trends: int = 3,
) -> List[Dict]:
    """Rank clusters and enforce diversity.

    Args:
        clusters: List of cluster dicts with ``label`` and ``source_breakdown``.
        mentions: Mapping from cluster label to (mentions_score, raw_score).
        acceleration: Mapping from cluster label to (acceleration_score, raw).
        max_trends: Maximum number of trends to return.

    Returns:
        A list of selected cluster dicts in ranked order. May contain
        fewer than ``max_trends`` entries if not enough clusters meet
        the diversity and source constraints.
    """
    # Build mapping label -> cluster
    cluster_map = {c["label"]: c for c in clusters}
    # Compute combined score
    combined: List[tuple] = []  # (label, combined_score)
    for label, cluster in cluster_map.items():
        m = mentions.get(label)
        a = acceleration.get(label)
        if not m or not a:
            continue
        combined_score = m[0] + a[0]
        combined.append((label, combined_score))
    # Sort by combined_score descending
    combined.sort(key=lambda x: x[1], reverse=True)
    # Diversity selection
    selected: List[Dict] = []
    selected_labels: List[str] = []
    stopwords = {
        "the", "and", "for", "with", "that", "this", "into", "about", "using", "use",
        "of", "in", "on", "at", "to", "a", "an", "from", "by", "as", "is", "are",
        "was", "were", "be", "has", "have", "had", "new", "old", "based", "into",
        "via", "it's", "its", "it's", "or", "if", "but", "not", "than", "which", "when",
        "how", "what", "why", "who", "where", "their", "there", "our", "your", "you", "we",
    }
    token_pattern = re.compile(r"[A-Za-z0-9]+")
    def content_words(label: str) -> set:
        tokens = token_pattern.findall(label.lower())
        return {t for t in tokens if t not in stopwords}
    for label, score in combined:
        cluster = cluster_map[label]
        # Require at least two unique sources
        if len(cluster.get("source_breakdown", {})) < 2:
            continue
        # Diversity check: compare with already selected cluster labels
        overlap_found = False
        cw = content_words(label)
        for slabel in selected_labels:
            sw = content_words(slabel)
            # Overlap if three or more words shared
            if len(cw & sw) >= 3:
                overlap_found = True
                break
        if overlap_found:
            continue
        selected.append(cluster)
        selected_labels.append(label)
        if len(selected) >= max_trends:
            break
    return selected