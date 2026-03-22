"""Top-level package for the Trend Spotter CLI.

This package provides the core logic to fetch, cluster and rank
emerging technology trends from multiple data sources. The CLI entry
point is defined in ``trend_spotter/cli.py`` and is exposed via the
``trend-spotter`` script.

The implementation follows the requirements defined in the
project specification:

* Query routing with breadth checks and limited refinements.
* Parallel data retrieval from SerpAPI, GitHub and Hacker News.
* Normalisation of signals into a common schema.
* LLM assisted clustering with a keyword fallback.
* Snapshot storage for acceleration scoring.
* Mentions and acceleration scoring with normalisation.
* Durability scoring with six structural signals.
* Classification into Compounding / Durable-Slow / Flash Trend / Ignore.
* Feedback loop with 30d / 90d evaluation.
* Adaptive weight tuning from outcome data.
* Diversity filtered ranking and final JSON output.

Environment variables are loaded via :mod:`python-dotenv`. Required
variables are ``SERPAPI_KEY`` and ``OPENAI_API_KEY``. The optional
``GITHUB_TOKEN`` elevates GitHub API rate limits.

See the README for usage instructions.
"""

__all__: list[str] = []
