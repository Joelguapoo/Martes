from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    display_name: str | None
    city: str | None
    weather_latitude: float | None
    weather_longitude: float | None
    weather_location_label: str | None
    created_by_user_id: str | None
    initial_welcome_shown_at: datetime | None
    last_seen_at: datetime | None
    welcome_enabled: bool
    task_reminders_enabled: bool
    contextual_suggestions_enabled: bool
    periodic_checks_enabled: bool
    voice_enabled: bool
    tone_analysis_enabled: bool


class UserProfileStore:
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
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    city TEXT,
                    weather_latitude REAL,
                    weather_longitude REAL,
                    weather_location_label TEXT,
                    created_by_user_id TEXT,
                    initial_welcome_shown_at TEXT,
                    last_seen_at TEXT,
                    welcome_enabled INTEGER NOT NULL DEFAULT 1,
                    task_reminders_enabled INTEGER NOT NULL DEFAULT 1,
                    contextual_suggestions_enabled INTEGER NOT NULL DEFAULT 1,
                    periodic_checks_enabled INTEGER NOT NULL DEFAULT 0,
                    voice_enabled INTEGER NOT NULL DEFAULT 0,
                    tone_analysis_enabled INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(user_profiles)").fetchall()
            }
            migrations = {
                "display_name": "ALTER TABLE user_profiles ADD COLUMN display_name TEXT",
                "city": "ALTER TABLE user_profiles ADD COLUMN city TEXT",
                "weather_latitude": "ALTER TABLE user_profiles ADD COLUMN weather_latitude REAL",
                "weather_longitude": "ALTER TABLE user_profiles ADD COLUMN weather_longitude REAL",
                "weather_location_label": (
                    "ALTER TABLE user_profiles ADD COLUMN weather_location_label TEXT"
                ),
                "created_by_user_id": (
                    "ALTER TABLE user_profiles ADD COLUMN created_by_user_id TEXT"
                ),
                "initial_welcome_shown_at": (
                    "ALTER TABLE user_profiles ADD COLUMN initial_welcome_shown_at TEXT"
                ),
                "last_seen_at": "ALTER TABLE user_profiles ADD COLUMN last_seen_at TEXT",
                "welcome_enabled": (
                    "ALTER TABLE user_profiles ADD COLUMN welcome_enabled "
                    "INTEGER NOT NULL DEFAULT 1"
                ),
                "task_reminders_enabled": (
                    "ALTER TABLE user_profiles ADD COLUMN task_reminders_enabled "
                    "INTEGER NOT NULL DEFAULT 1"
                ),
                "contextual_suggestions_enabled": (
                    "ALTER TABLE user_profiles ADD COLUMN contextual_suggestions_enabled "
                    "INTEGER NOT NULL DEFAULT 1"
                ),
                "periodic_checks_enabled": (
                    "ALTER TABLE user_profiles ADD COLUMN periodic_checks_enabled "
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "voice_enabled": (
                    "ALTER TABLE user_profiles ADD COLUMN voice_enabled INTEGER NOT NULL DEFAULT 0"
                ),
                "tone_analysis_enabled": (
                    "ALTER TABLE user_profiles ADD COLUMN tone_analysis_enabled "
                    "INTEGER NOT NULL DEFAULT 1"
                ),
            }
            for name, statement in migrations.items():
                if name not in columns:
                    conn.execute(statement)

    def ensure(self, user_id: str, *, created_by_user_id: str | None = None) -> UserProfile:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, created_by_user_id)
                    VALUES (?, ?)
                    """,
                    (user_id, created_by_user_id),
                )
        return self.get(user_id)

    def get(self, user_id: str) -> UserProfile:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return self.ensure(user_id)
        return self._from_row(row)

    def update(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        city: str | None = None,
        weather_latitude: float | None = None,
        weather_longitude: float | None = None,
        weather_location_label: str | None = None,
        settings: dict[str, bool] | None = None,
    ) -> UserProfile:
        self.ensure(user_id)
        fields: dict[str, object] = {}
        if display_name is not None:
            fields["display_name"] = display_name.strip() or None
        if city is not None:
            fields["city"] = city.strip() or None
        if weather_latitude is not None:
            fields["weather_latitude"] = weather_latitude
        if weather_longitude is not None:
            fields["weather_longitude"] = weather_longitude
        if weather_location_label is not None:
            fields["weather_location_label"] = weather_location_label.strip() or None
        for key, value in (settings or {}).items():
            if key in {
                "welcome_enabled",
                "task_reminders_enabled",
                "contextual_suggestions_enabled",
                "periodic_checks_enabled",
                "voice_enabled",
                "tone_analysis_enabled",
            }:
                fields[key] = int(value)
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            values = list(fields.values())
            values.append(user_id)
            with self._connect() as conn:
                conn.execute(f"UPDATE user_profiles SET {assignments} WHERE user_id = ?", values)
        return self.get(user_id)

    def mark_welcome_shown(self, user_id: str) -> UserProfile:
        now = datetime.now(UTC).isoformat()
        self.ensure(user_id)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE user_profiles
                SET initial_welcome_shown_at = COALESCE(initial_welcome_shown_at, ?),
                    last_seen_at = ?
                WHERE user_id = ?
                """,
                (now, now, user_id),
            )
        return self.get(user_id)

    def touch_seen(self, user_id: str) -> UserProfile:
        now = datetime.now(UTC).isoformat()
        self.ensure(user_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE user_profiles SET last_seen_at = ? WHERE user_id = ?",
                (now, user_id),
            )
        return self.get(user_id)

    def _from_row(self, row: sqlite3.Row) -> UserProfile:
        return UserProfile(
            user_id=row["user_id"],
            display_name=row["display_name"],
            city=row["city"],
            weather_latitude=row["weather_latitude"],
            weather_longitude=row["weather_longitude"],
            weather_location_label=row["weather_location_label"],
            created_by_user_id=row["created_by_user_id"],
            initial_welcome_shown_at=(
                datetime.fromisoformat(row["initial_welcome_shown_at"])
                if row["initial_welcome_shown_at"]
                else None
            ),
            last_seen_at=(
                datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None
            ),
            welcome_enabled=bool(row["welcome_enabled"]),
            task_reminders_enabled=bool(row["task_reminders_enabled"]),
            contextual_suggestions_enabled=bool(row["contextual_suggestions_enabled"]),
            periodic_checks_enabled=bool(row["periodic_checks_enabled"]),
            voice_enabled=bool(row["voice_enabled"]),
            tone_analysis_enabled=bool(row["tone_analysis_enabled"]),
        )
