import asyncio
from pathlib import Path

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus


class LlamaCppProvider(LocalModelProvider):
    def __init__(self, executable: Path | None, model_path: Path | None) -> None:
        self.executable = executable
        self.model_path = model_path

    @property
    def name(self) -> str:
        return "llama.cpp"

    async def status(self) -> ModelStatus:
        if self.executable is None:
            return ModelStatus(
                False,
                self.name,
                "sin configurar",
                "Ruta de llama.cpp no configurada.",
            )
        if self.model_path is None:
            return ModelStatus(
                False,
                self.name,
                "sin modelo",
                "Ruta del modelo GGUF no configurada.",
            )
        if not self.executable.exists():
            return ModelStatus(
                False,
                self.name,
                str(self.model_path),
                f"No existe el ejecutable: {self.executable}",
                details={"executable": str(self.executable), "model_path": str(self.model_path)},
            )
        if not self.model_path.exists():
            return ModelStatus(
                False,
                self.name,
                str(self.model_path),
                f"No existe el modelo GGUF: {self.model_path}",
                details={"executable": str(self.executable), "model_path": str(self.model_path)},
            )
        return ModelStatus(
            True,
            self.name,
            self.model_path.name,
            "llama.cpp local configurado.",
            details={"executable": str(self.executable), "model_path": str(self.model_path)},
        )

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        status = await self.status()
        if not status.available:
            raise RuntimeError(status.message)
        prompt = self._prompt(messages)
        process = await asyncio.create_subprocess_exec(
            str(self.executable),
            "-m",
            str(self.model_path),
            "-p",
            prompt,
            "-n",
            "512",
            "--no-display-prompt",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout or 90)
        except TimeoutError:
            process.kill()
            raise TimeoutError("llama.cpp supero el tiempo limite de generacion.") from None
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="ignore").strip() or "llama.cpp fallo.")
        return stdout.decode(errors="ignore").strip()

    def _prompt(self, messages: list[ChatMessage]) -> str:
        prompt = "\n".join(f"{message.role.upper()}: {message.content}" for message in messages)
        return f"{prompt}\nASSISTANT:"
