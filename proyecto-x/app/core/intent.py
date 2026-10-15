from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from app.ai.base import ChatMessage
from app.memory.conversation import ConversationMessage
from app.memory.models import MemoryItem
from app.security.permissions import RiskLevel
from app.tools.registry import ToolRegistry


class IntentType(StrEnum):
    CONVERSATION = "conversation"
    INFORMATION = "information_question"
    MEMORY_QUERY = "memory_query"
    CAPABILITIES = "capabilities_query"
    RESEARCH = "research_request"
    FILE_ACTION = "file_action"
    PROJECT_MANAGEMENT = "project_management"
    TASK_MANAGEMENT = "task_management"
    PLANNING = "planning_request"
    PLAN_EXECUTION = "plan_execution"
    ANALYSIS = "analysis_decision"
    PROGRAMMING = "programming_operation"
    COMBINED = "combined_capabilities"
    CORRECTION = "correction"
    AUTHORIZATION_REQUIRED = "authorization_required"
    AMBIGUOUS = "ambiguous_or_unsupported"


class ToolCandidate(BaseModel):
    name: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class ConversationReference(BaseModel):
    kind: str
    value: str
    source: str = "recent_history"


class IntentDiagnostics(BaseModel):
    stage_ms: dict[str, int] = Field(default_factory=dict)
    model_calls: int = 0
    resolver: str = "local_semantic"
    notes: list[str] = Field(default_factory=list)


class IntentResolution(BaseModel):
    original_message: str = ""
    intent_type: IntentType
    primary_goal: str = ""
    user_goal: str
    subgoals: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    verification_requirements: list[str] = Field(default_factory=list)
    required_outputs: list[dict[str, Any]] = Field(default_factory=list)
    referenced_context: list[ConversationReference] = Field(default_factory=list)
    candidate_tools: list[ToolCandidate] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_level: str = "query"
    clarification_required: bool = False
    expected_outcome: str = ""
    diagnostics: IntentDiagnostics = Field(default_factory=IntentDiagnostics)

    def has_tool(self, name: str) -> bool:
        return any(candidate.name == name for candidate in self.candidate_tools)

    def is_type(self, *types: IntentType) -> bool:
        return self.intent_type in types

    def safe_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data.pop("diagnostics", None)
        return data


