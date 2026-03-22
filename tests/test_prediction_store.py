"""Tests for PredictionStore (CRUD, weight versioning, evaluation runs)."""

import json

import pytest

from trend_spotter.persistence.prediction_store import PredictionStore
from trend_spotter.classification import ClassifiedTrend
from trend_spotter.scoring.durability import DurabilityResult


@pytest.fixture
def store():
    return PredictionStore(db_path=":memory:")


def _classified(label="AI agents", classification="Compounding"):
    return ClassifiedTrend(
        label=label,
        classification=classification,
        trajectory="rising",
        prediction_id="test-pred-001",
    )


def _durability_result():
    return DurabilityResult(
        score=75,
        signals={
            "builder_activity": 80,
            "adoption_quality": 70,
            "discourse_depth": 60,
            "cross_platform_presence": 50,
            "problem_anchoring": 40,
            "composability": 30,
        },
        sentiment_multiplier=1.0,
    )


class TestPredictionCRUD:
    def test_write_and_read(self, store):
        store.write_prediction(
            _classified(), _durability_result(), 85,
            "AI", "7d", "2025-01-01T00:00:00",
            [{"url": "https://example.com", "source": "web"}],
            original_signal_count=5,
        )
        preds = store.get_all_predictions()
        assert len(preds) == 1
        assert preds[0]["prediction_id"] == "test-pred-001"
        assert preds[0]["original_signal_count"] == 5

    def test_count(self, store):
        assert store.get_prediction_count() == 0
        store.write_prediction(
            _classified(), _durability_result(), 85,
            "AI", "7d", "2025-01-01T00:00:00", [],
        )
        assert store.get_prediction_count() == 1

    def test_original_signal_count_defaults_none(self, store):
        store.write_prediction(
            _classified(), _durability_result(), 85,
            "AI", "7d", "2025-01-01T00:00:00", [],
        )
        pred = store.get_all_predictions()[0]
        assert pred["original_signal_count"] is None


class TestEvaluation:
    def test_write_and_get_evaluation(self, store):
        store.write_prediction(
            _classified(), _durability_result(), 85,
            "AI", "7d", "2024-01-01T00:00:00", [],
        )
        store.write_evaluation("test-pred-001", "30d", "correct", "Growth sustained")
        evaluated = store.get_evaluated_predictions("30d")
        assert len(evaluated) == 1
        assert evaluated[0]["evaluation_30d"] == "correct"
        assert evaluated[0]["evaluation_30d_reasoning"] == "Growth sustained"

    def test_matured_returns_only_unevaluated(self, store):
        store.write_prediction(
            _classified(), _durability_result(), 85,
            "AI", "7d", "2024-01-01T00:00:00", [],
        )
        # Should be matured (window_end is old enough)
        matured = store.get_matured_predictions("30d")
        assert len(matured) == 1

        # Evaluate it
        store.write_evaluation("test-pred-001", "30d", "correct", "OK")

        # Should no longer be matured
        matured = store.get_matured_predictions("30d")
        assert len(matured) == 0


class TestWeightVersioning:
    def test_no_weights_returns_none(self, store):
        assert store.get_current_weights() is None
        assert store.get_current_weight_version() is None

    def test_write_and_read_weights(self, store):
        weights = {"builder_activity": 0.25, "adoption_quality": 0.25,
                    "discourse_depth": 0.125, "cross_platform_presence": 0.125,
                    "problem_anchoring": 0.125, "composability": 0.125}
        correlation = {"builder_activity": {"delta": 5.0}}
        version = store.write_weight_version(weights, correlation, 60)
        assert version == 1
        assert store.get_current_weights() == weights

    def test_versioning_increments(self, store):
        w = {"a": 1.0}
        v1 = store.write_weight_version(w, {}, 50)
        v2 = store.write_weight_version({"a": 0.5}, {}, 60)
        assert v2 == v1 + 1
        # Latest wins
        assert store.get_current_weights() == {"a": 0.5}

    def test_accuracy_after_update(self, store):
        store.write_weight_version({"a": 1.0}, {}, 50)
        store.update_accuracy_after(1, {"accuracy_pct": 72.5})
        cur = store.conn.cursor()
        cur.execute("SELECT accuracy_after_json FROM weight_versions WHERE version = 1")
        row = cur.fetchone()
        assert json.loads(row["accuracy_after_json"])["accuracy_pct"] == 72.5


class TestEvaluationRuns:
    def test_write_evaluation_run(self, store):
        store.write_evaluation_run("30d", 10, 6, 3, 1, 0, ["low accuracy"])
        cur = store.conn.cursor()
        cur.execute("SELECT * FROM evaluation_runs")
        rows = cur.fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["predictions_evaluated"] == 10
        assert row["correct"] == 6
        assert json.loads(row["warnings_json"]) == ["low accuracy"]
