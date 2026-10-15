from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.reminders.store import ReminderStore
from app.security.permissions import RiskLevel
from app.tools.registry import ToolRegistry, ToolSpec


class ReminderToolError(ValueError):
    pass


@dataclass(frozen=True)
class ReminderAgent:
    store: ReminderStore

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or "").strip()
        message = str(payload.get("message") or "").strip()
        remind_at_raw = str(payload.get("remind_at") or "").strip()
        timezone = str(payload.get("timezone") or "America/Bogota").strip()
        if not user_id:
            raise ReminderToolError("Usuario requerido para crear recordatorio.")
        if not message:
            raise ReminderToolError("Mensaje requerido para crear recordatorio.")
        try:
            remind_at = datetime.fromisoformat(remind_at_raw)
        except ValueError as exc:
            raise ReminderToolError("Fecha ISO invalida para el recordatorio.") from exc
        item = self.store.create(
            user_id=user_id,
            message=message,
            remind_at=remind_at,
            timezone=timezone,
            recurrence=payload.get("recurrence"),
        )
        return {"created": True, "reminder": item}

    async def list(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            raise ReminderToolError("Usuario requerido para consultar recordatorios.")
        return {"reminders": self.store.list(user_id=user_id)}

    async def upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            raise ReminderToolError("Usuario requerido para consultar recordatorios.")
        return {"reminders": self.store.upcoming(user_id=user_id)}

    async def cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or "").strip()
        reminder_id = str(payload.get("id") or "").strip()
        if not user_id or not reminder_id:
            raise ReminderToolError("Usuario e id requeridos para cancelar recordatorio.")
        return {"cancelled": self.store.cancel(reminder_id, user_id=user_id)}

    async def done(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id") or "").strip()
        reminder_id = str(payload.get("id") or "").strip()
        if not user_id or not reminder_id:
            raise ReminderToolError("Usuario e id requeridos para completar recordatorio.")
        return {"done": self.store.mark_done(reminder_id, user_id=user_id)}


def register_reminder_tools(registry: ToolRegistry, agent: ReminderAgent) -> None:
    common = {
        "permissions_required": ["sesion autenticada", "propietario del recordatorio"],
        "expected_result": "Recordatorio persistido o actualizado en SQLite.",
    }
    registry.register(
        ToolSpec(
            "reminders.create",
            "Crear un recordatorio persistente dentro de MARTES.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.create,
            input_schema={"message": "texto", "remind_at": "fecha ISO", "timezone": "zona"},
            required_params=["message", "remind_at"],
            optional_params=["timezone"],
            purpose="Guardar un recordatorio local con fecha y hora.",
            appropriate_when=["El usuario pide que se le recuerde o avise algo."],
            natural_examples=["Recuérdame mañana a las ocho llamar", "Avísame hoy revisar esto"],
            possible_errors=["Fecha ISO invalida", "Falta mensaje", "Falta usuario"],
            verification="Confirmar id del recordatorio creado en SQLite.",
            semantic_tags=["recordatorio", "agenda", "avisar"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "reminders.list",
            "Consultar recordatorios persistentes del usuario.",
            RiskLevel.QUERY,
            agent.list,
            input_schema={},
            purpose="Consultar recordatorios persistentes del usuario.",
            appropriate_when=["El usuario pregunta por sus recordatorios."],
            natural_examples=["Qué recordatorios tengo", "Muéstrame mis avisos"],
            verification="Devolver lista de recordatorios del usuario autenticado.",
            semantic_tags=["recordatorio", "listar"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "reminders.upcoming",
            "Consultar próximos recordatorios pendientes.",
            RiskLevel.QUERY,
            agent.upcoming,
            input_schema={},
            purpose="Consultar proximos recordatorios pendientes.",
            appropriate_when=["El usuario pregunta qué viene pronto o qué tiene pendiente."],
            natural_examples=["Qué tengo pendiente", "Próximos recordatorios"],
            verification="Devolver lista de recordatorios próximos.",
            semantic_tags=["recordatorio", "pendiente"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "reminders.cancel",
            "Cancelar un recordatorio del usuario.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.cancel,
            input_schema={"id": "id del recordatorio"},
            required_params=["id"],
            purpose="Cancelar un recordatorio existente del usuario.",
            appropriate_when=["El usuario pide cancelar o quitar un recordatorio."],
            natural_examples=["Cancela ese recordatorio", "Quita el aviso de mañana"],
            possible_errors=["Falta id", "No existe o no pertenece al usuario"],
            verification="Confirmar estado cancelled.",
            semantic_tags=["recordatorio", "cancelar"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "reminders.done",
            "Marcar un recordatorio como atendido.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.done,
            input_schema={"id": "id del recordatorio"},
            required_params=["id"],
            purpose="Marcar un recordatorio como atendido.",
            appropriate_when=["El usuario indica que ya terminó o atendió un aviso."],
            natural_examples=["Ya hice eso", "Marca el recordatorio como listo"],
            possible_errors=["Falta id", "No existe o no pertenece al usuario"],
            verification="Confirmar estado done.",
            semantic_tags=["recordatorio", "completar"],
            **common,
        )
    )
