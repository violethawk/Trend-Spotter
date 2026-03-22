"""Tests for the REST API (Phase 8)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from trend_spotter.api.app import create_app
from trend_spotter.persistence.prediction_store import PredictionStore
from trend_spotter.classification import ClassifiedTrend
from trend_spotter.scoring.durability import DurabilityResult


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """Disable API key auth for tests."""
    monkeypatch.delenv("TREND_SPOTTER_API_KEY", raising=False)


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def store_with_data():
    """Seed an in-memory store with test data."""
    store = PredictionStore(db_path=":memory:")
    ct = ClassifiedTrend("AI agents", "Compounding", "rising", "pred-001")
    dr = DurabilityResult(78, {"builder_activity": 80}, 1.0)
    store.write_prediction(ct, dr, 85, "AI", "7d", "2025-06-01T00:00:00", [])
    return store


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        assert "prediction_count" in data


class TestPredictions:
    def test_list_predictions_empty(self, client):
        resp = client.get("/predictions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 0

    def test_prediction_not_found(self, client):
        resp = client.get("/predictions/nonexistent-id")
        assert resp.status_code == 404


class TestAccuracy:
    def test_accuracy_no_data(self, client):
        resp = client.get("/accuracy?horizon=30d")
        assert resp.status_code == 200
        data = resp.json()
        assert "horizon" in data

    def test_accuracy_invalid_horizon(self, client):
        resp = client.get("/accuracy?horizon=999d")
        assert resp.status_code == 422


class TestAuth:
    def test_auth_required_when_key_set(self, client, monkeypatch):
        monkeypatch.setenv("TREND_SPOTTER_API_KEY", "secret-key-123")
        resp = client.get("/predictions")
        assert resp.status_code == 401

    def test_auth_passes_with_correct_key(self, client, monkeypatch):
        monkeypatch.setenv("TREND_SPOTTER_API_KEY", "secret-key-123")
        resp = client.get(
            "/predictions",
            headers={"X-API-Key": "secret-key-123"},
        )
        assert resp.status_code == 200

    def test_auth_fails_with_wrong_key(self, client, monkeypatch):
        monkeypatch.setenv("TREND_SPOTTER_API_KEY", "secret-key-123")
        resp = client.get(
            "/predictions",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401


class TestScans:
    def test_create_scan_validation(self, client):
        # Missing required field
        resp = client.post("/scans", json={})
        assert resp.status_code == 422

    def test_scan_not_found(self, client):
        resp = client.get("/scans/nonexistent-id")
        assert resp.status_code == 404


class TestCrossDomain:
    def test_cross_domain_no_data(self, client, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "test")
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        resp = client.post("/cross-domain", json={
            "fields": ["AI", "fintech"],
            "lookback_days": 30,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "cross_domain_trends" in data
        assert "fields_analyzed" in data
