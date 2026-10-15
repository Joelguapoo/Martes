from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus


@dataclass(frozen=True)
class ProviderSelection:
    active: LocalModelProvider | None
    active_status: ModelStatus
    statuses: list[ModelStatus]
    mode: str


class NoAvailableProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "none"

    async def status(self) -> ModelStatus:
        return ModelStatus(False, self.name, "ninguno", "No hay proveedor de IA disponible.")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        raise RuntimeError("No hay proveedor de IA disponible.")


class ProviderManager(LocalModelProvider):
    def __init__(self, providers: list[LocalModelProvider], mode: str = "auto") -> None:
        self.providers = providers
        self.mode = mode
        self._fallback = NoAvailableProvider()

    @property
    def name(self) -> str:
        return "provider-manager"

    async def inspect(self) -> ProviderSelection:
        statuses: list[ModelStatus] = []
        selected: LocalModelProvider | None = None
        selected_status: ModelStatus | None = None
        for provider in self.providers:
            status = await provider.status()
            is_preferred = self.mode in {"auto", provider.name}
            status = ModelStatus(
                status.available,
                status.provider,
                status.model,
                status.message,
                preferred=is_preferred,
                details=status.details,
            )
            statuses.append(status)
            if selected is None and status.available and is_preferred:
                selected = provider
                selected_status = status
                if self.mode != "auto":
                    break
        if selected is None and self.mode != "auto":
            for provider, status in zip(self.providers, statuses, strict=False):
                if provider.name == self.mode:
                    selected_status = status
                    break
        if selected_status is None:
            selected_status = await self._fallback.status()
        return ProviderSelection(selected, selected_status, statuses, self.mode)

    async def status(self) -> ModelStatus:
        return (await self.inspect()).active_status

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        selection = await self.inspect()
        if selection.active is None:
            raise RuntimeError(selection.active_status.message)
        return await selection.active.complete(messages, timeout=timeout)

    @property
    def supports_native_tools(self) -> bool:
        return True

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str = "auto",
        timeout: float | None = None,
    ) -> dict:
        selection = await self.inspect()
        if selection.active is None:
            raise RuntimeError(selection.active_status.message)
        return await selection.active.complete_with_tools(
            messages,
            tools,
            tool_choice=tool_choice,
            timeout=timeout,
        )

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        selection = await self.inspect()
        if selection.active is None:
            raise RuntimeError(selection.active_status.message)
        async for chunk in selection.active.stream(messages):
            yield chunk
