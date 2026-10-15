from __future__ import annotations

import re
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
    purpose: str = ""
    appropriate_when: list[str] = field(default_factory=list)
    natural_examples: list[str] = field(default_factory=list)
    required_params: list[str] = field(default_factory=list)
    optional_params: list[str] = field(default_factory=list)
    possible_errors: list[str] = field(default_factory=list)
    verification: str = ""
    dependencies: list[str] = field(default_factory=list)
    semantic_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.name,
            "description": self.description,
            "purpose": self.purpose or self.description,
            "available": self.available,
            "permissions_required": self.permissions_required,
            "input_schema": self.input_schema,
            "required_params": self.required_params,
            "optional_params": self.optional_params,
            "risk_level": self.risk_level.name.lower(),
            "appropriate_when": self.appropriate_when,
            "natural_examples": self.natural_examples,
            "possible_errors": self.possible_errors,
            "expected_result": self.expected_result,
            "verification": self.verification or self.expected_result,
            "dependencies": self.dependencies,
            "semantic_tags": self.semantic_tags,
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
            examples = "; ".join(tool.natural_examples[:3])
            when = "; ".join(tool.appropriate_when[:2])
            lines.append(
                f"- {tool.name}: {tool.description} Estado: {state}. "
                f"Riesgo: {tool.risk_level.name.lower()}. "
                f"Permisos: {', '.join(tool.permissions_required) or 'sesion autenticada'}. "
                f"Uso: {when or tool.purpose or tool.description}. "
                f"Ejemplos naturales: {examples or 'sin ejemplos'}."
            )
        lines.append(
            "Si el usuario pregunta que puedes hacer, responde solo con capacidades de esta lista "
            "y con los servicios confirmados por el estado del sistema. No inventes accesos."
        )
        return "\n".join(lines)

    def semantic_specs(self) -> list[dict[str, Any]]:
        return [tool.to_dict() for tool in self.list() if tool.available and tool.can_execute]

    def native_tool_schemas(
        self,
        names: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        schemas: list[dict[str, Any]] = []
        native_to_real: dict[str, str] = {}
        for tool in self.list():
            if not tool.available or not tool.can_execute:
                continue
            if names is not None and tool.name not in names:
                continue
            native_name = _native_tool_name(tool.name)
            native_to_real[native_name] = tool.name
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": native_name,
                        "description": _tool_description(tool),
                        "parameters": _input_schema_to_json_schema(tool),
                    },
                }
            )
        return schemas, native_to_real

    async def run(self, name: str, payload: dict[str, Any], *, authorized: bool = False) -> Any:
        if name not in self._tools:
            raise KeyError(f"Herramienta no registrada: {name}")
        spec = self._tools[name]
        if not spec.available or not spec.can_execute:
            raise PermissionError("La herramienta no esta disponible en esta instalacion.")
        require_authorization(spec.risk_level, authorized)
        return await spec.handler(payload)


def _native_tool_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return clean[:64]


def _tool_description(tool: ToolSpec) -> str:
    parts = [tool.description]
    if tool.purpose:
        parts.append(f"Finalidad: {tool.purpose}")
    if tool.appropriate_when:
        parts.append("Usar cuando: " + "; ".join(tool.appropriate_when[:3]))
    if tool.natural_examples:
        parts.append("Ejemplos: " + "; ".join(tool.natural_examples[:3]))
    parts.append(f"Riesgo: {tool.risk_level.name.lower()}.")
    parts.append(f"Verificacion: {tool.verification or tool.expected_result}")
    return " ".join(parts)[:1000]


def _input_schema_to_json_schema(tool: ToolSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for key, description in tool.input_schema.items():
        if key in {"overwrite", "authorized"}:
            json_type = "boolean"
        elif key in {"limit", "count", "max_results"}:
            json_type = "integer"
        else:
            json_type = "string"
        properties[key] = {
            "type": json_type,
            "description": str(description),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": tool.required_params,
        "additionalProperties": False,
    }
