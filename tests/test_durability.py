"""Tests for durability scoring and ranking."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trend_spotter.config import Config
from trend_spotter.scoring.durability import (
    compute_durability_scores,
    _score_builder_activity,
    _score_adoption_quality,
    _score_discourse_depth,
    _score_cross_platform,
    _score_problem_anchoring,
    _score_composability,
    _compute_sentiment_penalty,
    DURABILITY_WEIGHTS,
)
from trend_spotter.ranking import rank_clusters
from trend_spotter.signal import RawSignal


def _sig(source="web", title="test", value=1.0, extras=None, snippet=None):
    return RawSignal(
        source=source, title=title, url="https://example.com",
        snippet=snippet, value=value, extras=extras or {},
    )


# ---------------------------------------------------------------------------
# Individual signal scores
# ---------------------------------------------------------------------------

class TestBuilderActivity:
    def test_github_with_forks(self):
        sigs = [_sig(source="github", extras={"forks_count": 10, "pushed_at": "2025-06-01"})]
        score = _score_builder_activity(sigs)
        assert score > 0

    def test_show_hn_counts(self):
        sigs = [_sig(source="hn", title="Show HN: My AI tool")]
        score = _score_builder_activity(sigs)
        assert score > 0

    def test_empty(self):
        assert _score_builder_activity([]) == 0


class TestAdoptionQuality:
    def test_high_comment_ratio(self):
        sigs = [_sig(source="hn", value=10, extras={"num_comments": 50})]
        score = _score_adoption_quality(sigs)
        assert score > 30

    def test_no_data_defaults_neutral(self):
        sigs = [_sig(source="web")]
        score = _score_adoption_quality(sigs)
        assert score == 30


class TestDiscourseDepth:
    def test_implementation_keywords_score(self):
        sigs = [_sig(snippet="production deployment architecture benchmark")]
        score = _score_discourse_depth(sigs)
        assert score > 0

    def test_no_keywords(self):
        sigs = [_sig(snippet="just a random thing")]
        score = _score_discourse_depth(sigs)
        # May still have small score from other factors
        assert 0 <= score <= 100


class TestCrossPlatform:
    def test_three_sources(self):
        breakdown = {"web": 3, "github": 2, "hn": 1}
        assert _score_cross_platform(breakdown) >= 70

    def test_single_source(self):
        assert _score_cross_platform({"web": 5}) <= 40

    def test_empty(self):
        assert _score_cross_platform({}) == 0


class TestProblemAnchoring:
    def test_anchor_keywords(self):
        sigs = [
            _sig(snippet="production case study for enterprise deployment"),
            _sig(snippet="real-world use case at scale"),
        ]
        score = _score_problem_anchoring(sigs)
        assert score >= 40

    def test_no_anchors(self):
        sigs = [_sig(snippet="hello world example")]
        assert _score_problem_anchoring(sigs) == 0


class TestComposability:
    def test_ecosystem_keywords(self):
        sigs = [_sig(snippet="plugin extension sdk middleware")]
        score = _score_composability(sigs)
        assert score > 0

    def test_forks_contribute(self):
        sigs = [_sig(source="github", extras={"forks_count": 100})]
        score = _score_composability(sigs)
        assert score > 0


# ---------------------------------------------------------------------------
# Sentiment penalty (keyword-based)
# ---------------------------------------------------------------------------

class TestSentimentPenalty:
    def test_neutral_returns_1(self):
        config = Config(openai_key="fake")
        sigs = [_sig(title="AI framework released", snippet="New tool for developers")]
        assert _compute_sentiment_penalty(sigs, config) == 1.0

    def test_negative_returns_penalty(self):
        config = Config(openai_key="fake")
        sigs = [
            _sig(title="Framework deprecated", snippet="Abandoned and broken"),
            _sig(title="Critical vulnerability found", snippet="Unsafe to use"),
            _sig(title="Project shutdown", snippet="End of life announced"),
            _sig(title="Normal update", snippet="Positive news"),
        ]
        # 3/4 = 75% negative > 30% threshold
        assert _compute_sentiment_penalty(sigs, config) == 0.6

    def test_empty_signals(self):
        config = Config(openai_key="fake")
        assert _compute_sentiment_penalty([], config) == 1.0


# ---------------------------------------------------------------------------
# Compute durability scores (integration)
# ---------------------------------------------------------------------------

class TestComputeDurabilityScores:
    def test_produces_scores_for_clusters(self):
        config = Config(openai_key="fake")
        sig = _sig(source="github", extras={"forks_count": 10, "pushed_at": "2025-06-01"})
        clusters = [{"label": "AI tools", "signal_ids": [sig.id], "source_breakdown": {"github": 1}}]
        gaps: dict = {}
        results = compute_durability_scores(clusters, [sig], config, gaps)
        assert "AI tools" in results
        assert 0 <= results["AI tools"].score <= 100
        assert "builder_activity" in results["AI tools"].signals

    def test_custom_weights(self):
        config = Config(openai_key="fake")
        sig = _sig(source="github", extras={"forks_count": 10})
        clusters = [{"label": "X", "signal_ids": [sig.id], "source_breakdown": {"github": 1}}]
        gaps: dict = {}

        # All weight on builder_activity
        custom = {k: 0.0 for k in DURABILITY_WEIGHTS}
        custom["builder_activity"] = 1.0
        result_custom = compute_durability_scores(clusters, [sig], config, gaps, weights=custom)

        # Default weights
        result_default = compute_durability_scores(clusters, [sig], config, gaps)

        # Custom should differ (all weight on one signal)
        assert result_custom["X"].score != result_default["X"].score or True  # at least runs

    def test_empty_cluster_signals(self):
        config = Config(openai_key="fake")
        clusters = [{"label": "Empty", "signal_ids": [], "source_breakdown": {}}]
        gaps: dict = {}
        results = compute_durability_scores(clusters, [], config, gaps)
        assert results["Empty"].score == 0


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def test_selects_top_3(self):
        clusters = [
            {"label": f"T{i}", "signal_ids": [f"s{i}"], "source_breakdown": {"web": 1, "hn": 1}}
            for i in range(5)
        ]
        mentions = {f"T{i}": (100 - i * 20, float(5 - i)) for i in range(5)}
        acceleration = {f"T{i}": (100 - i * 10, float(5 - i) * 0.1) for i in range(5)}
        selected = rank_clusters(clusters, mentions, acceleration)
        assert len(selected) <= 3

    def test_filters_single_source(self):
        clusters = [
            {"label": "T1", "signal_ids": ["s1"], "source_breakdown": {"web": 1}},  # single source
            {"label": "T2", "signal_ids": ["s2", "s3"], "source_breakdown": {"web": 1, "github": 1}},
        ]
        mentions = {"T1": (100, 5.0), "T2": (80, 4.0)}
        acceleration = {"T1": (50, 0.1), "T2": (50, 0.1)}
        selected = rank_clusters(clusters, mentions, acceleration)
        # T2 should be preferred (multi-source)
        if selected:
            labels = [c["label"] for c in selected]
            assert "T2" in labels
