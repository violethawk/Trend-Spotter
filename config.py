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
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    """Data class representing runtime configuration.

    Attributes:
        serpapi_key: SerpAPI API key.
        openai_key:  OpenAI API key.
        github_token: Optional GitHub personal access token.
    """

    serpapi_key: str
    openai_key: str
    github_token: Optional[str]


def load_config() -> Config:
    """Load configuration from environment.

    Returns:
        A :class:`Config` instance.

    Raises:
        RuntimeError: if a required variable is missing.
    """
    # Load variables from .env if present.
    load_dotenv()

    serpapi_key = os.getenv("SERPAPI_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    github_token = os.getenv("GITHUB_TOKEN")

    missing = []
    if not serpapi_key:
        missing.append("SERPAPI_KEY")
    if not openai_key:
        missing.append("OPENAI_API_KEY")

    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}."
            " Please create a .env file or export the variables."
        )

    return Config(
        serpapi_key=serpapi_key,
        openai_key=openai_key,
        github_token=github_token,
    )