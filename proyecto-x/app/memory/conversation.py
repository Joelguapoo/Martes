from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = "Nuevo chat"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "active"
    user_id: str | None = None


class ConversationMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    role: str
    content: str
    kind: str = "message"
    status: str = "complete"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationStore:
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
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                )
                """
            )
            conversation_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "user_id" not in conversation_columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(conversation_messages)").fetchall()
            }
            if "conversation_id" not in columns:
                conn.execute("ALTER TABLE conversation_messages ADD COLUMN conversation_id TEXT")
            if "status" not in columns:
                conn.execute(
                    "ALTER TABLE conversation_messages "
                    "ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation
                ON conversation_messages(conversation_id, created_at)
                """
            )
            self._migrate_legacy_messages(conn)

    def _migrate_legacy_messages(self, conn: sqlite3.Connection) -> None:
        legacy_count = conn.execute(
            "SELECT COUNT(*) AS count FROM conversation_messages WHERE conversation_id IS NULL"
        ).fetchone()["count"]
        if legacy_count == 0:
            return
        now = datetime.now(UTC).isoformat()
        first = conn.execute(
            """
            SELECT content, created_at FROM conversation_messages
            WHERE conversation_id IS NULL AND role = 'user'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        title = self._title_from_text(first["content"]) if first else "Historial migrado"
        created_at = first["created_at"] if first else now
        conversation_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO conversations (id, user_id, title, created_at, updated_at, status)
            VALUES (?, NULL, ?, ?, ?, 'active')
            """,
            (conversation_id, title, created_at, now),
        )
        conn.execute(
            """
            UPDATE conversation_messages
            SET conversation_id = ?
            WHERE conversation_id IS NULL
            """,
            (conversation_id,),
        )

    def create_conversation(
        self,
        title: str = "Nuevo chat",
        *,
        user_id: str | None = None,
    ) -> Conversation:
        item = Conversation(title=self._clean_title(title) or "Nuevo chat", user_id=user_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, user_id, title, created_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.user_id,
                    item.title,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    item.status,
                ),
            )
        return item

    def ensure_default_conversation(self, *, user_id: str | None = None) -> Conversation:
        conversations = self.list_conversations(limit=1, user_id=user_id)
        if conversations:
            return conversations[0]
        return self.create_conversation(user_id=user_id)

    def list_conversations(
        self,
        limit: int = 100,
        *,
        user_id: str | None = None,
        include_unowned: bool = False,
    ) -> list[Conversation]:
        owner_filter = ""
        params: list[str | int] = []
        if user_id:
            owner_filter = "AND (user_id = ? OR (? = 1 AND user_id IS NULL))"
            params.extend([user_id, 1 if include_unowned else 0])
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM conversations
                WHERE status = 'active'
                {owner_filter}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def get_conversation(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
        include_unowned: bool = False,
    ) -> Conversation | None:
        owner_filter = ""
        params: list[str | int] = [conversation_id]
        if user_id:
            owner_filter = "AND (user_id = ? OR (? = 1 AND user_id IS NULL))"
            params.extend([user_id, 1 if include_unowned else 0])
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM conversations WHERE id = ? AND status = 'active' {owner_filter}",
                params,
            ).fetchone()
        return self._conversation_from_row(row) if row else None

    def rename_conversation(
        self,
        conversation_id: str,
        title: str,
        *,
        user_id: str | None = None,
    ) -> Conversation | None:
        title = self._clean_title(title)
        if not title:
            return None
        now = datetime.now(UTC).isoformat()
        clause = "id = ?"
        params: list[str] = [title, now, conversation_id]
        if user_id:
            clause += " AND (user_id = ? OR user_id IS NULL)"
            params.append(user_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE conversations SET title = ?, updated_at = ? WHERE {clause}",
                params,
            )
        return self.get_conversation(conversation_id, user_id=user_id, include_unowned=True)

    def delete_conversation(self, conversation_id: str, *, user_id: str | None = None) -> bool:
        item = self.get_conversation(conversation_id, user_id=user_id, include_unowned=True)
        if item is None:
            return False
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return True

    def add(
        self,
        role: str,
        content: str,
        kind: str = "message",
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        status: str = "complete",
    ) -> ConversationMessage:
        conversation = (
            self.get_conversation(conversation_id, user_id=user_id, include_unowned=True)
            if conversation_id
            else self.ensure_default_conversation(user_id=user_id)
        )
        if conversation is None:
            raise ValueError("Conversacion no encontrada.")
        item = ConversationMessage(
            conversation_id=conversation.id,
            role=role,
            content=content,
            kind=kind,
            status=status,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_messages
                (id, conversation_id, role, content, kind, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.conversation_id,
                    item.role,
                    item.content,
                    item.kind,
                    item.status,
                    item.created_at.isoformat(),
                ),
            )
            title = conversation.title
            if role == "user" and title == "Nuevo chat":
                title = self._title_from_text(content)
            conn.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?, user_id = COALESCE(user_id, ?)
                WHERE id = ?
                """,
                (title, item.created_at.isoformat(), user_id, conversation.id),
            )
        return item

    def messages(
        self,
        conversation_id: str,
        limit: int = 120,
        *,
        user_id: str | None = None,
    ) -> list[ConversationMessage]:
        if self.get_conversation(conversation_id, user_id=user_id, include_unowned=True) is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]

    def recent(
        self,
        limit: int = 80,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        include_unowned: bool = False,
    ) -> list[ConversationMessage]:
        if conversation_id:
            return self.messages(conversation_id, limit=limit, user_id=user_id)
        owner_filter = ""
        params: list[str | int] = []
        if user_id:
            owner_filter = """
                JOIN conversations ON conversations.id = conversation_messages.conversation_id
                WHERE conversations.user_id = ? OR (? = 1 AND conversations.user_id IS NULL)
            """
            params.extend([user_id, 1 if include_unowned else 0])
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT conversation_messages.* FROM conversation_messages
                {owner_filter}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._message_from_row(row) for row in reversed(rows)]

    def since(self, start_iso: str, limit: int = 200) -> list[ConversationMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversation_messages
                WHERE created_at >= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (start_iso, limit),
            ).fetchall()
        return [self._message_from_row(row) for row in rows]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM conversation_messages")
            conn.execute("DELETE FROM conversations")

    def _conversation_from_row(self, row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            status=row["status"],
            user_id=row["user_id"],
        )

    def _message_from_row(self, row: sqlite3.Row) -> ConversationMessage:
        return ConversationMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            kind=row["kind"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _title_from_text(self, text: str) -> str:
        cleaned = self._clean_title(text)
        return cleaned[:48].rstrip() or "Nuevo chat"

    def _clean_title(self, text: str) -> str:
        return " ".join(text.split())
