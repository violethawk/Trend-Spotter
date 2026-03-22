"""Snapshot persistence for Trend Spotter.

This module manages reading and writing snapshot data into a SQLite
database. Snapshots record aggregate metrics for each source (web,
GitHub and Hacker News) and per‑trend signal counts. They are used
primarily for computing acceleration scores between runs.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .signal import RawSignal


class SnapshotStore:
    """Handles storage and retrieval of run snapshots.

    A snapshot is created at the end of each successful run. The
    tables are automatically created on first instantiation.
    """

    def __init__(self, db_path: str = "trend_spotter.db") -> None:
        # Ensure directory exists
        self.db_path = db_path
        need_init = not os.path.exists(db_path)
        self.conn = sqlite3.connect(db_path)
        # Use row factory for convenience
        self.conn.row_factory = sqlite3.Row
        if need_init:
            self._init_db()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              field       TEXT NOT NULL,
              source      TEXT NOT NULL,           -- 'github' | 'hn' | 'web'
              metric      TEXT NOT NULL,           -- 'star_count' | 'post_count' | 'mention_count'
              value       REAL NOT NULL,
              window      TEXT NOT NULL,           -- '1d' | '7d' | '30d'
              captured_at TEXT NOT NULL            -- ISO 8601, UTC
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
              captured_at   TEXT NOT NULL          -- ISO 8601, UTC
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
        """Persist aggregate metrics and per‑trend counts for this run.

        Args:
            field: Field of interest.
            window: Time window token.
            signals: All retrieved raw signals for this run.
            clusters: List of cluster dictionaries.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        # Compute metrics per source
        # Sum stars for GitHub
        star_count = sum(sig.value for sig in signals if sig.source == "github")
        post_count = len([sig for sig in signals if sig.source == "hn"])
        mention_count = len([sig for sig in signals if sig.source == "web"])
        # Insert rows per source
        if star_count or True:
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
        # Insert per-trend signal counts
        for cluster in clusters:
            label = cluster["label"]
            count = len(cluster["signal_ids"])
            cur.execute(
                "INSERT INTO trend_snapshots (field, cluster_label, signal_count, window, captured_at) VALUES (?, ?, ?, ?, ?)",
                (field, label, count, window, now),
            )
        self.conn.commit()

    def get_previous_signal_count(
        self,
        field: str,
        label: str,
        window: str,
        captured_before: str,
    ) -> Optional[int]:
        """Retrieve the most recent signal count for a cluster prior to a given time.

        Args:
            field: Field of interest.
            label: Cluster label.
            window: Time window.
            captured_before: ISO timestamp before which to search.

        Returns:
            The signal count from the most recent snapshot, or ``None`` if no
            snapshot exists.
        """
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