class IntentResolver:
    """Resuelve intenciones en una estructura validada antes de ejecutar herramientas.

    La capa local cubre solicitudes frecuentes y de bajo riesgo sin consumir modelo. Las
    solicitudes abiertas quedan estructuradas para que MartesCore/orquestador decidan.
    """

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    def resolve(
        self,
        message: str,
        *,
        history: list[ConversationMessage | ChatMessage] | None = None,
        memories: list[MemoryItem] | None = None,
    ) -> IntentResolution:
        started = perf_counter()
        normalized = _normalize(message)
        tool_names = {
            tool.name for tool in self.tools.list() if tool.available and tool.can_execute
        }
        references = _references_from_history(history or [])
        candidates: list[ToolCandidate] = []
        subgoals: list[str] = []
        entities: dict[str, Any] = {}
        constraints: list[str] = []
        verification_requirements: list[str] = _verification_requirements(normalized)
        required_outputs: list[dict[str, Any]] = []
        missing: list[str] = []
        notes: list[str] = []

        if memories:
            constraints.extend(_memory_constraints(memories))

        if _asks_capabilities(normalized):
            candidates = [
                ToolCandidate(name=tool.name, reason="capacidad disponible", confidence=0.65)
                for tool in self.tools.list()
                if tool.available and tool.can_execute
            ][:8]
            return self._finish(
                IntentResolution(
                    intent_type=IntentType.CAPABILITIES,
                    user_goal="Conocer las capacidades reales disponibles.",
                    candidate_tools=candidates,
                    expected_outcome="Explicar capacidades implementadas sin inventar accesos.",
                ),
                started,
            )

        if _looks_risky(normalized):
            risk_tools = _candidate_existing(tool_names, ["files.rename"])
            return self._finish(
                IntentResolution(
                    intent_type=IntentType.AUTHORIZATION_REQUIRED,
                    user_goal=message.strip(),
                    candidate_tools=risk_tools,
                    risk_level="consequence",
                    clarification_required=True,
                    missing_information=["autorizacion explicita para la operacion sensible"],
                    expected_outcome="Pedir autorizacion concreta antes de actuar.",
                ),
                started,
            )

        folder_name = _extract_folder_name(message, normalized)
        text_file_name = _extract_text_file_name(message)
        text_content = _extract_text_content(message)
        referenced_folder = _referenced_folder(normalized, references)
        path = _extract_path(message)
        query = _extract_search_query(message)

        if folder_name:
            entities["folder_name"] = folder_name
        if referenced_folder:
            entities["referenced_folder"] = referenced_folder
        if text_file_name:
            entities["file_name"] = text_file_name
        if text_content:
            entities["content"] = text_content
        if path:
            entities["path"] = path
        if query:
            entities["query"] = query
        if _looks_business_workspace(normalized):
            folder_name = folder_name or "ideas-negocio"
            text_file_name = text_file_name or "ideas-iniciales.txt"
            text_content = text_content or _business_workspace_template()
            entities.update(
                {
                    "folder_name": folder_name,
                    "file_name": text_file_name,
                    "content": text_content,
                    "workspace_kind": "business_ideas",
                }
            )
            required_outputs.extend(
                [
                    {"type": "directory", "name": folder_name},
                    {"type": "text_file", "name": text_file_name},
                ]
            )
        objective_count = _extract_objective_count(normalized)
        if objective_count:
            entities["objective_count"] = objective_count
            required_outputs.append(
                {"type": "text_file", "name": text_file_name or "objetivos.txt"}
            )

        file_intent = _looks_file_action(normalized)
        if file_intent:
            if entities.get("workspace_kind") == "business_ideas":
                candidates.extend(
                    _candidate_existing(
                        tool_names,
                        ["files.create_directory", "files.create_text_file"],
                        reason="preparar espacio de ideas con archivo inicial",
                        confidence=0.9,
                    )
                )
            elif _looks_analysis(normalized):
                candidates.extend(
                    _candidate_existing(
                        tool_names,
                        ["files.list_directory", "documents.summarize", "files.search"],
                        reason="analisis o revision de archivos/carpetas",
                        confidence=0.82,
                    )
                )
                if not (folder_name or referenced_folder or path):
                    missing.append("archivo o carpeta a revisar")
            elif _looks_text_creation(normalized):
                candidates.extend(
                    _candidate_existing(
                        tool_names,
                        ["files.create_text_file", "files.create_directory"],
                        reason="crear o escribir archivo de texto",
                        confidence=0.88,
                    )
                )
                if not (folder_name or referenced_folder or path):
                    missing.append("carpeta destino o ruta del archivo")
            elif _looks_project_scaffold(normalized):
                candidates.extend(
                    _candidate_existing(
                        tool_names,
                        ["files.scaffold_project", "files.create_directory"],
                        reason="preparar estructura inicial de trabajo",
                        confidence=0.78,
                    )
                )
            else:
                candidates.extend(
                    _candidate_existing(
                        tool_names,
                        ["files.create_directory"],
                        reason="crear espacio, carpeta o directorio autorizado",
                        confidence=0.86,
                    )
                )
                if not folder_name:
                    folder_name = _infer_folder_name_from_goal(normalized)
                    if folder_name:
                        entities["folder_name"] = folder_name
                if not folder_name:
                    missing.append("nombre de la carpeta")

        if _looks_research(normalized):
            subgoals.append("investigar fuentes o informacion externa")
            candidates.extend(
                _candidate_existing(
                    tool_names,
                    ["research.web"],
                    reason="solicitud de busqueda o investigacion",
                    confidence=0.84,
                )
            )
        if _looks_weather(normalized):
            candidates.extend(
                _candidate_existing(
                    tool_names,
                    ["weather.current"],
                    reason="consulta de clima",
                    confidence=0.88,
                )
            )
        if _looks_reminder(normalized):
            candidates.extend(
                _candidate_existing(
                    tool_names,
                    ["reminders.create", "reminders.list"],
                    reason="gestion de recordatorios",
                    confidence=0.82,
                )
            )
        if _looks_task_management(normalized):
            subgoals.append("consultar o modificar una tarea persistente")
            task_tools = ["tasks.list", "tasks.find"]
            if _looks_task_completion(normalized):
                entities["requested_task_status"] = "DONE"
                task_tools.append("tasks.update_status")
            elif re.search(r"\b(crea|crear|agrega|anade|añade)\b", normalized):
                task_tools.append("tasks.create")
            elif re.search(r"\b(cambia|modifica|edita|prioridad|vencimiento)\b", normalized):
                task_tools.append("tasks.update")
            candidates.extend(
                _candidate_existing(
                    tool_names,
                    task_tools,
                    reason="gestión persistente de tareas y proyectos",
                    confidence=0.9,
                )
            )
        if _looks_document(normalized):
            candidates.extend(
                _candidate_existing(
                    tool_names,
                    ["documents.summarize", "documents.read_text", "documents.create_markdown"],
                    reason="lectura, resumen o creacion de documento",
                    confidence=0.78,
                )
            )
        if _looks_project_management(normalized):
            subgoals.append("crear o gestionar proyecto persistente")
            candidates.extend(
                _candidate_existing(
                    tool_names,
                    ["files.scaffold_project", "files.create_directory"],
                    reason="organizacion de proyecto",
                    confidence=0.72,
                )
            )
        if _looks_planning(normalized):
            subgoals.append("descomponer el objetivo en pasos verificables")
        if _looks_correction(normalized):
            subgoals.append("modificar la ultima instruccion o resultado")
            notes.append("referencia contextual de correccion detectada")

        candidates = _dedupe_candidates(candidates)
        intent_type = _intent_type_for(normalized, candidates, subgoals, file_intent)
        namespaces = {candidate.name.split(".", 1)[0] for candidate in candidates}
        if len(namespaces) > 1 and not (file_intent and namespaces <= {"files", "documents"}):
            intent_type = IntentType.COMBINED
        clarification_required = bool(missing and intent_type != IntentType.CONVERSATION)
        if clarification_required:
            intent_type = IntentType.AMBIGUOUS

        resolution = IntentResolution(
            original_message=message,
            intent_type=intent_type,
            primary_goal=_primary_goal(message),
            user_goal=_goal_text(message),
            subgoals=subgoals,
            entities=entities,
            constraints=constraints,
            verification_requirements=verification_requirements,
            required_outputs=required_outputs,
            referenced_context=references,
            candidate_tools=candidates,
            missing_information=missing,
            risk_level=_highest_risk(candidates, self.tools),
            clarification_required=clarification_required,
            expected_outcome=_expected_outcome(intent_type, candidates, entities),
            diagnostics=IntentDiagnostics(notes=notes),
        )
        return self._finish(resolution, started)

    def _finish(self, resolution: IntentResolution, started: float) -> IntentResolution:
        resolution.diagnostics.stage_ms["intent_resolver"] = int((perf_counter() - started) * 1000)
        return resolution


