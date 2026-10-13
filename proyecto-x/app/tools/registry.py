from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.security.permissions import RiskLevel, require_authorization

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk_level: RiskLevel
    handler: ToolHandler
    available: bool = True
    permissions_required: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    expected_result: str = "Resultado verificable devuelto por el backend."
    can_execute: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.name,
            "description": self.description,
            "available": self.available,
            "permissions_required": self.permissions_required,
            "input_schema": self.input_schema,
            "risk_level": self.risk_level.name.lower(),
            "expected_result": self.expected_result,
            "can_execute": self.can_execute,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def public_specs(self) -> list[dict[str, Any]]:
        return [tool.to_dict() for tool in self.list()]

    def capability_context(self) -> str:
        lines = [
            "Capacidades reales habilitadas por el backend:",
        ]
        for tool in self.list():
            state = "disponible" if tool.available and tool.can_execute else "no disponible"
            lines.append(
                f"- {tool.name}: {tool.description} Estado: {state}. "
                f"Riesgo: {tool.risk_level.name.lower()}. "
                f"Permisos: {', '.join(tool.permissions_required) or 'sesion autenticada'}."
            )
        lines.append(
            "Si el usuario pregunta que puedes hacer, responde solo con capacidades de esta lista "
            "y con los servicios confirmados por el estado del sistema. No inventes accesos."
        )
        return "\n".join(lines)

    async def run(self, name: str, payload: dict[str, Any], *, authorized: bool = False) -> Any:
        if name not in self._tools:
            raise KeyError(f"Herramienta no registrada: {name}")
        spec = self._tools[name]
        if not spec.available or not spec.can_execute:
            raise PermissionError("La herramienta no esta disponible en esta instalacion.")
        require_authorization(spec.risk_level, authorized)
        return await spec.handler(payload)
