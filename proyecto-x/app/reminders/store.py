from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class ReminderStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    recurrence TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_triggered_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_user_time
                ON reminders(user_id, status, remind_at)
                """
            )

    def create(
        self,
        *,
        user_id: str,
        message: str,
        remind_at: datetime,
        timezone: str,
        recurrence: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        item_id = str(uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reminders
                (id, user_id, message, remind_at, timezone, status, recurrence,
                 created_at, updated_at, last_triggered_at)
                VALUES (?, ?, ?, ?, ?, 'SCHEDULED', ?, ?, ?, NULL)
                """,
                (
                    item_id,
                    user_id,
                    message,
                    remind_at.isoformat(),
                    timezone,
                    recurrence,
                    now,
                    now,
                ),
            )
        return self.get(item_id, user_id=user_id) or {}

    def get(self, reminder_id: str, *, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def list(self, *, user_id: str, include_cancelled: bool = False) -> list[dict[str, Any]]:
        clause = "" if include_cancelled else "AND status != 'CANCELLED'"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM reminders
                WHERE user_id = ? {clause}
                ORDER BY remind_at ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upcoming(self, *, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE user_id = ? AND status = 'SCHEDULED' AND remind_at >= ?
                ORDER BY remind_at ASC
                LIMIT ?
                """,
                (user_id, now, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel(self, reminder_id: str, *, user_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE reminders
                SET status = 'CANCELLED', updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (now, reminder_id, user_id),
            )
        return cursor.rowcount > 0

    def mark_done(self, reminder_id: str, *, user_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE reminders
                SET status = 'DONE', updated_at = ?, last_triggered_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (now, now, reminder_id, user_id),
            )
        return cursor.rowcount > 0
