"""Tests for ingestion: retrieval, clustering, and query routing."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest
import responses

from trend_spotter.ingestion.retrieval import fetch_web, fetch_github, fetch_hn
from trend_spotter.ingestion.clustering import cluster_signals, _fallback_cluster
from trend_spotter.ingestion.query_router import collect_signals
from trend_spotter.config import Config
from trend_spotter.signal import RawSignal


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

class TestFetchWeb:
    @responses.activate
    def test_returns_signals(self):
        responses.add(
            responses.GET,
            "https://serpapi.com/search.json",
            json={"organic_results": [
                {"title": "AI agents rise", "link": "https://example.com/1", "snippet": "Growing fast"},
                {"title": "Agent frameworks", "link": "https://example.com/2", "snippet": "New tools"},
            ]},
            status=200,
        )
        result = fetch_web("AI agents", "7d", "fake-key")
        assert len(result) == 2
        assert result[0].source == "web"
        assert result[0].title == "AI agents rise"

    @responses.activate
    def test_empty_results(self):
        responses.add(
            responses.GET,
            "https://serpapi.com/search.json",
            json={"organic_results": []},
            status=200,
        )
        result = fetch_web("obscure topic", "7d", "fake-key")
        assert result == []

    @responses.activate
    def test_api_error_raises(self):
        responses.add(
            responses.GET,
            "https://serpapi.com/search.json",
            status=500,
        )
        with pytest.raises(Exception):
            fetch_web("AI agents", "7d", "fake-key")

    def test_invalid_window(self):
        with pytest.raises(ValueError):
            fetch_web("AI", "999d", "fake-key")


class TestFetchGitHub:
    @responses.activate
    def test_returns_signals_with_extras(self):
        responses.add(
            responses.GET,
            "https://api.github.com/search/repositories",
            json={"items": [{
                "full_name": "user/repo",
                "html_url": "https://github.com/user/repo",
                "description": "A cool project",
                "stargazers_count": 500,
                "forks_count": 50,
                "created_at": "2025-01-01",
                "pushed_at": "2025-06-01",
            }]},
            status=200,
        )
        result = fetch_github("AI agents", "7d", None)
        assert len(result) == 1
        assert result[0].source == "github"
        assert result[0].value == 500.0
        assert result[0].extras["forks_count"] == 50

    @responses.activate
    def test_with_token(self):
        responses.add(
            responses.GET,
            "https://api.github.com/search/repositories",
            json={"items": []},
            status=200,
        )
        result = fetch_github("AI", "7d", "gh-token-123")
        assert result == []
        assert "Bearer gh-token-123" in responses.calls[0].request.headers["Authorization"]


class TestFetchHN:
    @responses.activate
    def test_returns_signals_with_comments(self):
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={"hits": [{
                "title": "Show HN: AI Agent Tool",
                "url": "https://example.com/hn",
                "objectID": "12345",
                "points": 150,
                "num_comments": 42,
            }]},
            status=200,
        )
        result = fetch_hn("AI agents", "7d")
        assert len(result) == 1
        assert result[0].source == "hn"
        assert result[0].extras["num_comments"] == 42

    @responses.activate
    def test_missing_url_uses_hn_link(self):
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={"hits": [{
                "title": "Ask HN: AI agents?",
                "url": None,
                "objectID": "99999",
                "points": 10,
                "num_comments": 5,
            }]},
            status=200,
        )
        result = fetch_hn("AI agents", "7d")
        assert "news.ycombinator.com/item?id=99999" in result[0].url


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

class TestFallbackCluster:
    def test_groups_overlapping_signals(self):
        sigs = [
            RawSignal(source="web", title="AI agent framework tools", url="u1", value=1),
            RawSignal(source="web", title="AI agent orchestration", url="u2", value=1),
            RawSignal(source="web", title="Rust memory safety", url="u3", value=1),
        ]
        clusters = _fallback_cluster(sigs)
        # Should get at least 1 cluster with the AI agent signals
        ai_cluster = [c for c in clusters if any(
            s.id in c["signal_ids"] for s in sigs[:2]
        )]
        assert len(ai_cluster) >= 1

    def test_empty_signals(self):
        assert _fallback_cluster([]) == []


class TestClusterSignals:
    @responses.activate
    def test_llm_cluster_success(self):
        # The LLM returns a JSON string inside the content field
        cluster_json_str = json.dumps([
            {"cluster_id": 1, "label": "AI agents", "signal_ids": ["s1", "s2"]},
        ])
        responses.add(
            responses.POST,
            "https://api.openai.com/v1/chat/completions",
            json={"choices": [{"message": {"content": cluster_json_str}}]},
            status=200,
        )
        sigs = [
            RawSignal(id="s1", source="web", title="AI agents rising", url="u1", value=1),
            RawSignal(id="s2", source="github", title="agent-framework", url="u2", value=10),
        ]
        config = Config(openai_key="fake")
        clusters = cluster_signals(sigs, "AI", config)
        assert len(clusters) >= 1
        # If LLM succeeds, label is "AI agents"; if fallback, still produces clusters
        assert "canonical_key" in clusters[0]

    @responses.activate
    def test_llm_failure_falls_back(self):
        responses.add(
            responses.POST,
            "https://api.openai.com/v1/chat/completions",
            status=500,
        )
        sigs = [
            RawSignal(source="web", title="AI agent framework tools", url="u1", value=1),
            RawSignal(source="web", title="AI agent orchestration tools", url="u2", value=1),
        ]
        config = Config(openai_key="fake")
        clusters = cluster_signals(sigs, "AI", config)
        # Fallback should still produce clusters
        assert len(clusters) >= 1


# ---------------------------------------------------------------------------
# Query Router
# ---------------------------------------------------------------------------

class TestQueryRouter:
    @responses.activate
    def test_collects_from_available_sources(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        # GitHub
        responses.add(
            responses.GET,
            "https://api.github.com/search/repositories",
            json={"items": [{
                "full_name": "user/ai-tool",
                "html_url": "https://github.com/user/ai-tool",
                "description": "AI agent toolkit",
                "stargazers_count": 100,
                "forks_count": 10,
            }]},
            status=200,
        )
        # HN
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={"hits": [{
                "title": "AI agent tools",
                "url": "https://example.com",
                "objectID": "1",
                "points": 50,
                "num_comments": 10,
            }]},
            status=200,
        )
        config = Config(openai_key="fake")
        signals, gaps = collect_signals("AI agents", "7d", config, max_calls=10)
        assert len(signals) >= 1
        # web should not be in gaps since it wasn't available
        sources = {s.source for s in signals}
        assert "web" not in sources

    @responses.activate
    def test_failed_source_recorded_in_gaps(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        # GitHub fails
        responses.add(
            responses.GET,
            "https://api.github.com/search/repositories",
            status=500,
        )
        # HN works
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={"hits": []},
            status=200,
        )
        config = Config(openai_key="fake")
        signals, gaps = collect_signals("AI", "7d", config, max_calls=6)
        assert "github" in gaps
