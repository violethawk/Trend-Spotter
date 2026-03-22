"""Tests for Phase 7 cross-domain trend detection."""

import pytest

from trend_spotter.cross_domain import (
    CrossDomainMatch,
    CrossDomainTrend,
    _fallback_keyword_matching,
    _compute_convergence_window,
    MIN_DOMAINS,
)
from trend_spotter.persistence.prediction_store import PredictionStore


@pytest.fixture
def store():
    return PredictionStore(db_path=":memory:")


def _pred(field, label, classification="Compounding", pred_id="p1",
          accel=80, dur=75, created="2025-06-01T00:00:00"):
    return {
        "prediction_id": pred_id,
        "field": field,
        "trend_label": label,
        "classification": classification,
        "acceleration_score": accel,
        "durability_score": dur,
        "created_at": created,
    }


class TestFallbackKeywordMatching:
    def test_matching_keywords_across_fields(self):
        trends_by_field = {
            "software engineering": [_pred("software engineering", "autonomous agent orchestration frameworks")],
            "finance": [_pred("finance", "autonomous agent trading systems")],
        }
        results = _fallback_keyword_matching(trends_by_field)
        assert len(results) >= 1
        assert results[0].domain_count >= 2
        assert results[0].confidence == 0.5  # fallback confidence

    def test_no_match_when_labels_unrelated(self):
        trends_by_field = {
            "software engineering": [_pred("software engineering", "Rust memory safety")],
            "finance": [_pred("finance", "blockchain derivatives")],
        }
        results = _fallback_keyword_matching(trends_by_field)
        assert len(results) == 0

    def test_single_field_returns_nothing(self):
        trends_by_field = {
            "AI": [_pred("AI", "large language models")],
        }
        results = _fallback_keyword_matching(trends_by_field)
        assert len(results) == 0


class TestConvergenceWindow:
    def test_same_day(self):
        matches = [
            CrossDomainMatch("f1", "t1", "p1", 80, 70, "Compounding"),
            CrossDomainMatch("f2", "t2", "p2", 75, 65, "Compounding"),
        ]
        lookup = {
            ("f1", "t1"): {"created_at": "2025-06-01T10:00:00+00:00"},
            ("f2", "t2"): {"created_at": "2025-06-01T14:00:00+00:00"},
        }
        result = _compute_convergence_window(matches, lookup)
        assert result == "1d" or result.endswith("d")

    def test_missing_dates(self):
        matches = [
            CrossDomainMatch("f1", "t1", "p1", 80, 70, "Compounding"),
        ]
        result = _compute_convergence_window(matches, {})
        assert result == "unknown"

    def test_multi_week_span(self):
        matches = [
            CrossDomainMatch("f1", "t1", "p1", 80, 70, "Compounding"),
            CrossDomainMatch("f2", "t2", "p2", 75, 65, "Compounding"),
        ]
        lookup = {
            ("f1", "t1"): {"created_at": "2025-06-01T00:00:00+00:00"},
            ("f2", "t2"): {"created_at": "2025-06-15T00:00:00+00:00"},
        }
        result = _compute_convergence_window(matches, lookup)
        assert result == "14d"


class TestCrossDomainTrend:
    def test_to_dict(self):
        trend = CrossDomainTrend(
            meta_label="AI agents",
            description="test",
            domains=[
                CrossDomainMatch("f1", "t1", "p1", 80, 70, "Compounding"),
            ],
            domain_count=1,
            confidence=0.9,
            convergence_window="7d",
        )
        d = trend.to_dict()
        assert d["meta_label"] == "AI agents"
        assert len(d["domains"]) == 1
        assert d["domains"][0]["field"] == "f1"


class TestPredictionStoreIntegration:
    def test_cross_domain_table_created(self, store):
        cur = store.conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cross_domain_trends'"
        )
        assert cur.fetchone() is not None

    def test_write_and_read_cross_domain(self, store):
        trend = CrossDomainTrend(
            meta_label="AI agents",
            description="Multi-domain emergence",
            domains=[
                CrossDomainMatch("AI", "agent frameworks", "p1", 85, 78, "Compounding"),
                CrossDomainMatch("finance", "trading agents", "p2", 72, 65, "Compounding"),
            ],
            domain_count=2,
            confidence=0.85,
            convergence_window="14d",
        )
        store.write_cross_domain_trend(trend)
        results = store.get_cross_domain_trends()
        assert len(results) == 1
        assert results[0]["meta_label"] == "AI agents"
        assert len(results[0]["matches"]) == 2

    def test_get_recent_predictions(self, store):
        from trend_spotter.classification import ClassifiedTrend
        from trend_spotter.scoring.durability import DurabilityResult

        ct = ClassifiedTrend("trend1", "Compounding", "rising", "pred-1")
        dr = DurabilityResult(75, {"builder_activity": 80}, 1.0)
        store.write_prediction(ct, dr, 85, "AI", "7d",
                               "2099-01-01T00:00:00", [])

        recent = store.get_recent_predictions(lookback_days=30)
        assert len(recent) == 1
        assert recent[0]["field"] == "AI"
