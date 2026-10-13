from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.ai.manager import ProviderManager


class StaticProvider(LocalModelProvider):
    def __init__(self, name: str, available: bool) -> None:
        self._name = name
        self.available = available

    @property
    def name(self) -> str:
        return self._name

    async def status(self) -> ModelStatus:
        return ModelStatus(self.available, self.name, f"{self.name}-model", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return f"respuesta desde {self.name}"


async def test_provider_manager_selects_first_available_in_auto():
    manager = ProviderManager(
        [StaticProvider("ollama", False), StaticProvider("llama.cpp", True)],
        mode="auto",
    )

    status = await manager.status()
    answer = await manager.complete([ChatMessage("user", "hola")])

    assert status.provider == "llama.cpp"
    assert answer == "respuesta desde llama.cpp"


async def test_provider_manager_does_not_use_remote_when_disabled():
    manager = ProviderManager([StaticProvider("remote-authorized", False)], mode="auto")

    status = await manager.status()

    assert status.available is False
    assert status.provider == "none"
