"""Tests for Phase 6 weight tuning."""

import pytest

from trend_spotter.evaluation.weight_tuning import (
    compute_updated_weights,
    MIN_SAMPLE_SIZE,
    WEIGHT_FLOOR_MULT,
    WEIGHT_CEILING_MULT,
)
from trend_spotter.scoring.durability import DURABILITY_WEIGHTS


def _make_correlation(delta_override=None, samples=60):
    """Build a correlation dict with uniform values or per-signal overrides."""
    default_delta = 0.0
    correlation = {}
    for name in DURABILITY_WEIGHTS:
        d = (delta_override or {}).get(name, default_delta)
        correlation[name] = {
            "avg_correct": 50 + d,
            "avg_incorrect": 50.0,
            "delta": d,
            "sample_correct": samples,
            "sample_incorrect": samples,
        }
    return correlation


class TestMinSampleGuard:
    def test_insufficient_samples_returns_unchanged(self):
        correlation = _make_correlation(samples=10)
        new_weights, changelog = compute_updated_weights(
            correlation, dict(DURABILITY_WEIGHTS)
        )
        assert not changelog["applied"]
        assert new_weights == DURABILITY_WEIGHTS

    def test_sufficient_samples_proceeds(self):
        correlation = _make_correlation(
            delta_override={"builder_activity": 20}, samples=60,
        )
        new_weights, changelog = compute_updated_weights(
            correlation, dict(DURABILITY_WEIGHTS)
        )
        assert changelog["applied"]


class TestClamping:
    def test_extreme_positive_delta_clamped(self):
        correlation = _make_correlation(
            delta_override={"builder_activity": 200}, samples=60,
        )
        new_weights, _ = compute_updated_weights(
            correlation, dict(DURABILITY_WEIGHTS)
        )
        baseline = DURABILITY_WEIGHTS["builder_activity"]
        ceiling = baseline * WEIGHT_CEILING_MULT
        assert new_weights["builder_activity"] <= ceiling + 0.001

    def test_extreme_negative_delta_clamped(self):
        correlation = _make_correlation(
            delta_override={"composability": -200}, samples=60,
        )
        new_weights, _ = compute_updated_weights(
            correlation, dict(DURABILITY_WEIGHTS)
        )
        baseline = DURABILITY_WEIGHTS["composability"]
        floor = baseline * WEIGHT_FLOOR_MULT
        assert new_weights["composability"] >= floor - 0.001


class TestNormalisation:
    def test_weights_sum_to_one(self):
        correlation = _make_correlation(
            delta_override={
                "builder_activity": 15,
                "adoption_quality": -10,
                "discourse_depth": 5,
            },
            samples=60,
        )
        new_weights, _ = compute_updated_weights(
            correlation, dict(DURABILITY_WEIGHTS)
        )
        assert sum(new_weights.values()) == pytest.approx(1.0, abs=0.01)

    def test_zero_deltas_produce_no_changes(self):
        correlation = _make_correlation(delta_override={}, samples=60)
        new_weights, changelog = compute_updated_weights(
            correlation, dict(DURABILITY_WEIGHTS)
        )
        assert not changelog["applied"]
        assert sum(new_weights.values()) == pytest.approx(1.0, abs=0.01)


class TestChangelog:
    def test_changelog_records_changes(self):
        correlation = _make_correlation(
            delta_override={"builder_activity": 20}, samples=60,
        )
        _, changelog = compute_updated_weights(
            correlation, dict(DURABILITY_WEIGHTS)
        )
        assert changelog["applied"]
        assert "builder_activity" in changelog["changes"]
        change = changelog["changes"]["builder_activity"]
        assert "old" in change and "new" in change and "delta" in change
