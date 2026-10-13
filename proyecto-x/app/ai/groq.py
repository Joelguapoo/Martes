from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus, ProviderError

GROQ_KEYS_URL = "https://console.groq.com/keys"
GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(LocalModelProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = GROQ_OPENAI_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key.strip() if api_key else None
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    @property
    def name(self) -> str:
        return "groq"

    async def status(self) -> ModelStatus:
        if not self.api_key:
            return ModelStatus(
                False,
                self.name,
                self.model,
                "Groq no esta configurado: falta GROQ_API_KEY.",
                details={"code": "missing_api_key", "setup_url": GROQ_KEYS_URL},
            )
        try:
            data = await self._request("GET", "/models", timeout=12)
        except ProviderError as exc:
            return ModelStatus(
                False,
                self.name,
                self.model,
                exc.message,
                details={"code": exc.code, "setup_url": GROQ_KEYS_URL},
            )
        available_models = [
            item.get("id")
            for item in data.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
        if available_models and self.model not in available_models:
            return ModelStatus(
                False,
                self.name,
                self.model,
                f"Groq responde, pero el modelo '{self.model}' no aparece disponible.",
                details={"code": "model_unavailable", "available_models": available_models[:12]},
            )
        return ModelStatus(
            True,
            self.name,
            self.model,
            "GroqCloud disponible mediante API OpenAI-compatible.",
            details={"base_url": self.base_url, "code": "ok"},
        )

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        if not self.api_key:
            raise ProviderError(
                "Falta GROQ_API_KEY. "
                f"Crea una clave en {GROQ_KEYS_URL} y guardala en Configuracion.",
                code="missing_api_key",
            )
        payload = {
            "model": self.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": 0.7,
            "stream": False,
        }
        data = await self._request("POST", "/chat/completions", json=payload, timeout=timeout or 60)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "Groq respondio con un formato inesperado.",
                code="invalid_response",
            ) from exc
        return str(content).strip()

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        if not self.api_key:
            raise ProviderError(
                "Falta GROQ_API_KEY. "
                f"Crea una clave en {GROQ_KEYS_URL} y guardala en Configuracion.",
                code="missing_api_key",
            )
        payload = {
            "model": self.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "temperature": 0.7,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with (
                httpx.AsyncClient(base_url=self.base_url, timeout=60) as client,
                client.stream(
                    "POST",
                    "/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = line.removeprefix("data: ").strip()
                    if event == "[DONE]":
                        break
                    data = json.loads(event)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield str(content)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Groq no respondio a tiempo. Puede ser una interrupcion temporal.",
                code="timeout",
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"No se pudo conectar con Groq: {exc}",
                code="connection_error",
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout,
                transport=self.transport,
            ) as client:
                response = await client.request(method, path, headers=headers, json=json)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Groq no respondio a tiempo. Puede ser una interrupcion temporal.",
                code="timeout",
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                f"No se pudo conectar con Groq: {exc}",
                code="connection_error",
            ) from exc
        self._raise_for_status(response)
        return response.json()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise ProviderError(
                "Groq rechazo la credencial. Revisa la API key y permisos del proyecto.",
                code="invalid_auth",
            )
        if response.status_code == 429:
            raise ProviderError(
                "Groq devolvio limite de uso o cuota alcanzada. Revisa rate limits y facturacion.",
                code="rate_limited",
            )
        if response.status_code >= 500:
            raise ProviderError(
                "Groq esta temporalmente indisponible.",
                code="temporary_provider_error",
            )
        if response.status_code >= 400:
            detail = self._safe_error_detail(response)
            raise ProviderError(
                f"Groq devolvio un error de solicitud: {detail}",
                code="bad_request",
            )

    def _safe_error_detail(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text[:240]
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or response.status_code)
        return str(data)[:240]
