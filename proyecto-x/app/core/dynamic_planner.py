from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.tools.registry import ToolRegistry


class PlannedStep(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    action: dict[str, Any]
    depends_on: list[int] = Field(default_factory=list)

    @field_validator("action")
    @classmethod
    def action_has_tool(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value.get("tool"):
            raise ValueError("El paso necesita una herramienta registrada.")
        if "payload" not in value:
            value["payload"] = {}
        return value


@dataclass(frozen=True)
class DynamicPlan:
    objective: str
    steps: list[dict[str, Any]]
    question: str | None = None
    limitation: str | None = None


class DynamicPlanner:
    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    def build(self, message: str, *, user_id: str) -> DynamicPlan | None:
        available = {tool.name for tool in self.tools.list() if tool.available and tool.can_execute}
        lowered = message.lower()
        objective = re.sub(r"\s+", " ", message).strip(" .")
        try:
            if _asks_for_reminder(lowered):
                return self._reminder_plan(objective, user_id=user_id, available=available)
            if _asks_for_document_summary(lowered):
                return self._document_summary_plan(objective, available=available)
            if _asks_for_markdown_report(lowered):
                return self._markdown_report_plan(objective, available=available)
            if _asks_for_file_search(lowered):
                return self._file_search_plan(objective, available=available)
        except ValidationError as exc:
            return DynamicPlan(objective=objective, steps=[], limitation=str(exc))
        return None

    def _reminder_plan(
        self,
        objective: str,
        *,
        user_id: str,
        available: set[str],
    ) -> DynamicPlan:
        if "reminders.create" not in available:
            return DynamicPlan(
                objective=objective,
                steps=[],
                limitation="La herramienta reminders.create no está disponible.",
            )
        parsed = parse_spanish_reminder(objective)
        if parsed is None:
            return DynamicPlan(
                objective=objective,
                steps=[],
                question="¿Para qué fecha y hora exactas quiere el recordatorio?",
            )
        step = PlannedStep(
            title="Crear recordatorio persistente",
            action={
                "tool": "reminders.create",
                "payload": {
                    "user_id": user_id,
                    "message": parsed["message"],
                    "remind_at": parsed["remind_at"],
                    "timezone": parsed["timezone"],
                },
            },
        )
        return DynamicPlan(objective=objective, steps=[step.model_dump()])

    def _document_summary_plan(self, objective: str, *, available: set[str]) -> DynamicPlan:
        if "documents.summarize" not in available:
            return DynamicPlan(
                objective=objective,
                steps=[],
                limitation="La herramienta documents.summarize no está disponible.",
            )
        path = _extract_path(objective)
        if not path:
            return DynamicPlan(
                objective=objective,
                steps=[],
                question="¿Qué archivo dentro del workspace quiere resumir?",
            )
        step = PlannedStep(
            title="Resumir documento local autorizado",
            action={"tool": "documents.summarize", "payload": {"path": path}},
        )
        return DynamicPlan(objective=objective, steps=[step.model_dump()])

    def _markdown_report_plan(self, objective: str, *, available: set[str]) -> DynamicPlan:
        if "documents.create_markdown" not in available:
            return DynamicPlan(
                objective=objective,
                steps=[],
                limitation="La herramienta documents.create_markdown no está disponible.",
            )
        title = _extract_report_title(objective)
        path = f"informes/{_slug(title)}.md"
        step = PlannedStep(
            title="Crear informe Markdown",
            action={
                "tool": "documents.create_markdown",
                "payload": {
                    "path": path,
                    "title": title,
                        "body": (
                            "Informe creado por MARTES. Complete el contenido "
                            "o pida una investigación."
                        ),
                },
            },
        )
        return DynamicPlan(objective=objective, steps=[step.model_dump()])

    def _file_search_plan(self, objective: str, *, available: set[str]) -> DynamicPlan:
        if "files.search" not in available:
            return DynamicPlan(
                objective=objective,
                steps=[],
                limitation="La herramienta files.search no está disponible.",
            )
        query = _extract_search_query(objective)
        if not query:
            return DynamicPlan(objective=objective, steps=[], question="¿Qué nombre desea buscar?")
        step = PlannedStep(
            title="Buscar archivos por nombre",
            action={"tool": "files.search", "payload": {"path": ".", "query": query}},
        )
        return DynamicPlan(objective=objective, steps=[step.model_dump()])


def parse_spanish_reminder(message: str) -> dict[str, str] | None:
    timezone = "America/Bogota"
    tzinfo = _safe_timezone(timezone)
    now = datetime.now(tzinfo)
    lowered = message.lower()
    if "mañana" in lowered or "manana" in lowered:
        day = now.date() + timedelta(days=1)
    elif "hoy" in lowered:
        day = now.date()
    else:
        return None
    hour = _extract_hour(lowered)
    if hour is None:
        return None
    remind_at = datetime.combine(day, time(hour)).replace(tzinfo=tzinfo)
    content = re.sub(r"(?i)^.*?recu[eé]rdame\s+", "", message).strip(" .")
    content = re.sub(
        r"(?i)\b(mañana|manana|hoy)\b.*?(terminar|hacer|revisar|llamar|.+)$",
        r"\2",
        content,
    )
    content = content.strip(" .") or message
    return {"message": content, "remind_at": remind_at.isoformat(), "timezone": timezone}


def _asks_for_reminder(lowered: str) -> bool:
    return "recuérdame" in lowered or "recuerdame" in lowered


def _asks_for_document_summary(lowered: str) -> bool:
    return bool(
        re.search(r"\b(resume|resumir|analiza|analizar)\b", lowered)
        and _extract_path(lowered)
    )


def _asks_for_markdown_report(lowered: str) -> bool:
    return bool("informe" in lowered and ("markdown" in lowered or ".md" in lowered))


def _asks_for_file_search(lowered: str) -> bool:
    return bool(re.search(r"\b(busca|buscar|encuentra)\b.*\b(archivo|archivos)\b", lowered))


def _extract_path(text: str) -> str | None:
    match = re.search(r"([\wÁÉÍÓÚáéíóúñÑüÜ./-]+\.(?:txt|md|json|csv))", text)
    return match.group(1).strip().replace("\\", "/") if match else None


def _extract_report_title(text: str) -> str:
    match = re.search(r"(?:sobre|de)\s+(.+?)(?:\s+en\s+markdown|\.md|$)", text, re.I)
    return (match.group(1) if match else "Informe MARTES").strip(" .")


def _extract_search_query(text: str) -> str | None:
    match = re.search(r"(?:llamado|llamada|nombre|archivo)\s+['\"]?([^'\"\n]+?)['\"]?$", text, re.I)
    if match:
        return match.group(1).strip()
    words = text.split()
    return words[-1] if words else None


def _extract_hour(lowered: str) -> int | None:
    words = {
        "una": 1,
        "dos": 2,
        "tres": 3,
        "cuatro": 4,
        "cinco": 5,
        "seis": 6,
        "siete": 7,
        "ocho": 8,
        "nueve": 9,
        "diez": 10,
        "once": 11,
        "doce": 12,
    }
    numeric = re.search(r"\b(?:a\s+las\s+)?(\d{1,2})(?::\d{2})?\b", lowered)
    if numeric:
        hour = int(numeric.group(1))
    else:
        match = re.search(r"a\s+las\s+(\w+)", lowered)
        hour = words.get(match.group(1)) if match else None
    if hour is None:
        return None
    if (
        "p. m" in lowered or "pm" in lowered or "tarde" in lowered or "noche" in lowered
    ) and hour < 12:
        hour += 12
    return hour if 0 <= hour <= 23 else None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70] or "informe"


def _safe_timezone(key: str):
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=-5), name=key)
