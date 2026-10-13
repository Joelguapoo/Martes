from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai.base import ModelStatus
from app.memory.conversation import ConversationMessage, ConversationStore
from app.memory.store import MemoryStore


@dataclass(frozen=True)
class Briefing:
    title: str
    subtitle: str
    generated_at: datetime
    summary: str
    last_conversation: dict[str, str | None]
    system: dict[str, object]
    timeline: list[dict[str, str]]
    priorities: list[str]
    memory_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "generated_at": self.generated_at.isoformat(),
            "summary": self.summary,
            "last_conversation": self.last_conversation,
            "system": self.system,
            "timeline": self.timeline,
            "priorities": self.priorities,
            "memory_count": self.memory_count,
        }


class BriefingService:
    def __init__(
        self,
        conversations: ConversationStore,
        memory: MemoryStore,
        timezone: str = "America/Bogota",
    ) -> None:
        self.conversations = conversations
        self.memory = memory
        try:
            self.timezone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            self.timezone = timezone_fixed_bogota()

    def build(
        self,
        *,
        model_status: ModelStatus,
        provider_statuses: list[ModelStatus],
        backend_available: bool,
        web_enabled: bool,
        voice_available: bool,
    ) -> Briefing:
        now = datetime.now(self.timezone)
        start = datetime.combine(now.date(), time.min, tzinfo=self.timezone).astimezone(UTC)
        today_messages = self.conversations.since(start.isoformat())
        recent = self.conversations.recent(limit=40)
        user_messages = [message for message in today_messages if message.role == "user"]
        assistant_messages = [message for message in today_messages if message.role == "assistant"]
        notices = [message for message in today_messages if message.kind == "notice"]
        memories = self.memory.list(include_discarded=False)
        last_user = next((message for message in reversed(recent) if message.role == "user"), None)

        if today_messages:
            summary = (
                f"Hoy hay {len(user_messages)} mensajes del usuario, "
                f"{len(assistant_messages)} respuestas de IA y "
                f"{len(notices)} avisos tecnicos registrados."
            )
        else:
            summary = "Aun no hay actividad conversacional registrada para hoy."

        timeline = [
            {
                "time": message.created_at.astimezone(self.timezone).strftime("%H:%M"),
                "type": self._label(message),
                "text": self._compact(message.content),
            }
            for message in today_messages[-8:]
            if not (message.kind == "notice" and "modelo de IA local" in message.content)
        ]

        priorities = []
        if not model_status.available:
            priorities.append(
                "Activar un proveedor de IA local para habilitar conversacion generativa."
            )
        if not memories:
            priorities.append(
                "Agregar recuerdos utiles desde Memoria para personalizar el asistente."
            )
        if not today_messages:
            priorities.append(
                "Iniciar una conversacion o registrar una tarea para enriquecer el briefing."
            )
        if not priorities:
            priorities.append("Continuar desde el ultimo mensaje registrado.")

        return Briefing(
            title="JARVIS DAILY BRIEFING",
            subtitle="Resumen personal del dia",
            generated_at=now,
            summary=summary,
            last_conversation={
                "time": (
                    last_user.created_at.astimezone(self.timezone).isoformat()
                    if last_user
                    else None
                ),
                "last_user_message": last_user.content if last_user else None,
                "status": "modelo disponible" if model_status.available else "modelo offline",
            },
            system={
                "backend_available": backend_available,
                "active_provider": model_status.provider,
                "model": model_status.model,
                "model_available": model_status.available,
                "model_message": model_status.message,
                "providers": [status.__dict__ for status in provider_statuses],
                "web_enabled": web_enabled,
                "voice_available": voice_available,
                "memory_available": True,
            },
            timeline=timeline,
            priorities=priorities,
            memory_count=len(memories),
        )

    def _label(self, message: ConversationMessage) -> str:
        if message.kind == "notice":
            return "aviso"
        if message.role == "user":
            return "usuario"
        if message.role == "assistant":
            return "asistente"
        return "sistema"

    def _compact(self, text: str) -> str:
        clean = " ".join(text.split())
        return clean[:180] + ("..." if len(clean) > 180 else "")


def timezone_fixed_bogota() -> timezone:
    return timezone(timedelta(hours=-5), name="America/Bogota")
