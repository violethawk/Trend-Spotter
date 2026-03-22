"""Prediction storage for Trend Spotter (Phase 4/5).

This module persists classified trend predictions to SQLite so they
can be evaluated at 30d and 90d intervals (Phase 5). The prediction
store is active from Phase 4 launch so predictions accumulate from
day one.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .classification import ClassifiedTrend
from .durability import DurabilityResult


class PredictionStore:
    """SQLite-backed store for trend predictions."""

    def __init__(self, db_path: str = "trend_spotter.db") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                prediction_id            TEXT PRIMARY KEY,
                field                    TEXT NOT NULL,
                window                   TEXT NOT NULL,
                window_end               TEXT NOT NULL,
                trend_label              TEXT NOT NULL,
                acceleration_score       INTEGER NOT NULL,
                durability_score         INTEGER NOT NULL,
                builder_activity         INTEGER,
                adoption_quality         INTEGER,
                discourse_depth          INTEGER,
                cross_platform_presence  INTEGER,
                problem_anchoring        INTEGER,
                composability            INTEGER,
                sentiment_penalty        REAL DEFAULT 1.0,
                classification           TEXT NOT NULL,
                trajectory               TEXT NOT NULL,
                evidence_json            TEXT NOT NULL,
                created_at               TEXT NOT NULL,
                evaluated_at_30d         TEXT,
                evaluation_30d           TEXT,
                evaluated_at_90d         TEXT,
                evaluation_90d           TEXT
            );
            """
        )
        self.conn.commit()

    def write_prediction(
        self,
        classified: ClassifiedTrend,
        durability_result: Optional[DurabilityResult],
        acceleration_score: int,
        field: str,
        window: str,
        run_start_iso: str,
        evidence: List[Dict[str, Any]],
    ) -> None:
        """Write a classified trend as a prediction record.

        Args:
            classified: The classified trend object.
            durability_result: Durability scoring result (may be None).
            acceleration_score: Normalised acceleration score (0-100).
            field: Field of interest.
            window: Time window.
            run_start_iso: ISO timestamp of the run.
            evidence: List of source/evidence dicts for this trend.
        """
        dur_signals = durability_result.signals if durability_result else {}
        dur_score = durability_result.score if durability_result else 0
        sentiment = durability_result.sentiment_multiplier if durability_result else 1.0

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO predictions (
                prediction_id, field, window, window_end, trend_label,
                acceleration_score, durability_score,
                builder_activity, adoption_quality, discourse_depth,
                cross_platform_presence, problem_anchoring, composability,
                sentiment_penalty, classification, trajectory,
                evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                classified.prediction_id,
                field,
                window,
                run_start_iso,
                classified.label,
                acceleration_score,
                dur_score,
                dur_signals.get("builder_activity"),
                dur_signals.get("adoption_quality"),
                dur_signals.get("discourse_depth"),
                dur_signals.get("cross_platform_presence"),
                dur_signals.get("problem_anchoring"),
                dur_signals.get("composability"),
                sentiment,
                classified.classification,
                classified.trajectory,
                json.dumps(evidence),
                run_start_iso,
            ),
        )
        self.conn.commit()
