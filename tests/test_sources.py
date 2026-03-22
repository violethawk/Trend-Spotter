"""Tests for the pluggable source registry."""

import os

import pytest

from trend_spotter.ingestion.sources import (
    ALL_SOURCES,
    GitHubSource,
    HackerNewsSource,
    WebSource,
    get_available_sources,
)


class TestSourceAvailability:
    def test_hn_always_available(self):
        """HN requires no keys — always available."""
        hn = HackerNewsSource()
        assert hn.is_available()

    def test_github_always_available(self):
        """GitHub works without a token (lower rate limit)."""
        gh = GitHubSource()
        assert gh.is_available()

    def test_web_requires_serpapi_key(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        web = WebSource()
        assert not web.is_available()

    def test_web_available_with_key(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "test-key")
        web = WebSource()
        assert web.is_available()

    def test_get_available_excludes_unconfigured(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        available = get_available_sources()
        names = [s.name for s in available]
        assert "web" not in names
        assert "github" in names
        assert "hn" in names

    def test_get_available_includes_all_when_configured(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "test-key")
        available = get_available_sources()
        names = [s.name for s in available]
        assert "web" in names
        assert "github" in names
        assert "hn" in names


class TestSourceProperties:
    def test_all_sources_have_unique_names(self):
        names = [s.name for s in ALL_SOURCES]
        assert len(names) == len(set(names))

    def test_all_sources_have_name(self):
        for source in ALL_SOURCES:
            assert source.name
            assert isinstance(source.name, str)
