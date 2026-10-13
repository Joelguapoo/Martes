from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelStatus:
    available: bool
    provider: str
    model: str
    message: str
    preferred: bool = False
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class LocalModelProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def status(self) -> ModelStatus:
        raise NotImplementedError

    @abstractmethod
    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        raise NotImplementedError

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        yield await self.complete(messages)
