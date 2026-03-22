"""Tests for CLI entry points."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from trend_spotter.cli import run, run_evaluate, run_accuracy, run_predictions, run_cross_domain


class TestRunScan:
    @patch("trend_spotter.cli.run_pipeline")
    @patch("trend_spotter.cli.load_config")
    def test_prints_pipeline_output(self, mock_config, mock_pipeline, capsys):
        mock_config.return_value = MagicMock()
        mock_pipeline.return_value = {"field": "AI", "trends": []}
        run("AI", "7d")
        output = json.loads(capsys.readouterr().out)
        assert output["field"] == "AI"

    @patch("trend_spotter.cli.load_config")
    def test_exits_on_config_error(self, mock_config):
        mock_config.side_effect = RuntimeError("Missing key")
        with pytest.raises(SystemExit):
            run("AI", "7d")


class TestRunEvaluate:
    @patch("trend_spotter.cli.PredictionStore")
    @patch("trend_spotter.cli.load_config")
    def test_no_matured(self, mock_config, mock_store_cls, capsys):
        mock_config.return_value = MagicMock()
        store = MagicMock()
        store.get_matured_predictions.return_value = []
        mock_store_cls.return_value = store
        run_evaluate("30d")
        output = json.loads(capsys.readouterr().out)
        assert output["evaluated"] == 0

    @patch("trend_spotter.cli.evaluate_prediction")
    @patch("trend_spotter.cli.PredictionStore")
    @patch("trend_spotter.cli.load_config")
    def test_evaluates_and_prints(self, mock_config, mock_store_cls, mock_eval, capsys):
        mock_config.return_value = MagicMock()
        store = MagicMock()
        store.get_matured_predictions.return_value = [
            {"prediction_id": "p1", "trend_label": "T", "field": "AI",
             "classification": "Compounding", "window": "7d"},
        ]
        mock_store_cls.return_value = store
        mock_eval.return_value = MagicMock(
            prediction_id="p1", horizon="30d", outcome="correct",
            reasoning="OK", current_signal_count=5, original_signal_count=3,
            growth_delta=0.2, signals_with_growth=2,
        )
        run_evaluate("30d")
        output = json.loads(capsys.readouterr().out)
        assert output["evaluated"] == 1
        assert output["results"][0]["outcome"] == "correct"


class TestRunAccuracy:
    @patch("trend_spotter.cli.PredictionStore")
    def test_no_evaluated(self, mock_store_cls, capsys):
        store = MagicMock()
        store.get_evaluated_predictions.return_value = []
        store.get_prediction_count.return_value = 0
        mock_store_cls.return_value = store
        run_accuracy("30d")
        output = json.loads(capsys.readouterr().out)
        assert "No evaluated" in output["message"]

    @patch("trend_spotter.cli.PredictionStore")
    def test_with_evaluated(self, mock_store_cls, capsys):
        store = MagicMock()
        store.get_evaluated_predictions.return_value = [
            {"classification": "Compounding", "evaluation_30d": "correct"},
        ]
        store.get_all_predictions.return_value = [
            {"evaluation_30d": "correct"},
        ]
        mock_store_cls.return_value = store
        run_accuracy("30d")
        output = json.loads(capsys.readouterr().out)
        assert output["overall"]["correct"] == 1


class TestRunPredictions:
    @patch("trend_spotter.cli.PredictionStore")
    def test_no_predictions(self, mock_store_cls, capsys):
        store = MagicMock()
        store.get_all_predictions.return_value = []
        mock_store_cls.return_value = store
        run_predictions()
        output = json.loads(capsys.readouterr().out)
        assert output["count"] == 0

    @patch("trend_spotter.cli.PredictionStore")
    def test_with_predictions(self, mock_store_cls, capsys):
        store = MagicMock()
        store.get_all_predictions.return_value = [{
            "prediction_id": "p1", "field": "AI", "trend_label": "T",
            "classification": "Compounding", "trajectory": "rising",
            "acceleration_score": 80, "durability_score": 70,
            "created_at": "2025-01-01", "evaluation_30d": None,
            "evaluation_90d": None, "evaluation_30d_reasoning": None,
            "evaluation_90d_reasoning": None,
        }]
        mock_store_cls.return_value = store
        run_predictions()
        output = json.loads(capsys.readouterr().out)
        assert output["count"] == 1


class TestRunCrossDomain:
    @patch("trend_spotter.cli.detect_cross_domain_trends")
    @patch("trend_spotter.cli.PredictionStore")
    @patch("trend_spotter.cli.load_config")
    def test_no_results(self, mock_config, mock_store_cls, mock_detect, capsys):
        mock_config.return_value = MagicMock()
        store = MagicMock()
        store.get_recent_predictions.return_value = []
        mock_store_cls.return_value = store
        mock_detect.return_value = []
        run_cross_domain(["AI", "fintech"], 30, False)
        output = json.loads(capsys.readouterr().out)
        assert output["cross_domain_trends"] == []
