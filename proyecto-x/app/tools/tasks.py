from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.planning.models import TaskStatus
from app.planning.store import PlanningStore
from app.security.permissions import RiskLevel
from app.tools.registry import ToolRegistry, ToolSpec


class TaskToolError(ValueError):
    pass


@dataclass(frozen=True)
class TaskAgent:
    store: PlanningStore

    def _user_id(self, payload: dict[str, Any]) -> str:
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            raise TaskToolError("Usuario autenticado requerido para gestionar tareas.")
        return user_id

    def _project(self, payload: dict[str, Any], user_id: str) -> dict[str, Any]:
        project_id = str(payload.get("project_id") or "").strip()
        project_name = str(payload.get("project_name") or "").strip()
        if project_id:
            project = self.store.get_project(project_id, user_id=user_id)
            if project is None:
                raise TaskToolError("El proyecto no existe o no pertenece al usuario.")
            return project
        matches = self.store.find_projects_by_name(user_id=user_id, query=project_name)
        if not matches:
            raise TaskToolError("No encontré un proyecto con ese nombre.")
        if len(matches) > 1:
            raise TaskToolError("Hay varios proyectos con ese nombre; indique un identificador.")
        return matches[0]

    async def list_projects(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self._user_id(payload)
        return {"projects": self.store.list_projects(user_id=user_id)}

    async def list_tasks(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self._user_id(payload)
        project = self._project(payload, user_id)
        return {
            "project": project,
            "tasks": self.store.list_project_tasks(project["id"], user_id=user_id),
        }

    async def find(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self._user_id(payload)
        title = str(payload.get("title") or "").strip()
        if not title:
            raise TaskToolError("Título de tarea requerido para buscarla.")
        project = self._project(payload, user_id)
        matches = self.store.find_tasks_by_name(
            user_id=user_id,
            project_id=project["id"],
            query=title,
        )
        return {"project": project, "matches": matches}

    async def get(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self._user_id(payload)
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise TaskToolError("Identificador de tarea requerido.")
        task = self.store.get_task(task_id, user_id=user_id)
        if task is None:
            raise TaskToolError("La tarea no existe o no pertenece al usuario.")
        return {"task": task}

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = self._user_id(payload)
        title = str(payload.get("title") or "").strip()
        if not title:
            raise TaskToolError("Título de tarea requerido.")
        project = self._project(payload, user_id)
        task = self.store.create_task(
            user_id=user_id,
            project_id=project["id"],
            title=title,
            description=str(payload.get("description") or "").strip(),
            priority=_priority(payload.get("priority")),
            due_at=_due_at(payload.get("due_at")),
        )
        if task is None:
            raise TaskToolError("No pude guardar la tarea en el proyecto.")
        self._activity("task_created", "Tarea creada", task, payload, user_id)
        return {"created": True, "task": task, "project": project}

    async def update_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._update(payload, status_required=True)

    async def update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._update(payload, status_required=False)

    async def _update(self, payload: dict[str, Any], *, status_required: bool) -> dict[str, Any]:
        user_id = self._user_id(payload)
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise TaskToolError("Identificador de tarea requerido.")
        status_raw = payload.get("status")
        if status_required and not status_raw:
            raise TaskToolError("Estado requerido para actualizar la tarea.")
        status = _status(status_raw) if status_raw else None
        task = self.store.update_task(
            task_id,
            user_id=user_id,
            title=_optional_text(payload, "title"),
            description=_optional_text(payload, "description"),
            priority=_priority(payload.get("priority")) if "priority" in payload else None,
            due_at=_due_at(payload.get("due_at")) if "due_at" in payload else None,
            status=status,
        )
        if task is None:
            raise TaskToolError("No pude actualizar una tarea inexistente o ajena.")
        verified = self.store.get_task(task_id, user_id=user_id)
        if verified is None or (status is not None and verified["status"] != status.value):
            raise TaskToolError("La actualización no quedó verificada en SQLite.")
        self._activity("task_updated", "Tarea modificada", verified, payload, user_id)
        self._activity(
            "task_verified", "Modificación de tarea verificada", verified, payload, user_id
        )
        return {"updated": True, "task": verified, "verified": True}

    def _activity(
        self,
        kind: str,
        title: str,
        task: dict[str, Any],
        payload: dict[str, Any],
        user_id: str,
    ) -> None:
        self.store.record_activity(
            user_id=user_id,
            conversation_id=_optional_text(payload, "conversation_id"),
            plan_id=_optional_text(payload, "plan_id"),
            project_id=task.get("project_id"),
            kind=kind,
            title=title,
            status=str(task["status"]),
            detail=str(task["title"]),
            payload={"task_id": task["id"], "project_id": task.get("project_id")},
        )


def register_task_tools(registry: ToolRegistry, agent: TaskAgent) -> None:
    common = {
        "permissions_required": ["sesión autenticada", "propietario del proyecto y la tarea"],
        "expected_result": "Resultado persistido y comprobado en SQLite.",
    }
    registry.register(
        ToolSpec(
            "projects.list",
            "Consultar los proyectos persistentes del usuario.",
            RiskLevel.QUERY,
            agent.list_projects,
            input_schema={},
            purpose="Listar proyectos propios.",
            appropriate_when=["El usuario pregunta por sus proyectos."],
            natural_examples=["Qué proyectos tengo", "Muéstrame mis proyectos"],
            verification="Devuelve únicamente proyectos del usuario autenticado.",
            semantic_tags=["proyecto", "listar"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "tasks.list",
            "Consultar las tareas persistentes de un proyecto.",
            RiskLevel.QUERY,
            agent.list_tasks,
            input_schema={"project_id": "id opcional", "project_name": "nombre del proyecto"},
            optional_params=["project_id", "project_name"],
            purpose="Listar tareas de un proyecto propio.",
            appropriate_when=["El usuario pregunta qué tareas tiene un proyecto."],
            natural_examples=["Qué tareas tiene mi proyecto", "Lista las tareas de Estudio Python"],
            verification="Devuelve tareas del proyecto resuelto de forma inequívoca.",
            semantic_tags=["tarea", "proyecto", "listar"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "tasks.find",
            "Buscar una tarea por título exacto normalizado dentro de un proyecto.",
            RiskLevel.QUERY,
            agent.find,
            input_schema={
                "project_id": "id opcional",
                "project_name": "nombre",
                "title": "título de tarea",
            },
            required_params=["title"],
            optional_params=["project_id", "project_name"],
            purpose="Resolver una tarea sin modificar una coincidencia ambigua.",
            appropriate_when=["El usuario identifica una tarea concreta en un proyecto."],
            natural_examples=["Busca la tarea revisar recursos en Estudio Python"],
            possible_errors=["Proyecto inexistente", "Tarea inexistente", "Coincidencias ambiguas"],
            verification="Devuelve todas las coincidencias exactas normalizadas.",
            semantic_tags=["tarea", "buscar", "proyecto"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "tasks.get",
            "Consultar el estado actual de una tarea persistente.",
            RiskLevel.QUERY,
            agent.get,
            input_schema={"task_id": "id de la tarea"},
            required_params=["task_id"],
            purpose="Verificar una tarea por su identificador.",
            appropriate_when=["Se necesita comprobar un cambio de tarea."],
            natural_examples=["Comprueba el estado de esa tarea"],
            verification="Devuelve la tarea reconsultada desde SQLite.",
            semantic_tags=["tarea", "verificar"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "tasks.create",
            "Crear una tarea persistente dentro de un proyecto propio.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.create,
            input_schema={
                "project_id": "id opcional",
                "project_name": "nombre",
                "title": "título",
                "description": "descripción opcional",
                "priority": "low, normal o high",
                "due_at": "fecha ISO opcional",
            },
            required_params=["title"],
            optional_params=["project_id", "project_name", "description", "priority", "due_at"],
            purpose="Crear una tarea en el proyecto indicado.",
            appropriate_when=["El usuario pide añadir una tarea."],
            natural_examples=["Agrega una tarea a mi proyecto"],
            verification="Devuelve la tarea creada desde SQLite.",
            semantic_tags=["tarea", "crear", "proyecto"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "tasks.update_status",
            "Cambiar y verificar el estado de una tarea persistente.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.update_status,
            input_schema={
                "task_id": "id de la tarea",
                "status": "BACKLOG, TODO, IN_PROGRESS, BLOCKED, DONE o CANCELLED",
            },
            required_params=["task_id", "status"],
            purpose="Marcar una tarea como completada u otro estado válido.",
            appropriate_when=["El usuario termina, inicia o bloquea una tarea identificada."],
            natural_examples=["Marca esta tarea como completada", "Pon la tarea en progreso"],
            verification="Reconsulta SQLite y confirma el estado solicitado.",
            semantic_tags=["tarea", "completar", "estado"],
            **common,
        )
    )
    registry.register(
        ToolSpec(
            "tasks.update",
            "Modificar el título, descripción, prioridad, fecha o estado de una tarea.",
            RiskLevel.LOCAL_REVERSIBLE,
            agent.update,
            input_schema={
                "task_id": "id de la tarea",
                "title": "título opcional",
                "description": "descripción opcional",
                "priority": "low, normal o high",
                "due_at": "fecha ISO opcional",
                "status": "estado opcional",
            },
            required_params=["task_id"],
            optional_params=["title", "description", "priority", "due_at", "status"],
            purpose="Editar campos existentes de una tarea propia.",
            appropriate_when=["El usuario cambia detalles de una tarea."],
            natural_examples=["Cambia la prioridad de esta tarea a alta"],
            verification="Devuelve la tarea reconsultada después de actualizar.",
            semantic_tags=["tarea", "editar", "prioridad"],
            **common,
        )
    )


def _status(value: Any) -> TaskStatus:
    try:
        return TaskStatus(str(value).strip().upper())
    except ValueError as exc:
        raise TaskToolError("Estado de tarea inválido.") from exc


def _priority(value: Any) -> str:
    priority = str(value or "normal").strip().lower()
    if priority not in {"low", "normal", "high"}:
        raise TaskToolError("Prioridad inválida; use low, normal o high.")
    return priority


def _due_at(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise TaskToolError("La fecha de vencimiento debe usar formato ISO.") from exc
    return text


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    return str(payload[key]).strip() if key in payload and payload[key] is not None else None
