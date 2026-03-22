"""Data structures for normalised signals.

This module defines the :class:`RawSignal` dataclass used throughout
Trend Spotter. Raw signals are normalised representations of items
retrieved from disparate sources (web search, GitHub repositories,
Hacker News stories). Each signal carries the minimal set of fields
required for downstream clustering and scoring.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_LABEL_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "of", "in", "on",
    "at", "to", "a", "an", "from", "by", "as", "is", "are", "new",
    "based", "using", "via", "its",
})


def _simple_stem(word: str) -> str:
    """Minimal English stemmer for label canonicalization."""
    if len(word) <= 3:
        return word
    for suffix in ("ations", "ments", "ness", "ings", "tion", "sion",
                   "ment", "able", "ible", "ful", "ing", "ies", "es", "ed", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[:-len(suffix)]
    return word


def canonicalize_label(label: str) -> str:
    """Produce a stable canonical key from a cluster label.

    Lowercases, extracts alphanumeric tokens, removes stopwords,
    applies simple stemming, sorts alphabetically, and joins with
    underscores.

    This ensures that "AI agent frameworks" and "AI Agent Framework"
    and "Frameworks for AI agents" all produce the same key.
    """
    tokens = re.findall(r"[a-z0-9]+", label.lower())
    meaningful = sorted(
        _simple_stem(t) for t in tokens
        if t not in _LABEL_STOPWORDS and len(t) > 1
    )
    return "_".join(meaningful) if meaningful else label.lower().strip()


@dataclass
class RawSignal:
    """Normalised representation of an external signal.

    Attributes:
        id: A unique UUID for the signal. This is generated at
            instantiation.
        source: One of ``'web'``, ``'github'`` or ``'hn'``.
        title: Human readable title of the signal (article title,
            repository name or HN post title).
        url: A URL pointing to the source material.
        snippet: A short snippet or description of the signal. May be
            ``None`` for certain sources.
        value: Numeric value associated with the signal. For GitHub
            repositories this is the stargazer count, for Hacker News
            posts this is the points count, and for web articles it is
            always ``1``.
        extras: Source-specific metadata. For GitHub: ``forks_count``,
            ``created_at``, ``pushed_at``. For HN: ``num_comments``.
        retrieved_at: ISO 8601 timestamp (UTC) marking when the signal
            was retrieved.
    """

    source: str
    title: str
    url: str
    value: float
    snippet: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        """Return a serialisable representation of the signal."""
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "value": self.value,
            "extras": self.extras,
            "retrieved_at": self.retrieved_at,
        }
