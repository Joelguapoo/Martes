from dataclasses import dataclass
from time import perf_counter

from app.ai.base import ChatMessage, LocalModelProvider, ProviderError
from app.core.identity import MARTES_SYSTEM_PROMPT
from app.core.tone import ToneSignal, analyze_tone
from app.memory.learning import propose_memories
from app.memory.models import MemoryItem
from app.memory.store import MemoryStore
from app.research.models import ResearchResult
from app.research.web import WebResearcher

SYSTEM_PROMPT = MARTES_SYSTEM_PROMPT


@dataclass(frozen=True)
class AssistantResponse:
    answer: str | None
    notice: str | None
    used_memory: list[MemoryItem]
    proposed_memories: list[MemoryItem]
    tone: ToneSignal | None
    research: ResearchResult | None
    latency_ms: int
    model_available: bool
    model_message: str


class AssistantOrchestrator:
    def __init__(
        self,
        model: LocalModelProvider,
        memory: MemoryStore,
        researcher: WebResearcher | None,
        *,
        enable_memory: bool = True,
        remote_memory_enabled: bool = False,
        enable_tone: bool = True,
        auto_learning: bool = False,
        capabilities_context: str | None = None,
    ) -> None:
        self.model = model
        self.memory = memory
        self.researcher = researcher
        self.enable_memory = enable_memory
        self.remote_memory_enabled = remote_memory_enabled
        self.enable_tone = enable_tone
        self.auto_learning = auto_learning
        self.capabilities_context = capabilities_context or ""

    async def handle(
        self,
        user_text: str,
        *,
        history: list[ChatMessage] | None = None,
        user_id: str | None = None,
    ) -> AssistantResponse:
        started = perf_counter()
        if self._asks_capabilities(user_text) and self.capabilities_context:
            return AssistantResponse(
                answer=self._capability_answer(),
                notice=None,
                used_memory=[],
                proposed_memories=[],
                tone=None,
                research=None,
                latency_ms=int((perf_counter() - started) * 1000),
                model_available=True,
                model_message="Respuesta generada desde registro local de capacidades.",
            )
        tone = analyze_tone(user_text) if self.enable_tone else None
        status = await self.model.status()
        remote_provider = status.provider in {"groq", "remote-authorized"}
        can_use_memory = self.enable_memory and (not remote_provider or self.remote_memory_enabled)
        relevant = self.memory.search(user_text, limit=5, user_id=user_id) if can_use_memory else []
        proposals = propose_memories(user_text, self.auto_learning) if self.enable_memory else []
        for item in proposals:
            self.memory.add(item, user_id=user_id)

        research: ResearchResult | None = None
        if self._needs_research(user_text) and self.researcher is not None:
            research = await self.researcher.research(user_text)

        if status.available:
            context = self._build_context(relevant, tone, research)
            messages = [
                ChatMessage("system", SYSTEM_PROMPT),
                ChatMessage("system", context),
                *(history or [])[-10:],
                ChatMessage("user", user_text),
            ]
            try:
                answer = await self.model.complete(messages, timeout=60)
            except ProviderError as exc:
                status = type(status)(
                    False,
                    status.provider,
                    status.model,
                    exc.message,
                    status.preferred,
                    {"code": exc.code},
                )
                answer = self._provider_error_answer(status.message)
        else:
            answer = self._offline_answer(status.message, research)

        return AssistantResponse(
            answer=answer,
            notice=None if status.available else answer,
            used_memory=relevant,
            proposed_memories=proposals,
            tone=tone,
            research=research,
            latency_ms=int((perf_counter() - started) * 1000),
            model_available=status.available,
            model_message=status.message,
        )

    def _needs_research(self, text: str) -> bool:
        lowered = text.lower()
        markers = [
            "investiga",
            "busca en internet",
            "fuentes",
            "actual",
            "reciente",
            "ultimas",
            "ultimo",
            "hoy",
            "noticias",
        ]
        return any(marker in lowered for marker in markers)

    def _build_context(
        self,
        memories: list[MemoryItem],
        tone: ToneSignal | None,
        research: ResearchResult | None,
    ) -> str:
        parts: list[str] = []
        if memories:
            parts.append("Memoria pertinente:\n" + "\n".join(f"- {m.content}" for m in memories))
        if tone:
            parts.append(
                f"Senal de tono aproximada: {tone.label} "
                f"(confianza {tone.confidence:.2f}). Guia: {tone.guidance}"
            )
        if research:
            parts.append(
                "Investigacion recuperada:\n"
                + "\n".join(f"- {source.title}: {source.snippet}" for source in research.sources)
            )
        if self.capabilities_context:
            parts.append(self.capabilities_context)
        return "\n\n".join(parts) or "No hay contexto adicional pertinente."

    def _asks_capabilities(self, text: str) -> bool:
        lowered = text.lower().replace("¿", "").replace("?", "")
        return any(
            marker in lowered
            for marker in [
                "que puedes hacer",
                "qué puedes hacer",
                "cuales son tus funciones",
                "cuáles son tus funciones",
                "tus capacidades",
            ]
        )

    def _capability_answer(self) -> str:
        lines = [
            "A sus ordenes, senor. Estas son mis capacidades reales en esta instalacion:",
        ]
        for line in self.capabilities_context.splitlines():
            if line.startswith("- "):
                clean = line[2:].split(" Estado:", 1)[0]
                lines.append(f"- {clean}")
        lines.append(
            "Cuando ejecute una accion, el backend debe confirmarla antes de que yo "
            "la de por hecha."
        )
        return "\n".join(lines)

    def _offline_answer(self, model_message: str, research: ResearchResult | None) -> str:
        if research and research.sources:
            return (
                "Pude recuperar fuentes, pero el proveedor de IA no esta disponible para redactar "
                f"una sintesis completa. {model_message}"
            )
        return (
            "Todavia no puedo conversar porque el proveedor de IA no esta configurado "
            "o no esta disponible. Abre Configuracion para consultar los pasos necesarios "
            f"para activarlo. Detalle: {model_message}"
        )

    def _provider_error_answer(self, model_message: str) -> str:
        return (
            "El proveedor de IA esta configurado, pero no pudo completar esta respuesta. "
            f"Detalle: {model_message}"
        )
