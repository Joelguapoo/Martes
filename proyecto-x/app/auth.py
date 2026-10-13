from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type

OWNER_EMAIL = "kevynnquintero32@gmail.com"
SESSION_COOKIE = "martes_session"
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    role: str
    must_change_password: bool
    created_at: datetime
    disabled_at: datetime | None


@dataclass(frozen=True)
class AuthSession:
    id: str
    user_id: str
    csrf_token: str
    expires_at: datetime


class AuthStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    disabled_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES auth_users(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    email TEXT PRIMARY KEY,
                    failed_count INTEGER NOT NULL,
                    last_failed_at TEXT NOT NULL
                )
                """
            )

    def bootstrap_owner(self, email: str, password: str | None) -> AuthUser | None:
        existing = self.get_user_by_email(email)
        if existing:
            return existing
        if not password:
            return None
        return self.create_user(
            email=email,
            password=password,
            role="owner",
            must_change_password=True,
        )

    def has_users(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM auth_users").fetchone()
        return row["count"] > 0

    def create_user(
        self,
        *,
        email: str,
        password: str,
        role: str = "user",
        must_change_password: bool = False,
    ) -> AuthUser:
        now = datetime.now(UTC)
        user = AuthUser(
            id=str(uuid4()),
            email=self._normalize_email(email),
            role=role,
            must_change_password=must_change_password,
            created_at=now,
            disabled_at=None,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_users
                (id, email, password_hash, role, must_change_password, created_at, disabled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.email,
                    self.hasher.hash(password),
                    user.role,
                    int(user.must_change_password),
                    user.created_at.isoformat(),
                    None,
                ),
            )
        return user

    def authenticate(self, email: str, password: str) -> AuthUser | None:
        email = self._normalize_email(email)
        if self._is_rate_limited(email):
            return None
        row = self._user_row_by_email(email)
        if row is None or row["disabled_at"]:
            self._record_failed_attempt(email)
            return None
        try:
            ok = self.hasher.verify(row["password_hash"], password)
        except VerifyMismatchError:
            ok = False
        if not ok:
            self._record_failed_attempt(email)
            return None
        self._clear_failed_attempts(email)
        return self._user_from_row(row)

    def create_session(self, user_id: str, *, hours: int = 12) -> AuthSession:
        now = datetime.now(UTC)
        session = AuthSession(
            id=secrets.token_urlsafe(32),
            user_id=user_id,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now + timedelta(hours=hours),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (id, user_id, csrf_token, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.user_id,
                    session.csrf_token,
                    now.isoformat(),
                    session.expires_at.isoformat(),
                ),
            )
        return session

    def get_session(self, session_id: str | None) -> tuple[AuthUser, AuthSession] | None:
        if not session_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT auth_sessions.*, auth_users.email, auth_users.role,
                       auth_users.must_change_password, auth_users.created_at AS user_created_at,
                       auth_users.disabled_at
                FROM auth_sessions
                JOIN auth_users ON auth_users.id = auth_sessions.user_id
                WHERE auth_sessions.id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= datetime.now(UTC) or row["disabled_at"]:
            self.delete_session(session_id)
            return None
        user = AuthUser(
            id=row["user_id"],
            email=row["email"],
            role=row["role"],
            must_change_password=bool(row["must_change_password"]),
            created_at=datetime.fromisoformat(row["user_created_at"]),
            disabled_at=datetime.fromisoformat(row["disabled_at"]) if row["disabled_at"] else None,
        )
        session = AuthSession(
            id=row["id"],
            user_id=row["user_id"],
            csrf_token=row["csrf_token"],
            expires_at=expires_at,
        )
        return user, session

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE id = ?", (session_id,))

    def get_user_by_email(self, email: str) -> AuthUser | None:
        row = self._user_row_by_email(self._normalize_email(email))
        return self._user_from_row(row) if row else None

    def user_exists(self, email: str) -> bool:
        return self.get_user_by_email(email) is not None

    def _user_row_by_email(self, email: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM auth_users WHERE email = ?", (email,)).fetchone()

    def _is_rate_limited(self, email: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM auth_attempts WHERE email = ?", (email,)).fetchone()
        if not row or row["failed_count"] < 5:
            return False
        last = datetime.fromisoformat(row["last_failed_at"])
        return datetime.now(UTC) - last < timedelta(minutes=10)

    def _record_failed_attempt(self, email: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_attempts (email, failed_count, last_failed_at)
                VALUES (?, 1, ?)
                ON CONFLICT(email) DO UPDATE SET
                    failed_count = failed_count + 1,
                    last_failed_at = excluded.last_failed_at
                """,
                (email, now),
            )

    def _clear_failed_attempts(self, email: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_attempts WHERE email = ?", (email,))

    def _user_from_row(self, row: sqlite3.Row) -> AuthUser:
        return AuthUser(
            id=row["id"],
            email=row["email"],
            role=row["role"],
            must_change_password=bool(row["must_change_password"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            disabled_at=datetime.fromisoformat(row["disabled_at"]) if row["disabled_at"] else None,
        )

    def _normalize_email(self, email: str) -> str:
        return email.strip().lower()
