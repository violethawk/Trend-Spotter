"""Data structures for normalised signals.

This module defines the :class:`RawSignal` dataclass used throughout
Trend Spotter. Raw signals are normalised representations of items
retrieved from disparate sources (web search, GitHub repositories,
Hacker News stories). Each signal carries the minimal set of fields
required for downstream clustering and scoring.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


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
            repositories this is the stargazer count, for Hacker News
            posts this is the points count, and for web articles it is
            always ``1``.
        retrieved_at: ISO 8601 timestamp (UTC) marking when the signal
            was retrieved.
    """

    source: str
    title: str
    url: str
    value: float
    snippet: Optional[str] = None
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
            "retrieved_at": self.retrieved_at,
        }