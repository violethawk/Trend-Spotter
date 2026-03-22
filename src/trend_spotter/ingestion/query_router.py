"""Query routing and breadth control for Trend Spotter.

The QueryRouter coordinates query variants and parallel data retrieval
across multiple sources. It implements the decision tree described in
the specification:

1. Issue a broad query on the provided field.
2. Evaluate whether at least five signals mention the field in the
   title or snippet. If not, refine the query by appending
   ``framework`` and, if necessary, ``tool``.
3. Limit the total number of API calls to prevent runaway usage.
4. Stop querying a source after any failure during the run.

The router returns the aggregated raw signals and a list of
run‑level data gaps (e.g. failed sources or insufficient results).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Tuple

from ..config import Config
from .retrieval import fetch_github, fetch_hn, fetch_web
from ..signal import RawSignal


def collect_signals(
    field: str,
    window: str,
    config: Config,
    *,
    max_calls: int = 15,
    overall_timeout: float = 60.0,
    start_time: float = None,
) -> Tuple[List[RawSignal], List[str]]:
    """Collect signals across query variants and sources.

    Args:
        field: The technology or topic of interest.
        window: Time window token (``'1d'``, ``'7d'`` or ``'30d'``).
        config: Loaded configuration containing API keys.
        max_calls: Hard cap on the total number of API calls.
        overall_timeout: Maximum total runtime in seconds.
        start_time: Timestamp when the run started (monotonic time). If
            ``None`` the current monotonic time will be used.

    Returns:
        A tuple of (signals, run_data_gaps).
    """
    if start_time is None:
        start_time = time.monotonic()

    # Query variants in order of refinement
    variants = [field, f"{field} framework", f"{field} tool"]
    run_data_gaps: List[str] = []
    aggregated: List[RawSignal] = []
    # Active sources; if a source fails once it is removed
    active_sources = {"web", "github", "hn"}
    calls_made = 0

    # Precompile regex to detect presence of the field in titles/snippets
    field_re = re.compile(re.escape(field), re.IGNORECASE)

    for i, variant in enumerate(variants):
        if calls_made >= max_calls or not active_sources:
            break
        # Check timeout
        if time.monotonic() - start_time > overall_timeout:
            # Hard timeout; indicate but allow upper layer to handle partial data
            run_data_gaps.append("hard_timeout")
            break
        # Determine which sources to call this round
        sources_to_call = list(active_sources)
        # Use a thread pool to fire calls concurrently
        futures = {}
        with ThreadPoolExecutor(max_workers=len(sources_to_call)) as executor:
            for source in sources_to_call:
                if calls_made >= max_calls:
                    break
                if source == "web":
                    futures[executor.submit(fetch_web, variant, window, config.serpapi_key)] = source
                elif source == "github":
                    futures[executor.submit(fetch_github, variant, window, config.github_token)] = source
                elif source == "hn":
                    futures[executor.submit(fetch_hn, variant, window)] = source
                calls_made += 1
            # Collect results; any exception will remove the source
            for future in as_completed(futures):
                source = futures[future]
                try:
                    signals = future.result()
                    aggregated.extend(signals)
                except Exception:
                    # Mark source as failed; do not retry later
                    active_sources.discard(source)
                    run_data_gaps.append(source)
        # After collecting this round, check if we have enough relevant signals
        # Count signals where field appears in title or snippet
        relevant_count = 0
        for sig in aggregated:
            # Combine title and snippet and check for the field
            text = (sig.title or "") + " " + (sig.snippet or "")
            if field_re.search(text):
                relevant_count += 1
        if relevant_count >= 5:
            break
        # If not last variant and still insufficient, continue to next variant
        # If this is the last variant and still insufficient, record gap
        if i == len(variants) - 1 and relevant_count < 5:
            run_data_gaps.append("insufficient_results")

    return aggregated, run_data_gaps