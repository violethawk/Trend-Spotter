"""Functions for retrieving raw signals from external APIs.

This module encapsulates the network requests to SerpAPI, GitHub and
Hacker News. Each function returns a list of :class:`RawSignal`
instances. Errors are surfaced as exceptions so the caller can decide
whether to retry or skip a source. Callers should provide sensible
timeouts to prevent long-running requests.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

from .signal import RawSignal


logger = logging.getLogger(__name__)


def _window_to_tbs(window: str) -> str:
    """Map a time window token to SerpAPI's tbs parameter."""
    mapping = {
        "1d": "qdr:d",
        "7d": "qdr:w",
        "30d": "qdr:m",
    }
    if window not in mapping:
        raise ValueError(f"Invalid time_window {window}; expected one of 1d, 7d, 30d")
    return mapping[window]


def fetch_web(query: str, window: str, serpapi_key: str, *, timeout: float = 10.0) -> List[RawSignal]:
    """Retrieve web articles via SerpAPI."""
    endpoint = "https://serpapi.com/search.json"
    tbs = _window_to_tbs(window)
    params = {
        "q": query,
        "num": 10,
        "tbs": tbs,
        "api_key": serpapi_key,
    }
    resp = requests.get(endpoint, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("organic_results", [])
    signals = []
    for item in results:
        title = item.get("title") or item.get("header")
        url = item.get("link") or item.get("url")
        snippet = item.get("snippet")
        if title and url:
            signals.append(RawSignal(
                source="web",
                title=title,
                url=url,
                snippet=snippet,
                value=1.0,
            ))
    return signals


def fetch_github(query: str, window: str, github_token: Optional[str], *, timeout: float = 10.0) -> List[RawSignal]:
    """Retrieve GitHub repositories matching the query.

    Extracts forks_count, created_at, and pushed_at into extras for
    use by durability scoring.
    """
    endpoint = "https://api.github.com/search/repositories"
    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": 10,
    }
    headers = {
        "Accept": "application/vnd.github+json",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    resp = requests.get(endpoint, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    signals = []
    for repo in items:
        full_name = repo.get("full_name")
        html_url = repo.get("html_url")
        description = repo.get("description")
        stars = repo.get("stargazers_count", 0)
        if full_name and html_url:
            signals.append(RawSignal(
                source="github",
                title=full_name,
                url=html_url,
                snippet=description,
                value=float(stars),
                extras={
                    "forks_count": repo.get("forks_count", 0),
                    "created_at": repo.get("created_at"),
                    "pushed_at": repo.get("pushed_at"),
                },
            ))
    return signals


def fetch_hn(query: str, window: str, *, timeout: float = 10.0) -> List[RawSignal]:
    """Retrieve Hacker News stories matching the query within a time window.

    Extracts num_comments into extras for use by durability scoring.
    """
    endpoint = "https://hn.algolia.com/api/v1/search"
    now = datetime.now(timezone.utc)
    delta_map = {
        "1d": 1,
        "7d": 7,
        "30d": 30,
    }
    if window not in delta_map:
        raise ValueError(f"Invalid time_window {window}; expected one of 1d, 7d, 30d")
    start_time = now - timedelta(days=delta_map[window])
    unix_start = int(start_time.timestamp())
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{unix_start}",
        "hitsPerPage": 10,
    }
    resp = requests.get(endpoint, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    hits = data.get("hits", [])
    signals = []
    for hit in hits:
        title = hit.get("title") or hit.get("story_title")
        url = hit.get("url")
        object_id = hit.get("objectID")
        points = hit.get("points", 0)
        num_comments = hit.get("num_comments", 0)
        if not url and object_id:
            url = f"https://news.ycombinator.com/item?id={object_id}"
        if title and url:
            signals.append(RawSignal(
                source="hn",
                title=title,
                url=url,
                snippet=None,
                value=float(points),
                extras={
                    "num_comments": num_comments,
                },
            ))
    return signals
