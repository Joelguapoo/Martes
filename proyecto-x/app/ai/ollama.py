import json
from collections.abc import AsyncIterator

import httpx

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus


class OllamaProvider(LocalModelProvider):
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    @property
    def name(self) -> str:
        return "ollama"

    async def status(self) -> ModelStatus:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
        except Exception as exc:
            return ModelStatus(
                available=False,
                provider="ollama",
                model=self.model,
                message=(
                    "Ollama no esta disponible en 127.0.0.1:11434. Instala Ollama, "
                    "inicia el servicio y descarga un modelo local compatible."
                    f" Detalle: {exc}"
                ),
            )

        names = {item.get("name") for item in models}
        if self.model not in names:
            return ModelStatus(
                available=False,
                provider="ollama",
                model=self.model,
                message=(
                    f"Ollama responde, pero el modelo '{self.model}' no esta instalado. "
                    f"Ejecuta: ollama pull {self.model}"
                ),
            )
        return ModelStatus(True, "ollama", self.model, "Modelo local disponible.")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": [message.__dict__ for message in messages],
            "stream": False,
            "options": {"temperature": 0.5},
        }
        async with httpx.AsyncClient(timeout=timeout or 60) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"].strip()

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": [message.__dict__ for message in messages],
            "stream": True,
            "options": {"temperature": 0.5},
        }
        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content")
                if content:
                    yield content
