from fastapi.testclient import TestClient

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.config.settings import get_settings
from app.main import create_app


class FakeProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake", "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "Respuesta simulada"


def test_project_request_creates_persistent_project_and_activity(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "MARTES, crea un proyecto para aprender Python durante dos meses"},
    )
    activity = client.get("/api/activity").json()

    assert response.status_code == 200
    assert "Proyecto creado" in response.json()["answer"]
    assert activity["projects"][0]["objective"] == "aprender Python durante dos meses"
    assert len(activity["tasks"]) >= 4
    assert activity["activity"][0]["kind"] == "plan_completed"
    assert any(item["kind"] == "step_completed" for item in activity["activity"])


def test_planning_request_creates_recoverable_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"message": "planifica organizar mis documentos con permisos seguros"},
    )
    plans = client.get("/api/plans").json()

    assert response.status_code == 200
    assert "Plan operativo registrado" in response.json()["answer"]
    assert plans[0]["status"] == "PENDING"
    assert "organizar mis documentos" in plans[0]["objective"]
