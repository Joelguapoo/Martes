from app.ai.base import ModelStatus
from app.core.briefing import BriefingService
from app.memory.conversation import ConversationStore
from app.memory.store import MemoryStore


def test_briefing_empty_database(tmp_path):
    db = tmp_path / "app.db"
    service = BriefingService(ConversationStore(db), MemoryStore(db))

    briefing = service.build(
        model_status=ModelStatus(False, "none", "ninguno", "sin proveedor"),
        provider_statuses=[],
        backend_available=True,
        web_enabled=True,
        voice_available=False,
    )

    assert "Aun no hay actividad" in briefing.summary
    assert briefing.last_conversation["last_user_message"] is None
    assert briefing.memory_count == 0


def test_briefing_uses_last_user_message(tmp_path):
    db = tmp_path / "app.db"
    conversations = ConversationStore(db)
    conversations.add("user", "hola proyecto x")
    service = BriefingService(conversations, MemoryStore(db))

    briefing = service.build(
        model_status=ModelStatus(True, "fake", "fake-local", "ok"),
        provider_statuses=[],
        backend_available=True,
        web_enabled=True,
        voice_available=False,
    )

    assert briefing.last_conversation["last_user_message"] == "hola proyecto x"
