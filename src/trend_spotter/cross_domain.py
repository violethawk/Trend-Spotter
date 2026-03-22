"""Cross-domain trend analysis for Trend Spotter (Phase 7).

This module detects when the same underlying trend emerges
independently across multiple domains, signalling a structural
shift broader than any single field. It compares stored predictions
across fields using LLM-assisted semantic similarity.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from .config import Config
from .persistence.prediction_store import PredictionStore

logger = logging.getLogger(__name__)

# Minimum confidence for a cross-domain match
CONFIDENCE_THRESHOLD = 0.7

# Minimum number of domains for a cross-domain trend
MIN_DOMAINS = 2


@dataclass
class CrossDomainMatch:
    """A single domain's contribution to a cross-domain trend."""
    field: str
    trend_label: str
    prediction_id: Optional[str]
    acceleration_score: int
    durability_score: int
    classification: str


@dataclass
class CrossDomainTrend:
    """A trend detected across multiple independent domains."""
    meta_label: str
    description: str
    domains: List[CrossDomainMatch]
    domain_count: int
    confidence: float
    convergence_window: str
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meta_label": self.meta_label,
            "description": self.description,
            "domain_count": self.domain_count,
            "confidence": self.confidence,
            "convergence_window": self.convergence_window,
            "detected_at": self.detected_at,
            "domains": [asdict(d) for d in self.domains],
        }


def detect_cross_domain_trends(
    store: PredictionStore,
    config: Config,
    fields: Optional[List[str]] = None,
    lookback_days: int = 30,
) -> List[CrossDomainTrend]:
    """Detect trends emerging across multiple domains.

    Args:
        store: Prediction store with accumulated predictions.
        config: Runtime config for LLM access.
        fields: Specific fields to compare. If None, uses all fields
            with recent predictions.
        lookback_days: How far back to search for predictions.

    Returns:
        List of CrossDomainTrend objects.
    """
    # Gather recent predictions grouped by field
    predictions = store.get_recent_predictions(lookback_days)
    if not predictions:
        return []

    trends_by_field: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pred in predictions:
        if fields and pred["field"] not in fields:
            continue
        trends_by_field[pred["field"]].append(pred)

    if len(trends_by_field) < MIN_DOMAINS:
        logger.info(
            "Only %d field(s) with recent predictions; need %d for cross-domain",
            len(trends_by_field), MIN_DOMAINS,
        )
        return []

    # Use LLM to find semantic matches across fields
    matches = _find_semantic_matches(trends_by_field, config)

    # Persist results
    for trend in matches:
        store.write_cross_domain_trend(trend)

    return matches


def _find_semantic_matches(
    trends_by_field: Dict[str, List[Dict[str, Any]]],
    config: Config,
) -> List[CrossDomainTrend]:
    """Use LLM to identify semantically similar trends across fields."""
    # Build the prompt with trend labels per field
    field_summaries = {}
    for fld, preds in trends_by_field.items():
        labels = list({p["trend_label"] for p in preds})
        field_summaries[fld] = labels

    prompt_lines = []
    for fld, labels in field_summaries.items():
        prompt_lines.append(f'Field "{fld}": {json.dumps(labels)}')

    user_content = "\n".join(prompt_lines)

    system_prompt = (
        "You are an analyst detecting structural shifts across domains. "
        "Given trends from different fields, identify which represent the "
        "same underlying structural shift emerging independently.\n\n"
        "Return a JSON array of matches. Each match has:\n"
        '- "meta_label": a unifying name for the cross-domain trend\n'
        '- "description": 1-2 sentence explanation of why these are related\n'
        '- "matches": array of {"field": "...", "trend_label": "..."}\n'
        '- "confidence": 0.0 to 1.0 (how confident these are the same shift)\n\n'
        "Only include matches with confidence >= 0.7. "
        "Only include trends that appear in at least 2 different fields. "
        "Return [] if no cross-domain patterns exist. "
        "Return ONLY valid JSON, no other text."
    )

    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 1000,
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {config.openai_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30,
        )
        if response.status_code != 200:
            logger.warning("Cross-domain LLM call failed: %d", response.status_code)
            return []

        resp = response.json()
        content = resp["choices"][0]["message"]["content"].strip()

        # Parse the LLM response
        raw_matches = json.loads(content)
        if not isinstance(raw_matches, list):
            return []

    except (json.JSONDecodeError, KeyError, requests.RequestException) as exc:
        logger.warning("Cross-domain analysis failed: %s", exc)
        return _fallback_keyword_matching(trends_by_field)

    # Convert LLM output to CrossDomainTrend objects
    # Build a lookup for prediction data
    pred_lookup: Dict[tuple, Dict] = {}
    for fld, preds in trends_by_field.items():
        for p in preds:
            pred_lookup[(fld, p["trend_label"])] = p

    results: List[CrossDomainTrend] = []
    for match in raw_matches:
        confidence = match.get("confidence", 0)
        if confidence < CONFIDENCE_THRESHOLD:
            continue

        match_entries = match.get("matches", [])
        if len(match_entries) < MIN_DOMAINS:
            continue

        # Deduplicate by field
        seen_fields: set = set()
        domain_matches: List[CrossDomainMatch] = []
        for m in match_entries:
            fld = m.get("field", "")
            label = m.get("trend_label", "")
            if fld in seen_fields:
                continue
            seen_fields.add(fld)

            pred = pred_lookup.get((fld, label))
            domain_matches.append(CrossDomainMatch(
                field=fld,
                trend_label=label,
                prediction_id=pred["prediction_id"] if pred else None,
                acceleration_score=pred["acceleration_score"] if pred else 0,
                durability_score=pred["durability_score"] if pred else 0,
                classification=pred["classification"] if pred else "Unknown",
            ))

        if len(domain_matches) < MIN_DOMAINS:
            continue

        # Compute convergence window from prediction dates
        convergence = _compute_convergence_window(domain_matches, pred_lookup)

        results.append(CrossDomainTrend(
            meta_label=match.get("meta_label", "Unknown"),
            description=match.get("description", ""),
            domains=domain_matches,
            domain_count=len(domain_matches),
            confidence=round(confidence, 2),
            convergence_window=convergence,
        ))

    return results


