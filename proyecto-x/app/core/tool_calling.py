from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.ai.base import LocalModelProvider
from app.core.intent import IntentResolution
from app.security.permissions import PermissionError
from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolCallEvent:
    tool: str
    native_name: str
    ok: bool
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    verified: bool = False


@dataclass(frozen=True)
class ToolCallingResult:
    answer: str
    tool_events: list[ToolCallEvent]
    model_calls: int
    latency_ms: int
    stopped_reason: str


class ToolCallingEngine:
    def __init__(
        self,
        *,
        model: LocalModelProvider,
        tools: ToolRegistry,
        max_iterations: int = 4,
        max_result_chars: int = 8000,
    ) -> None:
        self.model = model
        self.tools = tools
        self.max_iterations = max(1, max_iterations)
        self.max_result_chars = max_result_chars

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        intent: IntentResolution,
        user_id: str,
        conversation_id: str | None = None,
        timeout: float | None = None,
    ) -> ToolCallingResult | None:
        if not self.model.supports_native_tools:
            return None
        started = perf_counter()
        candidate_names = {
            candidate.name for candidate in intent.candidate_tools if candidate.confidence >= 0.6
        }
        if not candidate_names:
            return None
        schemas, native_to_real = self.tools.native_tool_schemas(candidate_names)
        if not schemas:
            return None

        working_messages = [dict(item) for item in messages]
        events: list[ToolCallEvent] = []
        model_calls = 0
        stopped_reason = "final"
        for _ in range(self.max_iterations):
            model_calls += 1
            response = await self.model.complete_with_tools(
                working_messages,
                schemas,
                tool_choice="auto",
                timeout=timeout,
            )
            message = dict(response["message"])
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return ToolCallingResult(
                    answer=str(message.get("content") or "").strip(),
                    tool_events=events,
                    model_calls=model_calls,
                    latency_ms=int((perf_counter() - started) * 1000),
                    stopped_reason=stopped_reason,
                )
            working_messages.append(_assistant_tool_message(message))
            for call in tool_calls:
                event = await self._execute_tool_call(
                    call,
                    native_to_real=native_to_real,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                events.append(event)
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": event.native_name,
                        "content": _safe_json(
                            {
                                "ok": event.ok,
                                "tool": event.tool,
                                "result": event.result,
                                "error": event.error,
                                "verified": event.verified,
                            },
                            self.max_result_chars,
                        ),
                    }
                )
        stopped_reason = "max_iterations"
        return ToolCallingResult(
            answer=_fallback_answer(events),
            tool_events=events,
            model_calls=model_calls,
            latency_ms=int((perf_counter() - started) * 1000),
            stopped_reason=stopped_reason,
        )

    async def _execute_tool_call(
        self,
        call: dict[str, Any],
        *,
        native_to_real: dict[str, str],
        user_id: str,
        conversation_id: str | None,
    ) -> ToolCallEvent:
        function = call.get("function") if isinstance(call, dict) else None
        native_name = str((function or {}).get("name") or "")
        tool_name = native_to_real.get(native_name)
        if not tool_name:
            return ToolCallEvent(
                tool=native_name or "desconocida",
                native_name=native_name,
                ok=False,
                error="Herramienta no registrada o no permitida para esta solicitud.",
            )
        try:
            arguments = _parse_arguments((function or {}).get("arguments"))
        except ValueError as exc:
            return ToolCallEvent(
                tool=tool_name,
                native_name=native_name,
                ok=False,
                error=str(exc),
            )
        if tool_name.startswith(("reminders.", "tasks.", "projects.")):
            arguments["user_id"] = user_id
        if tool_name.startswith("tasks.") and conversation_id:
            arguments["conversation_id"] = conversation_id
        try:
            result = await self.tools.run(tool_name, arguments, authorized=False)
        except (PermissionError, ValueError, KeyError) as exc:
            return ToolCallEvent(
                tool=tool_name,
                native_name=native_name,
                ok=False,
                arguments=arguments,
                error=str(exc),
            )
        except Exception as exc:
            return ToolCallEvent(
                tool=tool_name,
                native_name=native_name,
                ok=False,
                arguments=arguments,
                error=str(exc),
            )
        verified = _verify_basic(tool_name, result)
        return ToolCallEvent(
            tool=tool_name,
            native_name=native_name,
            ok=verified,
            arguments=arguments,
            result=result if isinstance(result, dict) else {"value": result},
            verified=verified,
            error=None if verified else "No pude verificar el resultado de la herramienta.",
        )


def _assistant_tool_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls") or [],
    }


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if raw in {None, ""}:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        raise ValueError("Argumentos de herramienta con formato invalido.")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Argumentos de herramienta no son JSON valido.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Los argumentos de herramienta deben ser un objeto JSON.")
    return parsed


def _safe_json(payload: dict[str, Any], max_chars: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    trimmed = dict(payload)
    trimmed["result"] = "Resultado truncado por limite de seguridad."
    return json.dumps(trimmed, ensure_ascii=False, default=str)[:max_chars]


def _verify_basic(tool_name: str, result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if tool_name.endswith("create_directory"):
        return bool(result.get("created") and result.get("path"))
    if tool_name.endswith("create_text_file"):
        return bool(result.get("created") and result.get("path"))
    if tool_name.endswith("list_directory"):
        return "items" in result and bool(result.get("path"))
    if tool_name.startswith("research."):
        return bool(result.get("sources"))
    if tool_name == "projects.list":
        return "projects" in result
    if tool_name == "tasks.list":
        return "tasks" in result and bool(result.get("project"))
    if tool_name == "tasks.find":
        return "matches" in result and bool(result.get("project"))
    if tool_name == "tasks.get":
        return bool(result.get("task"))
    if tool_name == "tasks.create":
        return bool(result.get("created") and result.get("task"))
    if tool_name in {"tasks.update", "tasks.update_status"}:
        return bool(result.get("updated") and result.get("verified") and result.get("task"))
    if tool_name.startswith("weather."):
        return "temperature" in result
    if tool_name.startswith("documents."):
        return bool(result.get("path"))
    if tool_name.startswith("reminders.create"):
        return bool(result.get("created") and result.get("reminder", {}).get("id"))
    return True


def _fallback_answer(events: list[ToolCallEvent]) -> str:
    if not events:
        return "No pude completar la llamada de herramientas."
    completed = [event for event in events if event.ok]
    failed = [event for event in events if not event.ok]
    parts = []
    if completed:
        names = ", ".join(event.tool for event in completed)
        parts.append(f"Herramientas ejecutadas y verificadas: {names}.")
    if failed:
        names = ", ".join(f"{event.tool}: {event.error}" for event in failed)
        parts.append(f"Pendiente por revisar: {names}.")
    return " ".join(parts)
