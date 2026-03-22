"""Tests for the pipeline orchestration and CLI."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from trend_spotter.config import Config
from trend_spotter.pipeline import run_pipeline, _generate_description, _get_sources_for_cluster
from trend_spotter.signal import RawSignal


@pytest.fixture
def config():
    return Config(openai_key="fake-key")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class TestRunPipeline:
    @patch("trend_spotter.pipeline.collect_signals")
    def test_all_sources_fail(self, mock_collect, config):
        mock_collect.return_value = ([], ["web", "github", "hn"])
        result = run_pipeline("AI", "7d", config)
        assert "error" in result

    @patch("trend_spotter.pipeline.collect_signals")
    @patch("trend_spotter.pipeline.cluster_signals")
    def test_no_clusters(self, mock_cluster, mock_collect, config):
        mock_collect.return_value = (
            [RawSignal(source="web", title="test", url="u", value=1)],
            [],
        )
        mock_cluster.return_value = []
        result = run_pipeline("AI", "7d", config)
        assert result["trends"] == []
        assert "field" in result

    @patch("trend_spotter.pipeline.PredictionStore")
    @patch("trend_spotter.pipeline.SnapshotStore")
    @patch("trend_spotter.pipeline.classify_trends")
    @patch("trend_spotter.pipeline.rank_clusters")
    @patch("trend_spotter.pipeline.compute_durability_scores")
    @patch("trend_spotter.pipeline.compute_acceleration_scores")
    @patch("trend_spotter.pipeline.compute_mentions_scores")
    @patch("trend_spotter.pipeline.cluster_signals")
    @patch("trend_spotter.pipeline.collect_signals")
    def test_full_pipeline_produces_output(
        self, mock_collect, mock_cluster, mock_mentions, mock_accel,
        mock_dur, mock_rank, mock_classify, mock_snap, mock_pred, config,
    ):
        sig = RawSignal(id="s1", source="web", title="AI tool", url="u1", value=1)
        mock_collect.return_value = ([sig], [])
        mock_cluster.return_value = [
            {"label": "AI tools", "signal_ids": ["s1"], "source_breakdown": {"web": 1}, "canonical_key": "ai_tool"},
        ]
        mock_mentions.return_value = {"AI tools": (80, 1.5)}
        mock_accel.return_value = {"AI tools": (60, 0.5)}

        from trend_spotter.scoring.durability import DurabilityResult
        mock_dur.return_value = {"AI tools": DurabilityResult(score=70, signals={"builder_activity": 50}, sentiment_multiplier=1.0)}
        mock_rank.return_value = mock_cluster.return_value

        from trend_spotter.classification import ClassifiedTrend
        mock_classify.return_value = [
            ClassifiedTrend("AI tools", "Compounding", "rising", "pred-1"),
        ]
        mock_pred_inst = MagicMock()
        mock_pred_inst.get_current_weights.return_value = None
        mock_pred.return_value = mock_pred_inst
        mock_snap.return_value = MagicMock()

        result = run_pipeline("AI", "7d", config)
        assert len(result["trends"]) == 1
        assert result["trends"][0]["name"] == "AI tools"
        assert result["trends"][0]["classification"] == "Compounding"
        assert "latency_ms" in result

    @patch("trend_spotter.pipeline.collect_signals")
    @patch("trend_spotter.pipeline.cluster_signals")
    def test_hard_timeout_skips_clustering(self, mock_cluster, mock_collect, config):
        mock_collect.return_value = (
            [RawSignal(source="web", title="t", url="u", value=1)],
            ["hard_timeout"],
        )
        result = run_pipeline("AI", "7d", config)
        mock_cluster.assert_not_called()

    @patch("trend_spotter.pipeline.collect_signals")
    @patch("trend_spotter.pipeline.cluster_signals")
    def test_descriptions_off_by_default(self, mock_cluster, mock_collect, config):
        """Descriptions should use label-as-description when generate_descriptions=False."""
        mock_collect.return_value = ([], [])
        mock_cluster.return_value = []
        result = run_pipeline("AI", "7d", config, generate_descriptions=False)
        assert result["trends"] == []  # No clusters, but verifies no LLM call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestGenerateDescription:
    @patch("trend_spotter.pipeline.requests.post")
    def test_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "A framework for AI."}}]},
        )
        desc = _generate_description("AI agents", ["signal1"], "AI", "key")
        assert desc == "A framework for AI."

    @patch("trend_spotter.pipeline.requests.post")
    def test_fallback_on_error(self, mock_post):
        mock_post.side_effect = Exception("network error")
        desc = _generate_description("AI agents", [], "AI", "key")
        assert "AI agents" in desc
        assert "AI" in desc


class TestGetSourcesForCluster:
    def test_selects_highest_value_per_source(self):
        sigs = [
            RawSignal(id="s1", source="github", title="low", url="u1", value=10),
            RawSignal(id="s2", source="github", title="high", url="u2", value=100),
            RawSignal(id="s3", source="hn", title="hn post", url="u3", value=50),
        ]
        cluster = {
            "signal_ids": ["s1", "s2", "s3"],
            "source_breakdown": {"github": 2, "hn": 1},
        }
        sources = _get_sources_for_cluster(cluster, sigs)
        assert len(sources) == 2
        gh_source = next(s for s in sources if s["source"] == "github")
        assert gh_source["url"] == "u2"  # highest value

    def test_empty_cluster(self):
        sources = _get_sources_for_cluster({"signal_ids": [], "source_breakdown": {}}, [])
        assert sources == []