def _normalize(text: str) -> str:
    lowered = text.lower().replace("¿", "").replace("?", "")
    stripped = "".join(
        char for char in unicodedata.normalize("NFD", lowered) if unicodedata.category(char) != "Mn"
    )
    stripped = re.sub(r"\s+", " ", stripped).strip()
    corrections = {
        "acrpeta": "carpeta",
        "llamda": "llamada",
        "direcotrio": "directorio",
        "archvio": "archivo",
        "analis": "analiza",
        "analizame": "analiza",
        "preparame": "prepara",
        "montame": "monta",
        "dejame": "deja",
    }
    for wrong, right in corrections.items():
        stripped = re.sub(rf"\b{wrong}\b", right, stripped)
    return stripped


def _candidate_existing(
    available: set[str],
    names: list[str],
    *,
    reason: str = "herramienta candidata",
    confidence: float = 0.7,
) -> list[ToolCandidate]:
    return [
        ToolCandidate(name=name, reason=reason, confidence=confidence)
        for name in names
        if name in available
    ]


def _dedupe_candidates(candidates: list[ToolCandidate]) -> list[ToolCandidate]:
    best: dict[str, ToolCandidate] = {}
    for candidate in candidates:
        current = best.get(candidate.name)
        if current is None or candidate.confidence > current.confidence:
            best[candidate.name] = candidate
    return sorted(best.values(), key=lambda item: item.confidence, reverse=True)


def _references_from_history(
    history: list[ConversationMessage | ChatMessage],
) -> list[ConversationReference]:
    references: list[ConversationReference] = []
    for item in reversed(history[-12:]):
        content = getattr(item, "content", "")
        role = getattr(item, "role", "")
        for match in re.finditer(r"([A-Za-z]:\\[^\n|]+?)(?:\.|\s\||$)", content):
            value = match.group(1).strip()
            kind = "file" if re.search(r"\.[a-z0-9]{1,8}$", value, re.I) else "folder"
            references.append(
                ConversationReference(kind=kind, value=value, source=role or "history")
            )
        if len(references) >= 5:
            break
    return references


