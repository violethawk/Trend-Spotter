"""Query routing and breadth control for Trend Spotter.

The QueryRouter coordinates query variants and parallel data retrieval
across available sources. It discovers which sources are configured
at runtime and only calls those with valid API keys.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from ..config import Config
from ..signal import RawSignal
from .sources import Source, get_available_sources


def collect_signals(
    field: str,
    window: str,
    config: Config,
    *,
    max_calls: int = 15,
    overall_timeout: float = 60.0,
    start_time: float = None,
) -> Tuple[List[RawSignal], List[str]]:
    """Collect signals across query variants and available sources.

    Only sources with valid API keys are queried. If a source fails
    during the run it is removed for subsequent variants.

    Args:
        field: The technology or topic of interest.
        window: Time window token ('1d', '7d' or '30d').
        config: Loaded configuration (used for OpenAI; source keys
            are read from the environment by each source).
        max_calls: Hard cap on the total number of API calls.
        overall_timeout: Maximum total runtime in seconds.
        start_time: Monotonic timestamp when the run started.

    Returns:
        A tuple of (signals, run_data_gaps).
    """
    if start_time is None:
        start_time = time.monotonic()

    # Discover available sources
    available = get_available_sources()
    active: Dict[str, Source] = {s.name: s for s in available}

    if not active:
        return [], ["no_sources_available"]

    variants = [field, f"{field} framework", f"{field} tool"]
    run_data_gaps: List[str] = []
    aggregated: List[RawSignal] = []
    calls_made = 0

    field_re = re.compile(re.escape(field), re.IGNORECASE)

    for i, variant in enumerate(variants):
        if calls_made >= max_calls or not active:
            break
        if time.monotonic() - start_time > overall_timeout:
            run_data_gaps.append("hard_timeout")
            break

        sources_to_call = list(active.values())
        futures = {}
        with ThreadPoolExecutor(max_workers=len(sources_to_call)) as executor:
            for source in sources_to_call:
                if calls_made >= max_calls:
                    break
                futures[executor.submit(source.fetch, variant, window)] = source
                calls_made += 1

            for future in as_completed(futures):
                source = futures[future]
                try:
                    signals = future.result()
                    aggregated.extend(signals)
                except Exception:
                    active.pop(source.name, None)
                    run_data_gaps.append(source.name)

        # Check if we have enough relevant signals
        relevant_count = sum(
            1 for sig in aggregated
            if field_re.search((sig.title or "") + " " + (sig.snippet or ""))
        )
        if relevant_count >= 5:
            break
        if i == len(variants) - 1 and relevant_count < 5:
            run_data_gaps.append("insufficient_results")

    return aggregated, run_data_gaps
