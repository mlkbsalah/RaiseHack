"""SQLite memory for preferences, incidents, and feedback."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from time import time
from typing import Any

from .models import TapEvent


DEFAULT_PREFERENCES: dict[str, Any] = {
    "ask_after_seconds": 30,
    "alert_after_seconds": 90,
    "minimum_flow_rate_lpm": 0.3,
    "ignore_when_person_using_sink": True,
    "automatic_shutoff_allowed": False,
}


class TapMemory:
    def __init__(self, db_path: str = "tap_agent.sqlite3") -> None:
        self.db_path = db_path
        parent = Path(db_path).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    event_id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    duration_seconds REAL NOT NULL,
                    action TEXT,
                    event_json TEXT NOT NULL,
                    user_feedback TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
        self.set_default_preferences()

    def set_default_preferences(self) -> None:
        with self._connect() as connection:
            for key, value in DEFAULT_PREFERENCES.items():
                connection.execute(
                    "INSERT OR IGNORE INTO preferences (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )

    def get_preferences(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM preferences").fetchall()
        preferences = DEFAULT_PREFERENCES.copy()
        preferences.update({row["key"]: json.loads(row["value"]) for row in rows})
        preferences["automatic_shutoff_allowed"] = False
        return preferences

    def save_incident(self, event: TapEvent, user_feedback: str | None = None) -> None:
        recommendation = event.recommendation
        event_json = {
            "event_id": event.event_id,
            "started_at": event.started_at,
            "last_seen_at": event.last_seen_at,
            "duration_seconds": event.duration_seconds,
            "consecutive_positive_ticks": event.consecutive_positive_ticks,
            "flow_rate_lpm": event.flow_rate_lpm,
            "audio": event.audio.as_dict() if event.audio else None,
            "vision": event.vision.as_dict() if event.vision else None,
            "recommendation": recommendation.as_dict() if recommendation else None,
            "evidence_log": event.evidence_log,
        }
        now = time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    event_id, started_at, ended_at, duration_seconds, action,
                    event_json, user_feedback, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    ended_at=excluded.ended_at,
                    duration_seconds=excluded.duration_seconds,
                    action=excluded.action,
                    event_json=excluded.event_json,
                    user_feedback=COALESCE(excluded.user_feedback, incidents.user_feedback),
                    updated_at=excluded.updated_at
                """,
                (
                    event.event_id,
                    event.started_at,
                    event.last_seen_at,
                    event.duration_seconds,
                    recommendation.recommended_action if recommendation else None,
                    json.dumps(event_json, sort_keys=True),
                    user_feedback,
                    now,
                    now,
                ),
            )

    def get_recent_feedback_events(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, duration_seconds, action, event_json, user_feedback, updated_at
                FROM incidents
                WHERE user_feedback IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "duration_seconds": row["duration_seconds"],
                "action": row["action"],
                "event": json.loads(row["event_json"]),
                "user_feedback": row["user_feedback"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def record_feedback(self, event_id: str, feedback: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE incidents
                SET user_feedback = ?, updated_at = ?
                WHERE event_id = ?
                """,
                (feedback, time(), event_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No saved incident found for event_id={event_id!r}")

