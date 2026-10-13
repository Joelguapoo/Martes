import sqlite3

from app.memory.conversation import ConversationStore


def test_conversations_keep_separate_histories_and_titles(tmp_path):
    db = tmp_path / "app.db"
    store = ConversationStore(db)
    first = store.create_conversation()
    second = store.create_conversation()

    store.add("user", "hola primer chat", conversation_id=first.id)
    store.add("assistant", "respuesta uno", conversation_id=first.id)
    store.add("user", "hola segundo chat", conversation_id=second.id)

    assert [item.content for item in store.messages(first.id)] == [
        "hola primer chat",
        "respuesta uno",
    ]
    assert [item.content for item in store.messages(second.id)] == ["hola segundo chat"]
    assert store.get_conversation(first.id).title == "hola primer chat"
    assert store.get_conversation(second.id).title == "hola segundo chat"


def test_conversations_persist_after_reopen(tmp_path):
    db = tmp_path / "app.db"
    store = ConversationStore(db)
    item = store.create_conversation()
    store.add("user", "persisteme", conversation_id=item.id)

    reopened = ConversationStore(db)

    assert reopened.get_conversation(item.id) is not None
    assert reopened.messages(item.id)[0].content == "persisteme"


def test_legacy_messages_are_migrated_without_loss(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE conversation_messages (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO conversation_messages (id, role, content, kind, created_at)
            VALUES ('1', 'user', 'historial antiguo', 'message', '2026-10-13T09:00:00+00:00')
            """
        )

    store = ConversationStore(db)
    conversations = store.list_conversations()

    assert len(conversations) == 1
    assert store.messages(conversations[0].id)[0].content == "historial antiguo"
