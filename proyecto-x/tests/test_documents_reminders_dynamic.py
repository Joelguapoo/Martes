from fastapi.testclient import TestClient

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.config.settings import get_settings
from app.main import create_app
from app.reminders.store import ReminderStore


class FakeProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake", "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "Respuesta simulada"


def build_client(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(workspace))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    return TestClient(app), workspace


def test_document_tools_read_and_summarize_authorized_files(tmp_path, monkeypatch):
    client, workspace = build_client(tmp_path, monkeypatch)
    (workspace / "notas.md").write_text(
        "# Notas\n\nAprender Python con ejercicios.",
        encoding="utf-8",
    )
    (workspace / "datos.csv").write_text("tema,horas\npython,4\n", encoding="utf-8")

    summary = client.post(
        "/api/tools/run",
        json={"tool": "documents.summarize", "payload": {"path": "notas.md"}},
    ).json()
    csv_payload = client.post(
        "/api/tools/run",
        json={"tool": "documents.read_csv", "payload": {"path": "datos.csv"}},
    ).json()

    assert summary["result"]["words"] >= 4
    assert csv_payload["result"]["headers"] == ["tema", "horas"]


def test_dynamic_planner_executes_document_summary_plan(tmp_path, monkeypatch):
    client, workspace = build_client(tmp_path, monkeypatch)
    (workspace / "plan.md").write_text("Linea uno.\nLinea dos importante.", encoding="utf-8")

    response = client.post("/api/chat", json={"message": "resume el archivo plan.md"})
    activity = client.get("/api/activity").json()

    assert response.status_code == 200
    assert "Plan dinámico ejecutado" in response.json()["answer"]
    assert activity["plans"][0]["status"] == "COMPLETED"
    assert any(event["kind"] == "step_completed" for event in activity["activity"])


def test_dynamic_planner_creates_persistent_reminder(tmp_path, monkeypatch):
    client, _workspace = build_client(tmp_path, monkeypatch)

    response = client.post(
        "/api/chat",
        json={"message": "MARTES, recuérdame mañana a las ocho terminar el informe"},
    )
    activity = client.get("/api/activity").json()

    assert response.status_code == 200
    assert "Plan dinámico ejecutado" in response.json()["answer"]
    assert activity["reminders"]
    assert activity["reminders"][0]["message"]
    assert activity["reminders"][0]["timezone"] == "America/Bogota"


def test_users_cannot_access_other_users_reminders(tmp_path):
    from datetime import datetime, timedelta, timezone

    store = ReminderStore(tmp_path / "app.db")
    item = store.create(
        user_id="u1",
        message="privado",
        remind_at=datetime(
            2026,
            10,
            14,
            8,
            tzinfo=timezone(timedelta(hours=-5), name="America/Bogota"),
        ),
        timezone="America/Bogota",
    )

    assert store.get(item["id"], user_id="u2") is None
    assert store.list(user_id="u2") == []
    assert store.cancel(item["id"], user_id="u2") is False
