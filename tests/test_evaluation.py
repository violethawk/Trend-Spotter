"""Tests for evaluation correctness criteria and threshold checks."""

import pytest

from trend_spotter.evaluation.evaluator import (
    _apply_correctness_criteria,
    check_thresholds,
    compute_accuracy_metrics,
    PRESENCE_THRESHOLD,
    GROWTH_THRESHOLD,
    DECAY_THRESHOLD,
)


class TestCorrectnessCompounding:
    def test_30d_correct_when_growing(self):
        outcome, _ = _apply_correctness_criteria(
            "Compounding", "30d",
            presence_count=5, growth_delta=0.2,
            signals_with_growth=3, original_accel=80, original_dur=80,
        )
        assert outcome == "correct"

    def test_30d_incorrect_when_absent(self):
        outcome, _ = _apply_correctness_criteria(
            "Compounding", "30d",
            presence_count=1, growth_delta=-0.5,
            signals_with_growth=0, original_accel=80, original_dur=80,
        )
        assert outcome == "incorrect"

    def test_30d_ambiguous_when_present_but_marginal(self):
        outcome, _ = _apply_correctness_criteria(
            "Compounding", "30d",
            presence_count=4, growth_delta=0.01,
            signals_with_growth=1, original_accel=80, original_dur=80,
        )
        assert outcome == "ambiguous"


class TestCorrectnessDurableSlow:
    def test_30d_correct_when_stable(self):
        outcome, _ = _apply_correctness_criteria(
            "Durable/Slow", "30d",
            presence_count=5, growth_delta=0.02,
            signals_with_growth=2, original_accel=40, original_dur=80,
        )
        assert outcome == "correct"

    def test_90d_correct_when_present(self):
        outcome, _ = _apply_correctness_criteria(
            "Durable/Slow", "90d",
            presence_count=4, growth_delta=0.0,
            signals_with_growth=1, original_accel=40, original_dur=80,
        )
        assert outcome == "correct"


class TestCorrectnessFlashTrend:
    def test_30d_correct_when_decayed(self):
        outcome, _ = _apply_correctness_criteria(
            "Flash Trend", "30d",
            presence_count=0, growth_delta=-0.5,
            signals_with_growth=0, original_accel=80, original_dur=40,
        )
        assert outcome == "correct"

    def test_30d_incorrect_when_still_growing(self):
        outcome, _ = _apply_correctness_criteria(
            "Flash Trend", "30d",
            presence_count=5, growth_delta=0.3,
            signals_with_growth=3, original_accel=80, original_dur=40,
        )
        assert outcome == "incorrect"


class TestCorrectnessIgnore:
    def test_correct_when_absent(self):
        outcome, _ = _apply_correctness_criteria(
            "Ignore", "30d",
            presence_count=1, growth_delta=-0.1,
            signals_with_growth=0, original_accel=30, original_dur=30,
        )
        assert outcome == "correct"

    def test_incorrect_when_present(self):
        outcome, _ = _apply_correctness_criteria(
            "Ignore", "90d",
            presence_count=5, growth_delta=0.2,
            signals_with_growth=3, original_accel=30, original_dur=30,
        )
        assert outcome == "incorrect"


class TestCheckThresholds:
    def _metrics(self, accuracy, flash_incorrect=0, flash_total=0, ambiguous_rate=0):
        by_class = {}
        if flash_total > 0:
            by_class["Flash Trend"] = {
                "correct": flash_total - flash_incorrect,
                "incorrect": flash_incorrect,
                "ambiguous": 0,
                "total": flash_total,
            }
        if ambiguous_rate > 0:
            total = 10
            by_class["Compounding"] = {
                "correct": total - int(total * ambiguous_rate / 100),
                "incorrect": 0,
                "ambiguous": int(total * ambiguous_rate / 100),
                "total": total,
            }
        return {
            "overall": {"accuracy_pct": accuracy, "total": 20},
            "by_classification": by_class,
        }

    def test_no_warnings_when_above_targets(self):
        warnings = check_thresholds(self._metrics(70), "30d")
        assert warnings == []

    def test_warns_below_30d_target(self):
        warnings = check_thresholds(self._metrics(55), "30d")
        assert any("below target" in w for w in warnings)

    def test_warns_below_90d_target(self):
        warnings = check_thresholds(self._metrics(60), "90d")
        assert any("below target" in w for w in warnings)

    def test_warns_flash_trend_fp_rate(self):
        warnings = check_thresholds(
            self._metrics(70, flash_incorrect=5, flash_total=10), "30d"
        )
        assert any("Flash Trend" in w for w in warnings)

    def test_warns_high_ambiguous_rate(self):
        warnings = check_thresholds(
            self._metrics(70, ambiguous_rate=30), "30d"
        )
        assert any("ambiguous" in w for w in warnings)


class TestAccuracyMetrics:
    def test_basic_counts(self):
        preds = [
            {"classification": "Compounding", "evaluation_30d": "correct"},
            {"classification": "Compounding", "evaluation_30d": "incorrect"},
            {"classification": "Flash Trend", "evaluation_30d": "correct"},
        ]
        metrics = compute_accuracy_metrics(preds, "30d")
        assert metrics["overall"]["total"] == 3
        assert metrics["overall"]["correct"] == 2
        assert metrics["overall"]["accuracy_pct"] == pytest.approx(66.7, abs=0.1)

    def test_empty_evaluated(self):
        metrics = compute_accuracy_metrics([], "30d")
        assert metrics["overall"]["total"] == 0
        assert metrics["overall"]["accuracy_pct"] is None