def _memory_constraints(memories: list[MemoryItem]) -> list[str]:
    return [f"memoria pertinente: {item.content[:160]}" for item in memories[:3]]


def _goal_text(message: str) -> str:
    return re.sub(r"\s+", " ", message).strip(" .")[:500]


def _primary_goal(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", message).strip(" .")
    cleaned = re.sub(
        r"\b(comprueba|verifica|confirma|asegurate|asegúrate)\b.*$",
        "",
        cleaned,
        flags=re.I,
    ).strip(" .,")
    return cleaned[:300] or _goal_text(message)


def _verification_requirements(normalized: str) -> list[str]:
    requirements = []
    if re.search(
        r"\b(comprueba|verifica|confirma|asegurate|guardado|quedo guardado)\b",
        normalized,
    ):
        requirements.append("verificar existencia y contenido de los artefactos creados")
    if re.search(r"\b(enlaces originales|urls|fuentes)\b", normalized):
        requirements.append("conservar enlaces originales de las fuentes")
    return requirements


def _looks_business_workspace(normalized: str) -> bool:
    return bool(
        re.search(r"\b(espacio|sitio|carpeta|lugar)\b", normalized)
        and re.search(r"\b(ideas?|negocio|emprendimiento)\b", normalized)
        and re.search(r"\b(necesito|prepara|preparame|organiza|comenzar|empezar)\b", normalized)
    )


def _business_workspace_template() -> str:
    return (
        "# Ideas iniciales del negocio\n\n"
        "## Problema que quiero resolver\n\n"
        "- \n\n"
        "## Cliente ideal\n\n"
        "- \n\n"
        "## Primera oferta o producto\n\n"
        "- \n\n"
        "## Próximos pasos\n\n"
        "1. Escribir tres ideas de negocio.\n"
        "2. Elegir la más simple de validar.\n"
        "3. Definir una acción pequeña para esta semana.\n"
    )


def _extract_objective_count(normalized: str) -> int | None:
    if re.search(r"\btres\s+objetivos\b", normalized):
        return 3
    match = re.search(r"\b(\d{1,2})\s+objetivos\b", normalized)
    if not match:
        return None
    count = int(match.group(1))
    return count if 1 <= count <= 10 else None


def _looks_file_action(normalized: str) -> bool:
    action = re.search(
        r"\b(crea|crear|haz|hazme|hacer|prepara|preparar|monta|organiza|arma|deja|"
        r"necesito|guarda|guardar|lee|leer|analiza|revisa|busca|encuentra|echa|echale)\b",
        normalized,
    )
    target = re.search(
        r"\b(carpeta|directorio|archivo|txt|texto|documento|workspace|espacio|sitio|estructura|material)\b",
        normalized,
    )
    return bool(action and target)


def _looks_text_creation(normalized: str) -> bool:
    return bool(
        re.search(r"\b(crea|crear|genera|generar|haz|prepara|escribe|guardar|guarda)\b", normalized)
        and re.search(r"\b(archivo|txt|texto|nota|documento)\b", normalized)
    )


def _looks_analysis(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(analiza|analizar|revisa|revisar|escanea|explora|mira|vistazo|lee|echale)\b",
            normalized,
        )
        and re.search(r"\b(archivo|archivos|carpeta|directorio|documento|folder)\b", normalized)
    )


def _looks_project_scaffold(normalized: str) -> bool:
    return bool(
        re.search(r"\b(estructura|andamiaje|plantilla|base inicial)\b", normalized)
        and re.search(r"\b(proyecto|software|api|app|web)\b", normalized)
    )


def _looks_research(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(investiga|investigar|buscame|busca|fuentes|internet|recursos|alternativas)\b",
            normalized,
        )
    )


def _looks_weather(normalized: str) -> bool:
    return bool(re.search(r"\b(clima|temperatura|lluvia|pronostico)\b", normalized))


def _looks_reminder(normalized: str) -> bool:
    return bool(re.search(r"\b(recuerdame|recordatorio|avisame|agenda)\b", normalized))


def _looks_document(normalized: str) -> bool:
    return bool(re.search(r"\b(documento|informe|markdown|csv|json|resumen|resume)\b", normalized))


