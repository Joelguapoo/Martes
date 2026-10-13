from fastapi.testclient import TestClient

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.config.settings import get_settings
from app.main import _extract_folder_target, create_app
from app.tools.files import FileAgent, FileToolError


class FakeProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake", "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "Respuesta simulada"


def test_capabilities_endpoint_lists_real_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    payload = client.get("/api/capabilities").json()

    ids = {item["id"] for item in payload["tools"]}
    assert "files.create_directory" in ids
    assert "weather.current" in ids
    assert payload["workspace_root"].endswith("workspace")


def test_capability_question_uses_registry_not_generic_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    app = create_app(
        model_provider=FakeProvider(),
        database_path=tmp_path / "app.db",
        enable_web=False,
        auth_required=False,
    )
    client = TestClient(app)

    payload = client.post("/api/chat", json={"message": "Que puedes hacer?"}).json()

    assert "files.create_directory" in payload["answer"]
    assert "backend debe confirmarla" in payload["answer"]


def test_spanish_folder_parser_ignores_location_and_typo():
    folder = _extract_folder_target("crea una carpeta en el escritorio llamda prueba martes")

    assert folder == "prueba martes"


def test_spanish_folder_parser_stops_at_compound_clause():
    folder = _extract_folder_target(
        "Crea una carpeta en el escritorio llamada prueba final, "
        "dentro de esa carpeta crea un archivo txt"
    )

    assert folder == "prueba final"


def test_compound_folder_and_text_file_request_executes_locally(tmp_path, monkeypatch):
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
        json={
            "message": (
                "Crea una carpeta en el escritorio llamada prueba final, dentro de esa "
                "carpeta crea un archivo txt y quiero que dentro de ese archivo txt "
                "diga Hola Joel"
            )
        },
    ).json()

    folder = tmp_path / "workspace" / "prueba final"
    text_file = folder / "archivo.txt"
    assert payload["action"] == "create_folder_text_file"
    assert "Carpeta y archivo creados" in payload["answer"]
    assert folder.is_dir()
    assert text_file.read_text(encoding="utf-8") == "Hola Joel\n"


async def test_file_agent_blocks_path_traversal(tmp_path):
    agent = FileAgent(tmp_path / "workspace")

    try:
        await agent.create_directory({"path": "../fuera"})
    except FileToolError as exc:
        assert "sale del espacio autorizado" in str(exc)
    else:
        raise AssertionError("No debe permitir rutas fuera del workspace")


async def test_file_agent_creates_verified_file(tmp_path):
    agent = FileAgent(tmp_path / "workspace")

    folder = await agent.create_directory({"path": "Plan de estudio"})
    created = await agent.create_text_file(
        {"path": "Plan de estudio/objetivos.txt", "content": "Objetivos"}
    )

    assert folder["created"] is True
    assert created["created"] is True
    assert (tmp_path / "workspace" / "Plan de estudio" / "objetivos.txt").exists()