def _fallback_keyword_matching(
    trends_by_field: Dict[str, List[Dict[str, Any]]],
) -> List[CrossDomainTrend]:
    """Simple keyword-overlap fallback when LLM is unavailable."""
    # Extract tokens from each trend label
    import re
    stopwords = {
        "the", "and", "for", "with", "that", "this", "of", "in", "on",
        "at", "to", "from", "by", "as", "is", "are", "new", "based",
    }

    field_tokens: Dict[str, Dict[str, set]] = {}
    for fld, preds in trends_by_field.items():
        field_tokens[fld] = {}
        for p in preds:
            label = p["trend_label"]
            tokens = {
                t.lower() for t in re.findall(r"[A-Za-z0-9]+", label)
                if t.lower() not in stopwords and len(t) > 2
            }
            field_tokens[fld][label] = tokens

    # Compare across fields
    fields = list(field_tokens.keys())
    matches: Dict[str, List[CrossDomainMatch]] = {}

    for i, f1 in enumerate(fields):
        for f2 in fields[i + 1:]:
            for label1, tokens1 in field_tokens[f1].items():
                for label2, tokens2 in field_tokens[f2].items():
                    overlap = tokens1 & tokens2
                    if len(overlap) >= 2:
                        meta = " ".join(sorted(overlap))
                        if meta not in matches:
                            matches[meta] = []
                        # Avoid duplicate fields
                        existing_fields = {m.field for m in matches[meta]}
                        pred1 = next((p for p in trends_by_field[f1] if p["trend_label"] == label1), None)
                        pred2 = next((p for p in trends_by_field[f2] if p["trend_label"] == label2), None)
                        if f1 not in existing_fields and pred1:
                            matches[meta].append(CrossDomainMatch(
                                field=f1, trend_label=label1,
                                prediction_id=pred1.get("prediction_id"),
                                acceleration_score=pred1.get("acceleration_score", 0),
                                durability_score=pred1.get("durability_score", 0),
                                classification=pred1.get("classification", "Unknown"),
                            ))
                        if f2 not in existing_fields and pred2:
                            matches[meta].append(CrossDomainMatch(
                                field=f2, trend_label=label2,
                                prediction_id=pred2.get("prediction_id"),
                                acceleration_score=pred2.get("acceleration_score", 0),
                                durability_score=pred2.get("durability_score", 0),
                                classification=pred2.get("classification", "Unknown"),
                            ))

    results = []
    for meta_label, domain_matches in matches.items():
        if len(domain_matches) >= MIN_DOMAINS:
            results.append(CrossDomainTrend(
                meta_label=meta_label,
                description=f"Keyword overlap detected across {len(domain_matches)} domains",
                domains=domain_matches,
                domain_count=len(domain_matches),
                confidence=0.5,  # Lower confidence for keyword fallback
                convergence_window="unknown",
            ))

    return results


def _compute_convergence_window(
    domain_matches: List[CrossDomainMatch],
    pred_lookup: Dict[tuple, Dict],
) -> str:
    """Compute how close in time the trends emerged across domains."""
    dates = []
    for m in domain_matches:
        pred = pred_lookup.get((m.field, m.trend_label))
        if pred and pred.get("created_at"):
            try:
                dt = datetime.fromisoformat(pred["created_at"])
                dates.append(dt)
            except (ValueError, TypeError):
                pass

    if len(dates) < 2:
        return "unknown"

    span = max(dates) - min(dates)
    days = span.days
    if days <= 1:
        return "1d"
    elif days <= 7:
        return f"{days}d"
    elif days <= 30:
        return f"{days}d"
    else:
        return f"{days}d"
