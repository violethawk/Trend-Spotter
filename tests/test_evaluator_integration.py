"""Integration tests for the evaluation engine."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from trend_spotter.config import Config
from trend_spotter.evaluation.evaluator import (
    evaluate_prediction,
    _extract_original_urls,
    _normalize_url,
    _count_trend_presence,
    _count_signals_with_growth,
    _get_matching_signals,
    _has_signal_evidence,
)
from trend_spotter.signal import RawSignal


def _sig(source="web", title="test", url="https://example.com", value=1.0, extras=None, snippet=None):
    return RawSignal(source=source, title=title, url=url, value=value,
                     extras=extras or {}, snippet=snippet)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

class TestNormalizeUrl:
    def test_strips_protocol(self):
        assert _normalize_url("https://example.com/path") == "example.com/path"

    def test_strips_www(self):
        assert _normalize_url("https://www.example.com") == "example.com"

    def test_strips_trailing_slash(self):
        assert _normalize_url("https://example.com/") == "example.com"

    def test_strips_query_params(self):
        assert _normalize_url("https://example.com/path?foo=bar") == "example.com/path"

    def test_case_insensitive(self):
        assert _normalize_url("HTTPS://Example.COM") == "example.com"


class TestExtractOriginalUrls:
    def test_extracts_from_evidence_json(self):
        pred = {"evidence_json": '[{"url": "https://example.com/1"}, {"url": "https://example.com/2"}]'}
        urls = _extract_original_urls(pred)
        assert len(urls) == 2
        assert "example.com/1" in urls

    def test_handles_missing_evidence(self):
        assert _extract_original_urls({}) == set()

    def test_handles_malformed_json(self):
        assert _extract_original_urls({"evidence_json": "not json"}) == set()


# ---------------------------------------------------------------------------
# Presence counting with URL dedup
# ---------------------------------------------------------------------------

class TestCountTrendPresence:
    def test_counts_matching_signals(self):
        sigs = [
            _sig(title="AI agent framework released"),
            _sig(title="New AI agent tools"),
            _sig(title="Rust memory safety update"),
        ]
        total, new = _count_trend_presence("AI agent", sigs, set())
        assert total == 2
        assert new == 2

    def test_deduplicates_known_urls(self):
        sigs = [
            _sig(title="AI agent framework", url="https://example.com/1"),
            _sig(title="AI agent tools", url="https://example.com/2"),
        ]
        original_urls = {"example.com/1"}
        total, new = _count_trend_presence("AI agent", sigs, original_urls)
        assert total == 2
        assert new == 1  # Only /2 is genuinely new

    def test_empty_label(self):
        total, new = _count_trend_presence("", [_sig()], set())
        assert total == 0


# ---------------------------------------------------------------------------
# Signal evidence checks
# ---------------------------------------------------------------------------

class TestHasSignalEvidence:
    def test_builder_activity(self):
        sigs = [_sig(source="github", extras={"forks_count": 10})]
        assert _has_signal_evidence("builder_activity", sigs)

    def test_adoption_quality(self):
        sigs = [_sig(source="hn", extras={"num_comments": 20})]
        assert _has_signal_evidence("adoption_quality", sigs)

    def test_discourse_depth(self):
        sigs = [_sig(snippet="production deployment architecture")]
        assert _has_signal_evidence("discourse_depth", sigs)

    def test_cross_platform(self):
        sigs = [_sig(source="web"), _sig(source="github")]
        assert _has_signal_evidence("cross_platform_presence", sigs)

    def test_problem_anchoring(self):
        sigs = [_sig(snippet="enterprise production use case")]
        assert _has_signal_evidence("problem_anchoring", sigs)

    def test_composability(self):
        sigs = [_sig(snippet="plugin sdk extension ecosystem")]
        assert _has_signal_evidence("composability", sigs)

    def test_unknown_signal(self):
        assert not _has_signal_evidence("nonexistent", [_sig()])


# ---------------------------------------------------------------------------
# Full evaluation
# ---------------------------------------------------------------------------

class TestEvaluatePrediction:
    @patch("trend_spotter.evaluation.evaluator._requery_signals")
    def test_compounding_correct(self, mock_requery):
        mock_requery.return_value = [
            _sig(title="AI agent framework v2", url="https://new1.com",
                 source="github", extras={"forks_count": 10}),
            _sig(title="AI agent orchestration", url="https://new2.com",
                 source="hn", extras={"num_comments": 15}),
            _sig(title="AI agent production deploy", url="https://new3.com",
                 snippet="production deployment"),
            _sig(title="AI agent ecosystem", url="https://new4.com",
                 snippet="plugin extension sdk"),
        ]
        prediction = {
            "prediction_id": "p1",
            "field": "AI",
            "window": "7d",
            "classification": "Compounding",
            "acceleration_score": 80,
            "durability_score": 75,
            "trend_label": "AI agent",
            "evidence_json": '[{"url": "https://old.com/1"}]',
            "original_signal_count": 3,
            "builder_activity": 60,
            "adoption_quality": 50,
            "discourse_depth": 40,
            "cross_platform_presence": 30,
            "problem_anchoring": 20,
            "composability": 10,
        }
        config = Config(openai_key="fake")
        result = evaluate_prediction(prediction, "30d", config)
        assert result.outcome in ("correct", "ambiguous")
        assert result.current_signal_count >= 1

    @patch("trend_spotter.evaluation.evaluator._requery_signals")
    def test_ignore_correct_when_absent(self, mock_requery):
        mock_requery.return_value = [
            _sig(title="Completely unrelated topic"),
        ]
        prediction = {
            "prediction_id": "p2",
            "field": "obscure",
            "window": "7d",
            "classification": "Ignore",
            "acceleration_score": 20,
            "durability_score": 15,
            "trend_label": "very niche framework xyz",
            "evidence_json": "[]",
            "original_signal_count": 2,
            "builder_activity": 10,
        }
        config = Config(openai_key="fake")
        result = evaluate_prediction(prediction, "30d", config)
        assert result.outcome == "correct"
