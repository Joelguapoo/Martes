from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from app.memory.models import ConfirmationStatus, MemoryCategory, MemoryItem, MemorySource


class MemoryStore:
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
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    confirmation_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT
                )
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "user_id" not in columns:
                conn.execute("ALTER TABLE memories ADD COLUMN user_id TEXT")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(
                    id UNINDEXED,
                    content,
                    category,
                    content='memories',
                    content_rowid='rowid'
                )
                """
            )
            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, id, content, category)
                    VALUES (new.rowid, new.id, new.content, new.category);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, id, content, category)
                    VALUES('delete', old.rowid, old.id, old.content, old.category);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, id, content, category)
                    VALUES('delete', old.rowid, old.id, old.content, old.category);
                    INSERT INTO memories_fts(rowid, id, content, category)
                    VALUES (new.rowid, new.id, new.content, new.category);
                END;
                """
            )

    def add(self, item: MemoryItem, *, user_id: str | None = None) -> MemoryItem:
        if self._looks_sensitive(item.content):
            return item
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories
                (id, user_id, category, content, source, confidence, confirmation_status,
                 created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._to_row(item, user_id=user_id),
            )
        return item

    def list(
        self,
        include_discarded: bool = False,
        *,
        user_id: str | None = None,
        include_unowned: bool = False,
    ) -> list[MemoryItem]:
        query = "SELECT * FROM memories"
        clauses: list[str] = []
        params: list[str] = []
        if not include_discarded:
            clauses.append("confirmation_status != ?")
            params.append(ConfirmationStatus.DISCARDED.value)
        if user_id:
            if include_unowned:
                clauses.append("(user_id = ? OR user_id IS NULL)")
            else:
                clauses.append("user_id = ?")
            params.append(user_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            return [self._from_row(row) for row in conn.execute(query, params)]

    def search(
        self,
        query: str,
        limit: int = 8,
        *,
        user_id: str | None = None,
        include_unowned: bool = False,
    ) -> list[MemoryItem]:
        clean = self._fts_query(query)
        if not clean:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT memories.* FROM memories_fts
                JOIN memories ON memories.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ? AND memories.confirmation_status != ?
                AND (
                    ? IS NULL
                    OR memories.user_id = ?
                    OR (? = 1 AND memories.user_id IS NULL)
                )
                ORDER BY bm25(memories_fts)
                LIMIT ?
                """,
                (
                    clean,
                    ConfirmationStatus.DISCARDED.value,
                    user_id,
                    user_id,
                    1 if include_unowned else 0,
                    limit,
                ),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def _fts_query(self, query: str) -> str:
        terms = re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]{3,}", query, flags=re.UNICODE)
        return " OR ".join(f'"{term}"' for term in terms[:8])

    def update(
        self,
        item_id: str,
        *,
        content: str,
        category: MemoryCategory,
        user_id: str | None = None,
    ) -> MemoryItem | None:
        if self._looks_sensitive(content):
            return None
        now = datetime.now(UTC).isoformat()
        clause = "id = ?"
        params: list[str] = [content, category.value, now, item_id]
        if user_id:
            clause += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE memories SET content = ?, category = ?, updated_at = ? WHERE {clause}",
                params,
            )
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (item_id,)).fetchone()
        return self._from_row(row) if row else None

    def delete(self, item_id: str, *, user_id: str | None = None) -> bool:
        clause = "id = ?"
        params: list[str] = [item_id]
        if user_id:
            clause += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        with self._connect() as conn:
            cursor = conn.execute(f"DELETE FROM memories WHERE {clause}", params)
            return cursor.rowcount > 0

    def clear(self, *, user_id: str | None = None) -> None:
        with self._connect() as conn:
            if user_id:
                conn.execute(
                    "DELETE FROM memories WHERE user_id = ? OR user_id IS NULL",
                    (user_id,),
                )
            else:
                conn.execute("DELETE FROM memories")

    def add_many(self, items: Iterable[MemoryItem]) -> list[MemoryItem]:
        return [self.add(item) for item in items]

    def _to_row(
        self,
        item: MemoryItem,
        *,
        user_id: str | None = None,
    ) -> tuple[str, str | None, str, str, str, float, str, str, str, str | None]:
        return (
            item.id,
            user_id,
            item.category.value,
            item.content,
            item.source.value,
            item.confidence,
            item.confirmation_status.value,
            item.created_at.isoformat(),
            item.updated_at.isoformat(),
            item.expires_at.isoformat() if item.expires_at else None,
        )

    def _from_row(self, row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            id=row["id"],
            category=MemoryCategory(row["category"]),
            content=row["content"],
            source=MemorySource(row["source"]),
            confidence=row["confidence"],
            confirmation_status=ConfirmationStatus(row["confirmation_status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        )

    def _looks_sensitive(self, text: str) -> bool:
        lowered = text.lower()
        markers = [
            "password",
            "contrasena",
            "contraseña",
            "api key",
            "apikey",
            "token",
            "codigo de verificacion",
            "código de verificación",
            "secret",
            "groq_api_key",
        ]
        return any(marker in lowered for marker in markers)
