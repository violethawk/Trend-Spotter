"""Pluggable data source registry for Trend Spotter.

Each source is a self-contained class that declares its own
configuration requirements. The registry discovers available sources
at startup based on which environment variables are present.

To add a new source:
1. Subclass Source
2. Implement name, required_keys, and fetch()
3. Add an instance to SOURCES
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from ..signal import RawSignal

logger = logging.getLogger(__name__)


class Source(ABC):
    """Base class for a signal data source."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. 'github', 'hn', 'web')."""

    @property
    @abstractmethod
    def required_keys(self) -> List[str]:
        """Environment variable names required for this source.

        If empty, the source is always available (e.g. HN Algolia).
        """

    def is_available(self) -> bool:
        """Check if all required keys are present in the environment."""
        return all(os.getenv(k) for k in self.required_keys)

    def get_key(self, key_name: str) -> Optional[str]:
        """Get an environment variable value."""
        return os.getenv(key_name)

    @abstractmethod
    def fetch(
        self, query: str, window: str, *, timeout: float = 10.0
    ) -> List[RawSignal]:
        """Fetch signals for the given query and time window."""


# ---------------------------------------------------------------------------
# Built-in sources
# ---------------------------------------------------------------------------

class WebSource(Source):
    """Web search via SerpAPI."""

    @property
    def name(self) -> str:
        return "web"

    @property
    def required_keys(self) -> List[str]:
        return ["SERPAPI_KEY"]

    def fetch(
        self, query: str, window: str, *, timeout: float = 10.0
    ) -> List[RawSignal]:
        serpapi_key = self.get_key("SERPAPI_KEY")
        tbs_map = {"1d": "qdr:d", "7d": "qdr:w", "30d": "qdr:m"}
        tbs = tbs_map.get(window)
        if not tbs:
            raise ValueError(f"Invalid window {window}")

        resp = requests.get(
            "https://serpapi.com/search.json",
            params={"q": query, "num": 10, "tbs": tbs, "api_key": serpapi_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("organic_results", [])
        signals = []
        for item in results:
            title = item.get("title") or item.get("header")
            url = item.get("link") or item.get("url")
            snippet = item.get("snippet")
            if title and url:
                signals.append(RawSignal(
                    source="web", title=title, url=url,
                    snippet=snippet, value=1.0,
                ))
        return signals


class GitHubSource(Source):
    """GitHub repository search (works without token, better with one)."""

    @property
    def name(self) -> str:
        return "github"

    @property
    def required_keys(self) -> List[str]:
        return []  # Works without a token (lower rate limit)

    def fetch(
        self, query: str, window: str, *, timeout: float = 10.0
    ) -> List[RawSignal]:
        headers = {"Accept": "application/vnd.github+json"}
        token = self.get_key("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": 10},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        signals = []
        for repo in items:
            full_name = repo.get("full_name")
            html_url = repo.get("html_url")
            if full_name and html_url:
                signals.append(RawSignal(
                    source="github",
                    title=full_name,
                    url=html_url,
                    snippet=repo.get("description"),
                    value=float(repo.get("stargazers_count", 0)),
                    extras={
                        "forks_count": repo.get("forks_count", 0),
                        "created_at": repo.get("created_at"),
                        "pushed_at": repo.get("pushed_at"),
                    },
                ))
        return signals


class HackerNewsSource(Source):
    """Hacker News via the Algolia API (no key required)."""

    @property
    def name(self) -> str:
        return "hn"

    @property
    def required_keys(self) -> List[str]:
        return []

    def fetch(
        self, query: str, window: str, *, timeout: float = 10.0
    ) -> List[RawSignal]:
        delta_map = {"1d": 1, "7d": 7, "30d": 30}
        days = delta_map.get(window)
        if days is None:
            raise ValueError(f"Invalid window {window}")
        start = datetime.now(timezone.utc) - timedelta(days=days)
        unix_start = int(start.timestamp())

        resp = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params={
                "query": query,
                "tags": "story",
                "numericFilters": f"created_at_i>{unix_start}",
                "hitsPerPage": 10,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        signals = []
        for hit in resp.json().get("hits", []):
            title = hit.get("title") or hit.get("story_title")
            url = hit.get("url")
            object_id = hit.get("objectID")
            if not url and object_id:
                url = f"https://news.ycombinator.com/item?id={object_id}"
            if title and url:
                signals.append(RawSignal(
                    source="hn",
                    title=title,
                    url=url,
                    snippet=None,
                    value=float(hit.get("points", 0)),
                    extras={"num_comments": hit.get("num_comments", 0)},
                ))
        return signals


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# All known sources. To add a new source, append an instance here.
ALL_SOURCES: List[Source] = [
    WebSource(),
    GitHubSource(),
    HackerNewsSource(),
]


def get_available_sources() -> List[Source]:
    """Return sources whose required API keys are configured."""
    available = [s for s in ALL_SOURCES if s.is_available()]
    if not available:
        logger.warning("No data sources available — check API keys")
    else:
        names = [s.name for s in available]
        logger.info("Available sources: %s", ", ".join(names))
    return available
