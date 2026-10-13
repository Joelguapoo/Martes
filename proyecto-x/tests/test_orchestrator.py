from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.core.orchestrator import AssistantOrchestrator
from app.memory.store import MemoryStore


class FakeProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake", "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "Respuesta local simulada"


class MissingProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(False, "fake", "missing", "modelo ausente")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        raise AssertionError("No debe llamarse si falta el modelo")


async def test_orchestrator_uses_fake_provider(tmp_path):
    orchestrator = AssistantOrchestrator(
        FakeProvider(),
        MemoryStore(tmp_path / "memory.db"),
        researcher=None,
    )

    result = await orchestrator.handle("Hola")

    assert result.answer == "Respuesta local simulada"
    assert result.model_available is True


async def test_orchestrator_degrades_without_model(tmp_path):
    orchestrator = AssistantOrchestrator(
        MissingProvider(),
        MemoryStore(tmp_path / "memory.db"),
        researcher=None,
    )

    result = await orchestrator.handle("Hola")

    assert result.answer is not None
    assert "proveedor de IA no esta configurado" in result.answer
    assert result.notice == result.answer
    assert result.model_available is False