def _looks_project_management(normalized: str) -> bool:
    return bool(
        re.search(r"\b(proyecto|tareas|hitos|semana|trabajo)\b", normalized)
        and re.search(r"\b(crea|crear|organiza|prepara|planifica|monta|arma)\b", normalized)
    )


def _looks_task_management(normalized: str) -> bool:
    return bool(
        re.search(r"\b(tarea|tareas|pendiente|pendientes)\b", normalized)
        and re.search(
            r"\b(marca|marcar|completa|completada|completado|termine|terminé|"
            r"termina|terminar|lista|listar|muestra|busca|crear|crea|agrega|anade|añade|"
            r"cambia|modifica|edita)\b",
            normalized,
        )
    )


def _looks_task_completion(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(marca|marcar|completa|completada|completado|termine|terminé|"
            r"termina|terminar|finalice|finalicé|hecho|lista)\b",
            normalized,
        )
    )


def _looks_planning(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(planifica|planea|plan|pasos|organiza|prioriza|continua|reanuda)\b",
            normalized,
        )
    )


def _looks_correction(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(eso no|cambia|corrige|modifica|mejor|igual pero|ultima parte|dejalo como)\b",
            normalized,
        )
    )


def _looks_risky(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(borra|borrar|elimina|eliminar|sobrescribe|sobrescribir|"
            r"mueve todo|instala|desinstala)\b",
            normalized,
        )
    )


def _asks_capabilities(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(que puedes hacer|cuales son tus funciones|tus capacidades|"
            r"que herramientas tienes)\b",
            normalized,
        )
    )


