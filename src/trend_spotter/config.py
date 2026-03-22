"""Configuration loader for Trend Spotter.

This module centralises environment variable handling. It uses
``python‑dotenv`` to load variables from a ``.env`` file at the
project root if present, then reads from the process environment.

Two variables are mandatory:

* ``SERPAPI_KEY`` – API key for [SerpAPI](https://serpapi.com/).
* ``OPENAI_API_KEY`` – API key for the OpenAI Chat API used for
  clustering and description generation.

The optional ``GITHUB_TOKEN`` increases the rate limit for GitHub
repository searches. If absent, requests will still execute but are
subject to the unauthenticated rate limit of 60 requests per hour.

If a required variable is missing, :func:`load_config` raises a
``RuntimeError`` with a clear message. This explicit failure
prevents silent misconfigurations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Default database location: src/trend_spotter/data/trend_spotter.db
_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = str(_PACKAGE_DIR / "data" / "trend_spotter.db")


@dataclass
class Config:
    """Data class representing runtime configuration.

    Attributes:
        openai_key:  OpenAI API key (required for clustering).
        serpapi_key: SerpAPI API key (optional; web source disabled without it).
        github_token: Optional GitHub personal access token.
    """

    openai_key: str
    serpapi_key: Optional[str] = None
    github_token: Optional[str] = None


def load_config() -> Config:
    """Load configuration from environment.

    Only OPENAI_API_KEY is required (for LLM clustering).
    Data source keys (SERPAPI_KEY, GITHUB_TOKEN) are optional —
    sources without keys are simply skipped.

    Returns:
        A :class:`Config` instance.

    Raises:
        RuntimeError: if OPENAI_API_KEY is missing.
    """
    load_dotenv()

    openai_key = os.getenv("OPENAI_API_KEY")
    serpapi_key = os.getenv("SERPAPI_KEY")
    github_token = os.getenv("GITHUB_TOKEN")

    if not openai_key:
        raise RuntimeError(
            "Missing required environment variable: OPENAI_API_KEY."
            " Please create a .env file or export the variable."
        )

    return Config(
        openai_key=openai_key,
        serpapi_key=serpapi_key,
        github_token=github_token,
    )