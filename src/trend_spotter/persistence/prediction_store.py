"""Prediction storage for Trend Spotter (Phase 4/5).

This module persists classified trend predictions to SQLite so they
can be evaluated at 30d and 90d intervals. The prediction store is
active from Phase 4 launch so predictions accumulate from day one.

Phase 5 adds methods to find matured predictions, write evaluation
results, and query evaluated predictions for accuracy reporting.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..classification import ClassifiedTrend
from ..config import DEFAULT_DB_PATH
from ..scoring.durability import DurabilityResult


class PredictionStore:
    """SQLite-backed store for trend predictions."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
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
                evaluation_30d_reasoning TEXT,
                evaluated_at_90d         TEXT,
                evaluation_90d           TEXT,
                evaluation_90d_reasoning TEXT,
                original_signal_count    INTEGER
            );
            """
        )
        # Add columns if they don't exist (migration for existing DBs)
        for col, col_type in [
            ("evaluation_30d_reasoning", "TEXT"),
            ("evaluation_90d_reasoning", "TEXT"),
            ("original_signal_count", "INTEGER"),
        ]:
            try:
                cur.execute(f"ALTER TABLE predictions ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Phase 6: Weight versioning
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS weight_versions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                version             INTEGER NOT NULL,
                weights_json        TEXT NOT NULL,
                correlation_json    TEXT NOT NULL,
                sample_size         INTEGER NOT NULL,
                accuracy_before_json TEXT,
                accuracy_after_json  TEXT,
                created_at          TEXT NOT NULL
            );
            """
        )

        # Evaluation run tracking
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluation_runs (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                horizon               TEXT NOT NULL,
                run_at                TEXT NOT NULL,
                predictions_evaluated INTEGER NOT NULL,
                correct               INTEGER NOT NULL DEFAULT 0,
                incorrect             INTEGER NOT NULL DEFAULT 0,
                ambiguous             INTEGER NOT NULL DEFAULT 0,
                errors                INTEGER NOT NULL DEFAULT 0,
                warnings_json         TEXT
            );
            """
        )

        # Phase 7: Cross-domain trends
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cross_domain_trends (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_label         TEXT NOT NULL,
                description        TEXT,
                domain_count       INTEGER NOT NULL,
                matches_json       TEXT NOT NULL,
                confidence         REAL NOT NULL,
                convergence_window TEXT,
                detected_at        TEXT NOT NULL
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
        original_signal_count: Optional[int] = None,
    ) -> None:
        """Write a classified trend as a prediction record."""
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
                evidence_json, created_at, original_signal_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                original_signal_count,
            ),
        )
        self.conn.commit()

    def get_matured_predictions(self, horizon: str) -> List[Dict[str, Any]]:
        """Find predictions that have matured and are ready for evaluation.

        A prediction is matured at 30d if:
        - window_end is >= 30 days ago AND evaluated_at_30d is NULL

        A prediction is matured at 90d if:
        - window_end is >= 90 days ago AND evaluated_at_90d is NULL

        Args:
            horizon: "30d" or "90d".

        Returns:
            List of prediction rows as dicts.
        """
        now = datetime.now(timezone.utc)
        if horizon == "30d":
            cutoff = (now - timedelta(days=30)).isoformat()
            query = """
                SELECT * FROM predictions
                WHERE window_end <= ? AND evaluated_at_30d IS NULL
                ORDER BY window_end ASC
            """
        elif horizon == "90d":
            cutoff = (now - timedelta(days=90)).isoformat()
            query = """
                SELECT * FROM predictions
                WHERE window_end <= ? AND evaluated_at_90d IS NULL
                ORDER BY window_end ASC
            """
        else:
            return []

        cur = self.conn.cursor()
        cur.execute(query, (cutoff,))
        return [dict(row) for row in cur.fetchall()]

    def write_evaluation(
        self,
        prediction_id: str,
        horizon: str,
        outcome: str,
        reasoning: str,
    ) -> None:
        """Write evaluation result for a prediction.

        Args:
            prediction_id: UUID of the prediction.
            horizon: "30d" or "90d".
            outcome: "correct", "incorrect", or "ambiguous".
            reasoning: Explanation of the evaluation.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()

        if horizon == "30d":
            cur.execute(
                """
                UPDATE predictions
                SET evaluated_at_30d = ?, evaluation_30d = ?, evaluation_30d_reasoning = ?
                WHERE prediction_id = ?
                """,
                (now, outcome, reasoning, prediction_id),
            )
        elif horizon == "90d":
            cur.execute(
                """
                UPDATE predictions
                SET evaluated_at_90d = ?, evaluation_90d = ?, evaluation_90d_reasoning = ?
                WHERE prediction_id = ?
                """,
                (now, outcome, reasoning, prediction_id),
            )
        self.conn.commit()

    def get_evaluated_predictions(self, horizon: str) -> List[Dict[str, Any]]:
        """Get all predictions that have been evaluated at the given horizon.

        Args:
            horizon: "30d" or "90d".

        Returns:
            List of prediction rows as dicts.
        """
        cur = self.conn.cursor()
        if horizon == "30d":
            cur.execute(
                "SELECT * FROM predictions WHERE evaluation_30d IS NOT NULL"
            )
        elif horizon == "90d":
            cur.execute(
                "SELECT * FROM predictions WHERE evaluation_90d IS NOT NULL"
            )
        else:
            return []
        return [dict(row) for row in cur.fetchall()]

    def get_all_predictions(self) -> List[Dict[str, Any]]:
        """Get all predictions, evaluated or not."""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM predictions ORDER BY created_at DESC")
        return [dict(row) for row in cur.fetchall()]

    def get_prediction_count(self) -> int:
        """Get total number of stored predictions."""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM predictions")
        row = cur.fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Phase 6: Weight versioning
    # ------------------------------------------------------------------

    def get_current_weights(self) -> Optional[Dict[str, float]]:
        """Return the latest tuned weights, or None (use defaults)."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT weights_json FROM weight_versions ORDER BY version DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row["weights_json"])

    def get_current_weight_version(self) -> Optional[int]:
        """Return the latest weight version number, or None."""
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(version) as v FROM weight_versions")
        row = cur.fetchone()
        return row["v"] if row and row["v"] is not None else None

    def write_weight_version(
        self,
        weights: Dict[str, float],
        correlation: Dict[str, Any],
        sample_size: int,
        accuracy_before: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Insert a new weight version. Returns the version number."""
        cur_version = self.get_current_weight_version()
        new_version = (cur_version or 0) + 1
        now = datetime.now(timezone.utc).isoformat()

        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO weight_versions
                (version, weights_json, correlation_json, sample_size,
                 accuracy_before_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                new_version,
                json.dumps(weights),
                json.dumps(correlation),
                sample_size,
                json.dumps(accuracy_before) if accuracy_before else None,
                now,
            ),
        )
        self.conn.commit()
        return new_version

    def update_accuracy_after(
        self, version: int, accuracy_after: Dict[str, Any]
    ) -> None:
        """Record post-tuning accuracy for a weight version."""
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE weight_versions SET accuracy_after_json = ? WHERE version = ?",
            (json.dumps(accuracy_after), version),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Evaluation run tracking
    # ------------------------------------------------------------------

    def write_evaluation_run(
        self,
        horizon: str,
        predictions_evaluated: int,
        correct: int,
        incorrect: int,
        ambiguous: int,
        errors: int,
        warnings: Optional[List[str]] = None,
    ) -> None:
        """Record metadata for an automated evaluation run."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO evaluation_runs
                (horizon, run_at, predictions_evaluated,
                 correct, incorrect, ambiguous, errors, warnings_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                horizon,
                now,
                predictions_evaluated,
                correct,
                incorrect,
                ambiguous,
                errors,
                json.dumps(warnings) if warnings else None,
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Phase 7: Cross-domain trends
    # ------------------------------------------------------------------

    def get_recent_predictions(
        self, lookback_days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get predictions created within the lookback window."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM predictions WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        )
        return [dict(row) for row in cur.fetchall()]

    def write_cross_domain_trend(self, trend: Any) -> None:
        """Persist a detected cross-domain trend."""
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO cross_domain_trends
                (meta_label, description, domain_count, matches_json,
                 confidence, convergence_window, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trend.meta_label,
                trend.description,
                trend.domain_count,
                json.dumps([
                    {
                        "field": m.field,
                        "trend_label": m.trend_label,
                        "prediction_id": m.prediction_id,
                        "acceleration_score": m.acceleration_score,
                        "durability_score": m.durability_score,
                        "classification": m.classification,
                    }
                    for m in trend.domains
                ]),
                trend.confidence,
                trend.convergence_window,
                trend.detected_at,
            ),
        )
        self.conn.commit()

    def get_cross_domain_trends(
        self, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get stored cross-domain trends."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM cross_domain_trends ORDER BY detected_at DESC LIMIT ?",
            (limit,),
        )
        rows = []
        for row in cur.fetchall():
            d = dict(row)
            d["matches"] = json.loads(d.pop("matches_json"))
            rows.append(d)
        return rows
