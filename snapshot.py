"""Snapshot persistence for Trend Spotter.

This module manages reading and writing snapshot data into a SQLite
database. Snapshots record aggregate metrics for each source (web,
GitHub and Hacker News) and per-trend signal counts. They are used
primarily for computing acceleration scores between runs.

The trend_scores table stores raw acceleration and durability scores
per run, enabling trajectory detection across runs.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .signal import RawSignal


class SnapshotStore:
    """Handles storage and retrieval of run snapshots."""

    def __init__(self, db_path: str = "trend_spotter.db") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # Always run init to ensure new tables exist on existing DBs
        self._init_db()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              field       TEXT NOT NULL,
              source      TEXT NOT NULL,
              metric      TEXT NOT NULL,
              value       REAL NOT NULL,
              window      TEXT NOT NULL,
              captured_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_snapshots (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              field         TEXT NOT NULL,
              cluster_label TEXT NOT NULL,
              signal_count  INTEGER NOT NULL,
              window        TEXT NOT NULL,
              captured_at   TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trend_scores (
              id               INTEGER PRIMARY KEY AUTOINCREMENT,
              field            TEXT NOT NULL,
              cluster_label    TEXT NOT NULL,
              acceleration_raw REAL,
              durability_score INTEGER,
              window           TEXT NOT NULL,
              captured_at      TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def write_snapshots(
        self,
        field: str,
        window: str,
        signals: List[RawSignal],
        clusters: List[Dict],
    ) -> None:
        """Persist aggregate metrics and per-trend counts for this run."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        star_count = sum(sig.value for sig in signals if sig.source == "github")
        post_count = len([sig for sig in signals if sig.source == "hn"])
        mention_count = len([sig for sig in signals if sig.source == "web"])
        cur.execute(
            "INSERT INTO snapshots (field, source, metric, value, window, captured_at) VALUES (?, 'github', 'star_count', ?, ?, ?)",
            (field, star_count, window, now),
        )
        cur.execute(
            "INSERT INTO snapshots (field, source, metric, value, window, captured_at) VALUES (?, 'hn', 'post_count', ?, ?, ?)",
            (field, post_count, window, now),
        )
        cur.execute(
            "INSERT INTO snapshots (field, source, metric, value, window, captured_at) VALUES (?, 'web', 'mention_count', ?, ?, ?)",
            (field, mention_count, window, now),
        )
        for cluster in clusters:
            label = cluster["label"]
            count = len(cluster["signal_ids"])
            cur.execute(
                "INSERT INTO trend_snapshots (field, cluster_label, signal_count, window, captured_at) VALUES (?, ?, ?, ?, ?)",
                (field, label, count, window, now),
            )
        self.conn.commit()

    def write_trend_scores(
        self,
        field: str,
        window: str,
        scores: Dict[str, tuple],
    ) -> None:
        """Persist raw acceleration and durability scores for trajectory detection.

        Args:
            scores: Mapping from cluster label to (acceleration_raw, durability_score).
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        for label, (accel_raw, dur_score) in scores.items():
            cur.execute(
                "INSERT INTO trend_scores (field, cluster_label, acceleration_raw, durability_score, window, captured_at) VALUES (?, ?, ?, ?, ?, ?)",
                (field, label, accel_raw, dur_score, window, now),
            )
        self.conn.commit()

    def get_previous_signal_count(
        self,
        field: str,
        label: str,
        window: str,
        captured_before: str,
    ) -> Optional[int]:
        """Retrieve the most recent signal count for a cluster prior to a given time."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT signal_count
            FROM trend_snapshots
            WHERE field = ? AND cluster_label = ? AND window = ? AND captured_at < ?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (field, label, window, captured_before),
        )
        row = cur.fetchone()
        return row["signal_count"] if row else None

    def get_previous_acceleration(
        self,
        field: str,
        label: str,
        window: str,
        captured_before: str,
    ) -> Optional[float]:
        """Retrieve the most recent raw acceleration for trajectory detection."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT acceleration_raw
            FROM trend_scores
            WHERE field = ? AND cluster_label = ? AND window = ? AND captured_at < ?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (field, label, window, captured_before),
        )
        row = cur.fetchone()
        return row["acceleration_raw"] if row else None
