"""Tests for the automated scheduler."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from trend_spotter.evaluation.scheduler import run_schedule, _evaluate_horizon, _try_weight_tuning


class TestEvaluateHorizon:
    def test_no_matured(self):
        store = MagicMock()
        store.get_matured_predictions.return_value = []
        config = MagicMock()
        result = _evaluate_horizon(store, config, "30d")
        assert result["evaluated"] == 0

    @patch("trend_spotter.evaluation.scheduler.evaluate_prediction")
    def test_evaluates_matured(self, mock_eval):
        store = MagicMock()
        store.get_matured_predictions.return_value = [
            {"prediction_id": "p1", "trend_label": "AI agents", "classification": "Compounding"},
        ]
        mock_eval.return_value = MagicMock(
            prediction_id="p1", horizon="30d", outcome="correct",
            reasoning="Growth sustained",
        )
        config = MagicMock()
        result = _evaluate_horizon(store, config, "30d")
        assert result["evaluated"] == 1
        assert result["correct"] == 1
        store.write_evaluation.assert_called_once()

    @patch("trend_spotter.evaluation.scheduler.evaluate_prediction")
    def test_handles_evaluation_error(self, mock_eval):
        store = MagicMock()
        store.get_matured_predictions.return_value = [
            {"prediction_id": "p1", "trend_label": "X", "classification": "Ignore"},
        ]
        mock_eval.side_effect = Exception("API down")
        config = MagicMock()
        result = _evaluate_horizon(store, config, "30d")
        assert result["errors"] == 1


class TestTryWeightTuning:
    @patch("trend_spotter.evaluation.scheduler.compute_updated_weights")
    @patch("trend_spotter.evaluation.scheduler.compute_signal_correlation")
    @patch("trend_spotter.evaluation.scheduler.compute_accuracy_metrics")
    def test_applies_when_weights_change(self, mock_acc, mock_corr, mock_update):
        store = MagicMock()
        store.get_current_weights.return_value = None

        mock_corr.return_value = {"builder_activity": {"delta": 10}}
        mock_acc.return_value = {"overall": {"accuracy_pct": 65}}
        mock_update.return_value = (
            {"builder_activity": 0.25},
            {"applied": True, "changes": {"builder_activity": {"old": 0.2, "new": 0.25}}, "reason": "Updated"},
        )
        store.write_weight_version.return_value = 1

        evaluated = [{"evaluation_90d": "correct"} for _ in range(30)] + \
                    [{"evaluation_90d": "incorrect"} for _ in range(20)]
        non_ambiguous = evaluated
        result = _try_weight_tuning(store, evaluated, non_ambiguous)
        assert result["status"] == "applied"
        assert result["version"] == 1

    @patch("trend_spotter.evaluation.scheduler.compute_updated_weights")
    @patch("trend_spotter.evaluation.scheduler.compute_signal_correlation")
    @patch("trend_spotter.evaluation.scheduler.compute_accuracy_metrics")
    def test_skips_when_no_changes(self, mock_acc, mock_corr, mock_update):
        store = MagicMock()
        store.get_current_weights.return_value = None
        mock_corr.return_value = {}
        mock_acc.return_value = {}
        mock_update.return_value = (
            {"builder_activity": 0.2},
            {"applied": False, "reason": "No changes"},
        )
        result = _try_weight_tuning(store, [], [])
        assert result["status"] == "skipped"


class TestRunSchedule:
    @patch("trend_spotter.evaluation.scheduler.load_config")
    def test_returns_error_when_config_fails(self, mock_config):
        mock_config.side_effect = RuntimeError("Missing OPENAI_API_KEY")
        result = run_schedule()
        assert "error" in result

    @patch("trend_spotter.evaluation.scheduler.PredictionStore")
    @patch("trend_spotter.evaluation.scheduler.load_config")
    def test_runs_both_horizons(self, mock_config, mock_store_cls):
        mock_config.return_value = MagicMock()
        store = MagicMock()
        store.get_matured_predictions.return_value = []
        store.get_evaluated_predictions.return_value = []
        mock_store_cls.return_value = store
        result = run_schedule()
        assert "30d" in result["evaluations"]
        assert "90d" in result["evaluations"]
