"""Tests for the scoring subpackage (mentions, acceleration, durability)."""

import math

import pytest

from trend_spotter.signal import RawSignal
from trend_spotter.scoring.mentions import compute_mentions_scores, MENTION_WEIGHTS
from trend_spotter.scoring.acceleration import compute_acceleration_scores
from trend_spotter.persistence.snapshot import SnapshotStore


def _sig(source="web", title="test", value=1.0, extras=None):
    return RawSignal(
        source=source, title=title, url="https://example.com",
        snippet="snippet", value=value, extras=extras or {},
    )


# ---- Mentions ----

class TestMentionsScoring:
    def test_empty_clusters(self):
        assert compute_mentions_scores([], []) == {}

    def test_single_cluster_normalises_to_100(self):
        sig = _sig()
        clusters = [{"label": "A", "signal_ids": [sig.id]}]
        result = compute_mentions_scores(clusters, [sig])
        assert result["A"][0] == 100

    def test_source_weights_applied(self):
        gh_sig = _sig(source="github")
        web_sig = _sig(source="web")
        clusters = [
            {"label": "GH", "signal_ids": [gh_sig.id]},
            {"label": "Web", "signal_ids": [web_sig.id]},
        ]
        result = compute_mentions_scores(clusters, [gh_sig, web_sig])
        # GitHub weight 1.5 > web weight 1.0
        assert result["GH"][0] > result["Web"][0]

    def test_multiple_signals_sum(self):
        sigs = [_sig() for _ in range(3)]
        clusters = [{"label": "A", "signal_ids": [s.id for s in sigs]}]
        result = compute_mentions_scores(clusters, sigs)
        assert result["A"][1] == pytest.approx(3.0)  # 3 * web weight 1.0


# ---- Acceleration ----

class TestAccelerationScoring:
    def test_no_baseline_flags_gap(self):
        store = SnapshotStore(db_path=":memory:")
        clusters = [{"label": "A", "signal_ids": ["1", "2", "3"]}]
        gaps: dict = {}
        result = compute_acceleration_scores(
            clusters, "AI", "7d", store, "2099-01-01T00:00:00",
            per_trend_gaps=gaps,
        )
        assert "A" in result
        assert any("no_baseline" in g for g in gaps.get("A", []))

    def test_positive_acceleration(self):
        store = SnapshotStore(db_path=":memory:")
        # Write a baseline with 2 signals
        store.conn.execute(
            "INSERT INTO trend_snapshots (field, cluster_label, signal_count, window, captured_at) VALUES (?, ?, ?, ?, ?)",
            ("AI", "A", 2, "7d", "2025-01-01T00:00:00"),
        )
        store.conn.commit()
        clusters = [{"label": "A", "signal_ids": ["1", "2", "3", "4", "5"]}]
        gaps: dict = {}
        result = compute_acceleration_scores(
            clusters, "AI", "7d", store, "2025-01-02T00:00:00",
            per_trend_gaps=gaps,
        )
        # 5 current vs 2 baseline -> positive log delta
        assert result["A"][1] > 0

    def test_single_cluster_gets_50_when_equal(self):
        store = SnapshotStore(db_path=":memory:")
        store.conn.execute(
            "INSERT INTO trend_snapshots (field, cluster_label, signal_count, window, captured_at) VALUES (?, ?, ?, ?, ?)",
            ("AI", "A", 3, "7d", "2025-01-01T00:00:00"),
        )
        store.conn.commit()
        clusters = [{"label": "A", "signal_ids": ["1", "2", "3"]}]
        gaps: dict = {}
        result = compute_acceleration_scores(
            clusters, "AI", "7d", store, "2025-01-02T00:00:00",
            per_trend_gaps=gaps,
        )
        # Single cluster with no change -> normalised to 50
        assert result["A"][0] == 50
