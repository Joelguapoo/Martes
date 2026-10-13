import httpx

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus


class RemoteCompatibleProvider(LocalModelProvider):
    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str | None,
        model: str | None,
        api_key: str | None,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url.rstrip("/") if base_url else None
        self.model = model
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "remote-authorized"

    async def status(self) -> ModelStatus:
        if not self.enabled:
            return ModelStatus(
                False,
                self.name,
                self.model or "sin configurar",
                "Proveedor remoto desactivado.",
            )
        if not self.base_url or not self.model or not self.api_key:
            return ModelStatus(
                False,
                self.name,
                self.model or "sin configurar",
                "Proveedor remoto incompleto: faltan URL, modelo o credencial.",
            )
        return ModelStatus(
            True,
            self.name,
            self.model,
            (
                "Proveedor remoto autorizado configurado. "
                "Puede implicar costes, cuotas y envio de datos."
            ),
            details={"base_url": self.base_url},
        )

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        status = await self.status()
        if not status.available:
            raise RuntimeError(status.message)
        payload = {"model": self.model, "messages": [message.__dict__ for message in messages]}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=timeout or 60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
