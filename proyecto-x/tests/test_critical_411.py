import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.config.settings import get_settings
from app.main import create_app
from app.research.models import ResearchResult, Source
from app.research.web import WebResearcher


class FakeProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake", "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "Comparación simulada con fuentes verificadas."


def _client(tmp_path, monkeypatch, *, enable_web: bool = False) -> TestClient:
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=enable_web,
        auth_required=False,
    )
    return TestClient(app)


def test_business_workspace_creates_meaningful_folder_and_file(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    payload = client.post(
        "/api/chat",
        json={
            "message": (
                "MARTES, necesito un espacio para organizar mis ideas de un negocio "
                "que quiero comenzar. Prepárame algo sencillo para empezar"
            )
        },
    ).json()

    folder = tmp_path / "workspace" / "ideas-negocio"
    file_path = folder / "ideas-iniciales.txt"
    content = file_path.read_text(encoding="utf-8")
    assert payload["action"] == "create_folder_text_file"
    assert folder.is_dir()
    assert "Problema que quiero resolver" in content
    assert "Cliente ideal" in content
    assert "contenido" in payload["answer"].lower()
    assert client.get("/api/activity").json()["projects"] == []


def test_exact_folder_and_three_objectives_file_does_not_create_project(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    payload = client.post(
        "/api/chat",
        json={
            "message": (
                "Crea una carpeta llamada Prueba MARTES 41, dentro crea un archivo "
                "objetivos.txt con tres objetivos para mi proyecto y comprueba que todo "
                "quedó guardado"
            )
        },
    ).json()

    folder = tmp_path / "workspace" / "Prueba MARTES 41"
    file_path = folder / "objetivos.txt"
    content = file_path.read_text(encoding="utf-8")
    objectives = re.findall(r"(?m)^\d+\.\s+\S", content)
    assert payload["action"] == "create_folder_text_file"
    assert "Proyecto creado" not in payload["answer"]
    assert folder.is_dir()
    assert len(objectives) == 3
    assert "Verifiqué 3" in payload["answer"]
    assert client.get("/api/activity").json()["projects"] == []


def test_research_report_and_week_project_are_separate_and_verified(tmp_path, monkeypatch):
    async def fake_research(self: WebResearcher, query: str) -> ResearchResult:
        now = datetime.now(UTC)
        return ResearchResult(
            query=query,
            summary="Tres recursos gratuitos comparados para aprender Python.",
            facts=["Curso A", "Curso B", "Curso C"],
            limitations=[],
            sources=[
                Source(
                    title="Python Docs Tutorial",
                    site="docs.python.org",
                    url="https://docs.python.org/3/tutorial/",
                    accessed_at=now,
                    snippet="Tutorial oficial de Python.",
                ),
                Source(
                    title="freeCodeCamp Python",
                    site="freecodecamp.org",
                    url="https://www.freecodecamp.org/learn/scientific-computing-with-python/",
                    accessed_at=now,
                    snippet="Curso gratuito con ejercicios.",
                ),
                Source(
                    title="Automate the Boring Stuff",
                    site="automatetheboringstuff.com",
                    url="https://automatetheboringstuff.com/",
                    accessed_at=now,
                    snippet="Libro gratuito orientado a automatización.",
                ),
            ],
        )

    monkeypatch.setattr(WebResearcher, "research", fake_research)
    client = _client(tmp_path, monkeypatch, enable_web=True)

    payload = client.post(
        "/api/chat",
        json={
            "message": (
                "Investiga tres recursos gratuitos para aprender Python, compáralos, "
                "guarda un informe con los enlaces originales y crea un proyecto con "
                "cinco tareas iniciales para estudiar durante una semana"
            )
        },
    ).json()

    report = tmp_path / "workspace" / "informes" / "recursos-python.md"
    report_text = report.read_text(encoding="utf-8")
    activity = client.get("/api/activity").json()
    plan = payload["payload"]["plan"]
    plan_id = plan["id"]
    events = [item for item in activity["activity"] if item.get("plan_id") == plan_id]
    event_titles = {item["title"] for item in events}
    step_titles = {step["title"] for step in plan["steps"]}
    assert payload["payload"]["project"]["name"] == "Estudio Python - Semana 1"
    assert len(payload["payload"]["project"]["tasks"]) == 5
    assert len(activity["tasks"]) == 5
    assert plan["status"] == "COMPLETED"
    assert "Crear proyecto de estudio con cinco tareas" in step_titles
    assert "Verificar persistencia del proyecto y sus cinco tareas" in step_titles
    assert "Validar resultados recuperados" in step_titles
    assert "Comparar fuentes verificadas" in step_titles
    assert "Proyecto creado" in event_titles
    assert "5 tareas creadas" in event_titles
    assert all(item.get("plan_id") == plan_id for item in events)
    assert report.exists()
    assert "## Comparación" in report_text
    assert "## Verificación" in report_text
    assert "https://docs.python.org/3/tutorial/" in report_text
    assert "https://www.freecodecamp.org/learn/scientific-computing-with-python/" in report_text
    assert "https://automatetheboringstuff.com/" in report_text
    resume = client.post(f"/api/plans/{plan_id}/resume").json()
    assert "ya está en estado COMPLETED" in resume["summary"]
    assert len(client.get("/api/activity").json()["tasks"]) == 5


def test_research_failure_still_creates_week_project_without_fake_report(tmp_path, monkeypatch):
    async def fake_research(self: WebResearcher, query: str) -> ResearchResult:
        return ResearchResult(
            query=query,
            summary="No se recuperaron fuentes verificables.",
            facts=[],
            limitations=["Sin red disponible."],
            sources=[],
        )

    monkeypatch.setattr(WebResearcher, "research", fake_research)
    client = _client(tmp_path, monkeypatch, enable_web=True)

    payload = client.post(
        "/api/chat",
        json={
            "message": (
                "Investiga tres recursos gratuitos para aprender Python, compáralos, "
                "guarda un informe con los enlaces originales y crea un proyecto con "
                "cinco tareas iniciales para estudiar durante una semana"
            )
        },
    ).json()

    report = tmp_path / "workspace" / "informes" / "recursos-python.md"
    plan = payload["payload"]["plan"]
    activity = client.get("/api/activity").json()
    events = [item for item in activity["activity"] if item.get("plan_id") == plan["id"]]
    event_titles = {item["title"] for item in events}
    assert payload["state"] == "error"
    assert "no inventé fuentes" in payload["answer"]
    assert len(payload["payload"]["project"]["tasks"]) == 5
    assert plan["status"] == "FAILED"
    assert "Proyecto creado" in event_titles
    assert "5 tareas creadas" in event_titles
    assert any(item["title"] == "Investigar tres recursos gratuitos" for item in events)
    assert not report.exists()


def test_project_tasks_can_be_requested_from_a_new_chat(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    created = client.post(
        "/api/chat",
        json={"message": "Crea un proyecto sobre Aprendizaje Web"},
    ).json()
    new_chat = client.post("/api/conversations", json={"title": "Consulta"}).json()

    response = client.post(
        "/api/chat",
        json={
            "conversation_id": new_chat["id"],
            "message": "MARTES, ¿qué tareas tiene mi proyecto Aprendizaje Web?",
        },
    ).json()

    assert created["payload"]["project"]["name"] == "Aprendizaje Web"
    assert response["payload"]["project"]["name"] == "Aprendizaje Web"
    assert "Definir alcance de Aprendizaje Web" in response["answer"]
    assert "tarea(s) guardadas" in response["answer"]


def test_research_report_keeps_composed_objective_without_truncating(tmp_path, monkeypatch):
    async def fake_research(self: WebResearcher, query: str) -> ResearchResult:
        now = datetime.now(UTC)
        return ResearchResult(
            query=query,
            summary="Recursos gratuitos verificados para aprender Python.",
            facts=[],
            limitations=[],
            sources=[
                Source(
                    title="Python Docs Tutorial",
                    site="docs.python.org",
                    url="https://docs.python.org/3/tutorial/",
                    accessed_at=now,
                    snippet="Tutorial oficial de Python.",
                ),
            ],
        )

    monkeypatch.setattr(WebResearcher, "research", fake_research)
    client = _client(tmp_path, monkeypatch, enable_web=True)

    payload = client.post(
        "/api/chat",
        json={
            "message": (
                "Investiga tres recursos gratuitos para aprender Python, compáralos, "
                "guarda un informe con los enlaces originales"
            )
        },
    ).json()

    plan = payload["payload"]["plan"]
    assert plan["objective"] == (
        "Investigar y guardar informe: tres recursos gratuitos para aprender Python"
    )
    assert plan["steps"][1]["result"]["payload"]["query"] == (
        "tres recursos gratuitos para aprender Python"
    )
