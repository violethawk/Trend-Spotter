"""Tests for snapshot persistence with canonical key support."""

import pytest

from trend_spotter.persistence.snapshot import SnapshotStore
from trend_spotter.signal import RawSignal


@pytest.fixture
def store():
    return SnapshotStore(db_path=":memory:")


class TestSnapshotWriteRead:
    def test_write_and_read_signal_count(self, store):
        sigs = [RawSignal(source="web", title="t", url="u", value=1)]
        clusters = [{"label": "AI agents", "signal_ids": [sigs[0].id],
                     "canonical_key": "agent_ai"}]
        store.write_snapshots("AI", "7d", sigs, clusters)
        count = store.get_previous_signal_count(
            "AI", "AI agents", "7d", "2099-01-01"
        )
        assert count == 1

    def test_canonical_key_matches_variant_label(self, store):
        """Labels 'AI agents' and 'AI Agent Framework' should share baselines."""
        sigs = [RawSignal(source="web", title="t", url="u", value=1)]
        clusters = [{"label": "AI agents", "signal_ids": [sigs[0].id],
                     "canonical_key": "agent_ai"}]
        store.write_snapshots("AI", "7d", sigs, clusters)

        # Look up with a different label that canonicalizes the same way
        count = store.get_previous_signal_count(
            "AI", "ai agent", "7d", "2099-01-01"
        )
        assert count == 1

    def test_write_and_read_trend_scores(self, store):
        store.write_trend_scores("AI", "7d", {"trend1": (0.5, 70)})
        accel = store.get_previous_acceleration(
            "AI", "trend1", "7d", "2099-01-01"
        )
        assert accel == pytest.approx(0.5)

    def test_no_baseline_returns_none(self, store):
        assert store.get_previous_signal_count("X", "Y", "7d", "2099-01-01") is None
        assert store.get_previous_acceleration("X", "Y", "7d", "2099-01-01") is None


class TestSnapshotAggregateMetrics:
    def test_writes_per_source_metrics(self, store):
        sigs = [
            RawSignal(source="github", title="repo", url="u1", value=500),
            RawSignal(source="github", title="repo2", url="u2", value=200),
            RawSignal(source="hn", title="post", url="u3", value=50),
            RawSignal(source="web", title="article", url="u4", value=1),
        ]
        clusters = [{"label": "T", "signal_ids": [s.id for s in sigs],
                     "canonical_key": "t"}]
        store.write_snapshots("AI", "7d", sigs, clusters)
        cur = store.conn.cursor()
        cur.execute("SELECT metric, value FROM snapshots WHERE field='AI'")
        rows = {r["metric"]: r["value"] for r in cur.fetchall()}
        assert rows["star_count"] == 700  # 500 + 200
        assert rows["post_count"] == 1
        assert rows["mention_count"] == 1
