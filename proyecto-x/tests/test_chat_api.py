from fastapi.testclient import TestClient

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.main import create_app

BUILD_REPORT = "Listo. Construí la primera versión funcional de Proyecto X"


class RecordingProvider(LocalModelProvider):
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake", "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        self.messages = messages
        return "Hola. Estoy operativo desde el proveedor conversacional."


class OfflineProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(False, "fake", "missing", "modelo ausente")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        raise AssertionError("No debe llamarse si el modelo esta desconectado")


def test_chat_route_uses_conversational_provider(tmp_path):
    provider = RecordingProvider()
    app = create_app(
        model_provider=provider,
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    response = client.post("/api/chat", json={"message": "hola"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Hola. Estoy operativo desde el proveedor conversacional."
    assert payload["notice"] is None
    assert provider.messages[-1].role == "user"
    assert provider.messages[-1].content == "hola"
    assert BUILD_REPORT not in payload["answer"]


def test_chat_route_returns_notice_when_model_offline(tmp_path):
    app = create_app(
        model_provider=OfflineProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    response = client.post("/api/chat", json={"message": "hola"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] is None
    assert "proveedor de IA no esta configurado" in payload["notice"]
    assert BUILD_REPORT not in payload["notice"]


def test_stream_route_always_emits_terminal_event_when_model_offline(tmp_path):
    app = create_app(
        model_provider=OfflineProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    response = client.post("/api/chat/stream", json={"message": "hola"})

    assert response.status_code == 200
    events = [line for line in response.text.splitlines() if line.strip()]
    assert any('"event": "notice"' in line for line in events)
    assert any('"event": "done"' in line for line in events)
    assert all('"request_id"' in line for line in events)


def test_history_persists_user_and_notice_without_fake_assistant(tmp_path):
    db = tmp_path / "app.db"
    app = create_app(
        model_provider=OfflineProvider(),
        database_path=db,
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    client.post("/api/chat", json={"message": "hola"})
    history = client.get("/api/history").json()

    assert [item["role"] for item in history] == ["user", "system"]
    assert history[1]["kind"] == "notice"


def test_chat_route_keeps_conversation_histories_separate(tmp_path):
    provider = RecordingProvider()
    app = create_app(
        model_provider=provider,
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)
    first = client.post("/api/conversations", json={"title": "Primero"}).json()
    second = client.post("/api/conversations", json={"title": "Segundo"}).json()

    client.post("/api/chat", json={"conversation_id": first["id"], "message": "hola uno"})
    client.post("/api/chat", json={"conversation_id": second["id"], "message": "hola dos"})

    first_messages = client.get(f"/api/history?conversation_id={first['id']}").json()
    second_messages = client.get(f"/api/history?conversation_id={second['id']}").json()

    assert [item["content"] for item in first_messages if item["role"] == "user"] == ["hola uno"]
    assert [item["content"] for item in second_messages if item["role"] == "user"] == ["hola dos"]
    assert len(first_messages) == 2
    assert len(second_messages) == 2