def _extract_folder_name(message: str, normalized: str) -> str | None:
    patterns = [
        r"(?:carpeta|directorio)\s+(?:en\s+(?:el\s+)?(?:escritorio|desktop)\s+)?(?:llamada|con\s+nombre|que\s+se\s+llame)\s+['\"]?([^'\"\n,]+?)(?:['\"]?(?:,|\s+dentro\b|\s+y\s+dentro\b|$))",
        r"(?:haz|crea|prepara|monta|arma)\w*\s+(?:una\s+)?(?:carpeta|directorio|espacio|sitio)\s+(?:para|del|de)\s+(?:el\s+|mi\s+)?([^,\n]+)",
        r"(?:necesito|quiero)\s+(?:una\s+)?(?:carpeta|directorio|espacio|sitio)\s+(?:de\s+trabajo\s+)?(?:para|del|de)\s+(?:el\s+|mi\s+)?([^,\n]+)",
        r"(?:carpeta|directorio)\s+([A-Za-zÁÉÍÓÚáéíóúñÑüÜ0-9][^,\n]+)$",
        r"(?:se\s+llame|llamada)\s+['\"]?([^'\"\n,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            return _clean_folder_name(match.group(1))
    if _looks_project_scaffold(normalized):
        match = re.search(r"(?:proyecto|api|app|web)\s+(?:de|para)?\s*([^,\n]+)?", message, re.I)
        if match and match.group(1):
            return _clean_folder_name(match.group(1))
    return None


def _infer_folder_name_from_goal(normalized: str) -> str | None:
    patterns = [
        r"\bproyecto\s+([a-z0-9 ñü]+)$",
        r"\bmaterial\s+de\s+la\s+([a-z0-9 ñü]+)$",
        r"\barchivos\s+del\s+proyecto\s+([a-z0-9 ñü]+)$",
        r"\bpara\s+(?:el\s+|mi\s+)?([a-z0-9 ñü]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return _clean_folder_name(match.group(1))
    if "web" in normalized:
        return "web"
    return None


def _clean_folder_name(value: str) -> str | None:
    cleaned = re.sub(r"^(?:para|del|de)\s+(?:el\s+|mi\s+)?", "", value.strip(), flags=re.I)
    value = cleaned
    cleaned = re.sub(
        r"\b(en\s+el\s+)?(escritorio|desktop)\b",
        "",
        value,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\b(dentro|crea|crear|archivo|txt|texto|y quiero|que diga).*$",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = cleaned.strip(" .:/\\")
    if not cleaned:
        return None
    return cleaned[:80]


def _extract_text_file_name(message: str) -> str | None:
    match = re.search(
        r"(?:archivo|txt|texto|nota)(?:\s+(?:llamado|llamada|con\s+nombre))?\s+['\"]?([^'\"\n,]+?\.txt)['\"]?(?:,|\s+con\s+|\s+en\s+|\s+y\s+|$)",
        message,
        re.I,
    )
    if not match:
        return None
    name = match.group(1).strip().replace("\\", "/").split("/")[-1]
    return name if name.lower().endswith(".txt") else f"{name}.txt"


def _extract_text_content(message: str) -> str | None:
    match = re.search(
        r"(?:diga|que\s+diga|con\s+el\s+texto|contenido|escriba)\s+['\"]?(.+?)['\"]?\s*$",
        message,
        re.I,
    )
    return match.group(1).strip() if match else None


def _extract_path(message: str) -> str | None:
    match = re.search(r"([\wÁÉÍÓÚáéíóúñÑüÜ ./\\-]+\.(?:txt|md|json|csv))", message)
    return match.group(1).strip().replace("\\", "/") if match else None


def _extract_search_query(message: str) -> str | None:
    match = re.search(
        r"(?:busca|encuentra).*?(?:archivo|archivos).*?(?:llamado|nombre|de)\s+['\"]?([^'\"\n]+)",
        message,
        re.I,
    )
    return match.group(1).strip() if match else None


def _referenced_folder(normalized: str, references: list[ConversationReference]) -> str | None:
    if not re.search(r"\b(esa carpeta|la carpeta anterior|ahi|alli|ese directorio)\b", normalized):
        return None
    for reference in references:
        if reference.kind == "folder":
            return _desktop_relative(reference.value)
        if reference.kind == "file":
            return _desktop_relative(str(reference.value).rsplit("\\", 1)[0])
    return None


def _desktop_relative(path: str) -> str:
    parts = re.split(r"[\\/]+", path)
    lowered = [part.lower() for part in parts]
    if "desktop" in lowered:
        index = lowered.index("desktop")
        return "/".join(parts[index + 1 :]) or parts[-1]
    return parts[-1]


def _intent_type_for(
    normalized: str,
    candidates: list[ToolCandidate],
    subgoals: list[str],
    file_intent: bool,
) -> IntentType:
    if file_intent:
        return IntentType.FILE_ACTION
    names = {candidate.name for candidate in candidates}
    if "weather.current" in names:
        return IntentType.INFORMATION
    if "research.web" in names:
        return IntentType.RESEARCH
    if "reminders.create" in names or "reminders.list" in names:
        return IntentType.TASK_MANAGEMENT
    if any(name.startswith("tasks.") or name == "projects.list" for name in names):
        return IntentType.TASK_MANAGEMENT
    if any(name.startswith("documents.") for name in names):
        return IntentType.INFORMATION
    if _looks_project_management(normalized):
        return IntentType.PROJECT_MANAGEMENT
    if _looks_planning(normalized) or subgoals:
        return IntentType.PLANNING
    if re.search(r"\b(python|codigo|programa|script|api|software)\b", normalized):
        return IntentType.PROGRAMMING
    if re.search(r"\b(que|como|por que|cuando|donde|cual)\b", normalized):
        return IntentType.INFORMATION
    return IntentType.CONVERSATION


def _highest_risk(candidates: list[ToolCandidate], tools: ToolRegistry) -> str:
    by_name = {tool.name: tool for tool in tools.list()}
    order = {
        RiskLevel.QUERY.name.lower(): 0,
        RiskLevel.LOCAL_REVERSIBLE.name.lower(): 1,
        RiskLevel.CONSEQUENCE.name.lower(): 2,
        RiskLevel.DESTRUCTIVE_OR_SENSITIVE.name.lower(): 3,
    }
    current = "query"
    for candidate in candidates:
        spec = by_name.get(candidate.name)
        if not spec:
            continue
        risk = spec.risk_level.name.lower()
        if order.get(risk, 0) > order.get(current, 0):
            current = risk
    return current


def _expected_outcome(
    intent_type: IntentType,
    candidates: list[ToolCandidate],
    entities: dict[str, Any],
) -> str:
    if intent_type == IntentType.FILE_ACTION:
        if "content" in entities:
            return "Crear/actualizar recursos de archivo y verificar ruta y contenido."
        return "Ejecutar la operacion de archivo solicitada y verificar el resultado."
    if intent_type == IntentType.COMBINED:
        return "Coordinar varias capacidades mediante herramientas o un plan verificable."
    if candidates:
        return "Seleccionar herramienta real, validar parametros y usar su resultado verificado."
    return "Responder de forma natural sin afirmar acciones no ejecutadas."
