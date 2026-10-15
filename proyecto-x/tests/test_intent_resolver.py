import pytest
from fastapi.testclient import TestClient

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.config.settings import get_settings
from app.core.intent import IntentResolver, IntentType
from app.main import create_app
from app.security.permissions import RiskLevel
from app.tools.registry import ToolRegistry, ToolSpec


class FakeProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake", "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "Respuesta simulada"


async def _noop(payload: dict):
    return {"ok": True, "payload": payload}


@pytest.fixture
def registry() -> ToolRegistry:
    tools = ToolRegistry()
    for name in [
        "files.create_directory",
        "files.create_text_file",
        "files.list_directory",
        "files.scaffold_project",
        "documents.summarize",
        "documents.create_markdown",
        "research.web",
        "weather.current",
        "reminders.create",
    ]:
        tools.register(
            ToolSpec(
                name,
                f"Herramienta {name}",
                RiskLevel.QUERY,
                _noop,
                semantic_tags=name.split("."),
            )
        )
    return tools


@pytest.mark.parametrize(
    ("message", "expected_tool"),
    [
        ("Crea una carpeta llamada Proyecto Web", "files.create_directory"),
        ("Hazme un directorio para el proyecto web", "files.create_directory"),
        ("Prepárame un espacio donde guardar el material de la web", "files.create_directory"),
        ("Móntame una carpeta que se llame Proyecto Web", "files.create_directory"),
        (
            "Crea un sitio para tener organizados los archivos del proyecto web",
            "files.create_directory",
        ),
        ("crear acrpeta llamda Proyecto Web", "files.create_directory"),
    ],
)
def test_resolver_understands_folder_synonyms_and_typos(registry, message, expected_tool):
    resolution = IntentResolver(registry).resolve(message)

    assert resolution.intent_type == IntentType.FILE_ACTION
    assert resolution.has_tool(expected_tool)
    assert not resolution.clarification_required


def test_resolver_identifies_combined_research_report_request(registry):
    resolution = IntentResolver(registry).resolve(
        "Investiga tres alternativas, compáralas y guarda un informe markdown"
    )

    assert resolution.intent_type == IntentType.COMBINED
    assert resolution.has_tool("research.web")
    assert resolution.has_tool("documents.create_markdown")


def test_resolver_requests_authorization_for_risky_action(registry):
    resolution = IntentResolver(registry).resolve("Borra esa carpeta completa")

    assert resolution.intent_type == IntentType.AUTHORIZATION_REQUIRED
    assert resolution.clarification_required
    assert resolution.risk_level == "consequence"


def test_natural_directory_request_executes_without_model_guessing(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    payload = client.post(
        "/api/chat",
        json={"message": "Hazme un directorio para el proyecto web"},
    ).json()

    assert payload["answer"].startswith("Listo")
    assert (tmp_path / "workspace" / "proyecto web").is_dir()


def test_intent_diagnose_exposes_safe_structured_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    payload = client.post(
        "/api/intent/diagnose",
        json={"message": "Échale un vistazo a la carpeta Proyecto Web"},
    ).json()

    assert payload["intent"]["intent_type"] == "file_action"
    assert payload["diagnostics"]["resolver"] == "local_semantic"
    assert "candidate_tools" in payload["intent"]
