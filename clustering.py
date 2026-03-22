"""Clustering of raw signals into trends.

This module implements both LLM-assisted clustering and a fallback
keyword-based grouping. Clustering is responsible for grouping
semantically similar raw signals into coherent trend clusters and
assigning each cluster a short label.

When the OpenAI Chat API is available, an LLM is used to perform
clustering according to a detailed system prompt. If the LLM call
fails or returns malformed JSON, the fallback algorithm groups
signals based on shared content words.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional

import requests

from .config import Config
from .signal import RawSignal


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You are a trend clustering engine.\n\n"
    "You will receive a list of raw signals (titles and snippets) collected about the field: \"{field}\".\n\n"
    "Your job is to group them into trend clusters. Each cluster represents a single coherent emerging trend.\n\n"
    "Rules:\n"
    "- Merge semantically similar items even if labeled differently.\n"
    "  Example: \"AI agents\", \"autonomous agents\", \"multi-agent frameworks\" → one cluster.\n"
    "- Do not split a single concept into multiple clusters.\n"
    "- Do not create a cluster with fewer than 2 signals unless there are fewer than 6 total signals.\n"
    "- Maximum 5 clusters regardless of input size.\n"
    "- Ignore signals that are clearly off-topic or spam.\n\n"
    "Return ONLY valid JSON. No explanation, no markdown, no preamble.\n\n"
    "Output format:\n"
    "[\n"
    "  {\n"
    "    \"cluster_id\": \"c1\",\n"
    "    \"label\": \"short trend name (3–6 words)\",\n"
    "    \"signal_ids\": [\"uuid1\", \"uuid2\", \"...\"]\n"
    "  }\n"
    "]"
)


def cluster_signals(signals: List[RawSignal], field: str, config: Config) -> List[Dict]:
    """Cluster raw signals into trends using the OpenAI API when possible.

    Args:
        signals: List of raw signals to cluster.
        field: The field being analysed (passed to the prompt).
        config: Loaded configuration with the OpenAI API key.

    Returns:
        A list of cluster dictionaries, each with ``cluster_id``, ``label``,
        ``signal_ids`` and ``source_breakdown``. If no clustering is
        possible, returns an empty list.
    """
    if not signals:
        return []
    # Attempt LLM clustering
    clusters: Optional[List[Dict]] = None
    try:
        clusters = _llm_cluster(signals, field, config.openai_key)
    except Exception as exc:
        logger.warning("LLM clustering failed; falling back to keyword grouping: %s", exc)
    if not clusters:
        clusters = _fallback_cluster(signals)
    # Compute source breakdown
    for cluster in clusters:
        breakdown = defaultdict(int)
        for sid in cluster["signal_ids"]:
            # find signal by id
            for sig in signals:
                if sig.id == sid:
                    breakdown[sig.source] += 1
                    break
        cluster["source_breakdown"] = dict(breakdown)
    return clusters


def _llm_cluster(signals: List[RawSignal], field: str, openai_key: str) -> List[Dict]:
    """Call the OpenAI Chat Completion API to cluster signals.

    Args:
        signals: List of raw signals.
        field: Field of interest for prompt.
        openai_key: API key for OpenAI.

    Returns:
        Parsed cluster list or ``None`` if parsing fails.

    Raises:
        Exception: If the API call fails.
    """
    # Prepare user payload: list of dicts with id, title and snippet
    payload_signals = [
        {
            "id": sig.id,
            "title": sig.title,
            "snippet": sig.snippet or "",
        }
        for sig in signals
    ]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(field=field)},
        {"role": "user", "content": json.dumps(payload_signals, ensure_ascii=False)},
    ]
    data = {
        "model": "gpt-3.5-turbo",
        "messages": messages,
        "max_tokens": 400,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=data,
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError(f"OpenAI API returned status {response.status_code}: {response.text}")
    resp = response.json()
    content = resp["choices"][0]["message"]["content"]
    # Strip code fences if present
    content = content.strip()
    # Remove leading and trailing triple backticks and optional json label
    if content.startswith("```"):
        # Remove first and last lines containing backticks
        lines = content.splitlines()
        # remove first line
        lines = lines[1:]
        # remove last line if it is backticks
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
        # If the first line is a language hint (e.g. json), remove it
        if content.startswith("json"):
            content = content[len("json") :].lstrip()
    try:
        parsed = json.loads(content)
        # Validate basic structure
        if not isinstance(parsed, list):
            raise ValueError("Top level is not a list")
        clusters: List[Dict] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            cluster_id = item.get("cluster_id") or item.get("id")
            label = item.get("label") or item.get("name")
            signal_ids = item.get("signal_ids") or item.get("signals")
            if not (cluster_id and label and isinstance(signal_ids, list)):
                continue
            clusters.append({
                "cluster_id": cluster_id,
                "label": label,
                "signal_ids": signal_ids,
            })
        return clusters
    except Exception as exc:
        logger.warning("Failed to parse LLM output: %s", exc)
        return []


def _fallback_cluster(signals: List[RawSignal]) -> List[Dict]:
    """Group signals by overlapping content words.

    This fallback is used when LLM clustering fails or is unavailable.

    Args:
        signals: List of raw signals.

    Returns:
        A list of cluster dictionaries. If there are fewer than six
        signals, clusters of size one are allowed. Otherwise, clusters
        with a single signal are dropped.
    """
    # Stopwords list for English; minimal but covers common words
    stopwords = {
        "the", "and", "for", "with", "that", "this", "into", "about", "using", "use",
        "of", "in", "on", "at", "to", "a", "an", "from", "by", "as", "is", "are",
        "was", "were", "be", "has", "have", "had", "new", "old", "based", "into",
        "via", "it's", "its", "it's", "or", "if", "but", "not", "than", "which", "when",
        "how", "what", "why", "who", "where", "their", "there", "our", "your", "you", "we",
    }

    # Precompute content words for each signal
    words_map: Dict[str, set] = {}
    token_pattern = re.compile(r"[A-Za-z0-9]+")
    for sig in signals:
        text = sig.title
        # Extract alphanumeric tokens
        tokens = token_pattern.findall(text.lower())
        content_words = {t for t in tokens if t not in stopwords}
        words_map[sig.id] = content_words
    # Initialize clusters list; each element is list of signal ids
    clusters: List[List[str]] = []
    # Represent cluster vocab (union of words) for assignment
    cluster_vocabs: List[set] = []
    for sig in signals:
        sid = sig.id
        words = words_map[sid]
        if not words:
            # If no meaningful words, place into own cluster
            clusters.append([sid])
            cluster_vocabs.append(words)
            continue
        placed = False
        for idx, vocab in enumerate(cluster_vocabs):
            # Compute overlap: number of shared content words
            overlap = len(words & vocab)
            if overlap >= 2:
                clusters[idx].append(sid)
                # Update cluster vocab to include union of words
                cluster_vocabs[idx] = vocab | words
                placed = True
                break
        if not placed:
            clusters.append([sid])
            cluster_vocabs.append(set(words))
    # Determine if we need to drop singletons
    if len(signals) >= 6:
        clusters = [c for c in clusters if len(c) > 1]
    # Limit to maximum of 5 clusters by size descending
    clusters = sorted(clusters, key=lambda c: len(c), reverse=True)[:5]
    result = []
    for i, cluster in enumerate(clusters, start=1):
        # Determine label: most frequent non-stopword term across titles
        word_counter: Counter[str] = Counter()
        for sid in cluster:
            words = words_map[sid]
            word_counter.update(words)
        if word_counter:
            label_word, _ = word_counter.most_common(1)[0]
        else:
            label_word = "trend"
        result.append({
            "cluster_id": f"c{i}",
            "label": label_word,
            "signal_ids": cluster,
        })
    return result