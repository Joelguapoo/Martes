from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.dynamic_planner import DynamicPlanner
from app.planning.executor import PlanExecutor
from app.planning.models import PlanStatus
from app.planning.store import PlanningStore
from app.reminders.store import ReminderStore
from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class CoreActionResult:
    answer: str
    conversation_id: str
    state: str = "planning"
    progress_label: str = "Preparando plan operativo..."
    done_label: str = "Plan registrado."
    visual: str = "planning"
    suggestions: list[dict[str, str]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


class MartesCore:
    def __init__(
        self,
        *,
        planning: PlanningStore,
        tools: ToolRegistry,
        reminders: ReminderStore | None = None,
    ) -> None:
        self.planning = planning
        self.tools = tools
        self.reminders = reminders
        self.executor = PlanExecutor(planning=planning, tools=tools)
        self.dynamic_planner = DynamicPlanner(tools)

    async def handle(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult | None:
        lowered = message.lower().strip()
        plan_id = _extract_plan_id(message)
        if plan_id and re.search(r"\b(continua|continúa|reanuda|ejecuta)\b", lowered):
            return await self._resume_plan(
                plan_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if _looks_like_research_report(lowered):
            return await self._create_research_report_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if _looks_like_software_structure(lowered):
            return await self._create_software_structure_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if _looks_like_project_request(lowered):
            return await self._create_project_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if _looks_like_planning_request(lowered):
            return self._create_execution_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if _asks_for_recent_work(lowered):
            return self._summarize_recent_work(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        dynamic = self.dynamic_planner.build(message, user_id=user_id)
        if dynamic:
            return await self._execute_dynamic_plan(
                dynamic,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        return None

    async def _create_project_plan(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        objective = _extract_objective(message)
        name = _title_from_objective(objective)
        tasks = _project_tasks_for(objective)
        project = self.planning.create_project(
            user_id=user_id,
            name=name,
            objective=objective,
            description=f"Proyecto creado desde conversación: {message[:240]}",
            tasks=tasks,
        )
        folder = _slug(name)
        plan = self.planning.create_plan(
            user_id=user_id,
            conversation_id=conversation_id,
            objective=f"Organizar proyecto: {objective}",
            steps=[
                {
                    "title": "Crear carpeta del proyecto",
                    "action": {"tool": "files.create_directory", "payload": {"path": folder}},
                },
                {
                    "title": "Crear README inicial",
                    "depends_on": [1],
                    "action": {
                        "tool": "files.create_text_file",
                        "payload": {
                            "path": f"{folder}/README.md",
                            "content": (
                                f"# {project['name']}\n\n"
                                f"Objetivo: {project['objective']}\n\n"
                                "## Semana 1\n\n"
                                "- Instalar entorno de practica.\n"
                                "- Repasar sintaxis basica.\n"
                                "- Crear ejercicios pequenos.\n"
                            ),
                        },
                    },
                },
                {
                    "title": "Crear plan de primera semana",
                    "depends_on": [1],
                    "action": {
                        "tool": "files.create_text_file",
                        "payload": {
                            "path": f"{folder}/semana-1.txt",
                            "content": "\n".join(task["title"] for task in project["tasks"]),
                        },
                    },
                },
                {
                    "title": "Verificar archivos del proyecto",
                    "depends_on": [1, 2, 3],
                    "action": {"tool": "files.list_directory", "payload": {"path": folder}},
                },
            ],
            status=PlanStatus.PENDING,
        )
        execution = await self.executor.run(plan["id"], user_id=user_id)
        task_lines = "\n".join(f"- {task['title']}" for task in project["tasks"])
        answer = (
            f"Proyecto creado y guardado: **{project['name']}**.\n"
            f"Objetivo: {project['objective']}.\n"
            f"Tareas iniciales:\n{task_lines}\n"
            f"\nEjecución real:\n{execution.summary}"
        )
        return CoreActionResult(
            answer=answer,
            conversation_id=conversation_id,
            state="planning",
            progress_label="Creando proyecto y tareas persistentes...",
            done_label="Proyecto ejecutado y verificado.",
            visual=execution.visual if execution.visual != "complete" else "project",
            suggestions=[
                {
                    "label": "Ver actividad",
                    "prompt": "muéstrame los trabajos recientes",
                },
                {
                    "label": "Priorizar tareas",
                    "prompt": f"prioriza las tareas del proyecto {project['name']}",
                },
            ],
            payload={"project": project, "plan": execution.plan},
        )

    async def _create_research_report_plan(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        objective = _extract_objective(message)
        folder = "informes"
        report_name = f"{_slug(objective) or 'informe'}.md"
        plan = self.planning.create_plan(
            user_id=user_id,
            conversation_id=conversation_id,
            objective=f"Investigar y guardar informe: {objective}",
            steps=[
                {
                    "title": "Crear carpeta de informes",
                    "action": {"tool": "files.create_directory", "payload": {"path": folder}},
                },
                {
                    "title": "Investigar fuentes reales",
                    "action": {"tool": "research.web", "payload": {"query": objective}},
                },
                {
                    "title": "Guardar informe con fuentes",
                    "depends_on": [1, 2],
                    "action": {
                        "tool": "files.create_text_file",
                        "payload": {
                            "path": f"{folder}/{report_name}",
                            "content_from": "research",
                        },
                    },
                },
                {
                    "title": "Verificar informe guardado",
                    "depends_on": [3],
                    "action": {"tool": "files.list_directory", "payload": {"path": folder}},
                },
            ],
            status=PlanStatus.PENDING,
        )
        execution = await self.executor.run(plan["id"], user_id=user_id)
        return CoreActionResult(
            answer=f"Investigación solicitada: **{objective}**.\n{execution.summary}",
            conversation_id=conversation_id,
            state="researching",
            progress_label="Investigando y guardando informe...",
            done_label="Investigación procesada.",
            visual="research" if not execution.failed_step else "error",
            suggestions=[{"label": "Ver actividad", "prompt": "muéstrame los trabajos recientes"}],
            payload={"plan": execution.plan},
        )

    async def _create_software_structure_plan(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        objective = _extract_objective(message)
        name = _slug(objective) or "api-python"
        plan = self.planning.create_plan(
            user_id=user_id,
            conversation_id=conversation_id,
            objective=f"Crear estructura de software: {objective}",
            steps=[
                {
                    "title": "Crear estructura inicial de proyecto",
                    "action": {"tool": "files.scaffold_project", "payload": {"name": name}},
                },
                {
                    "title": "Crear notas de pruebas",
                    "depends_on": [1],
                    "action": {
                        "tool": "files.create_text_file",
                        "payload": {
                            "path": f"{name}/docs/testing.md",
                            "content": (
                                "# Pruebas\n\n"
                                "Pendiente: registrar comandos de prueba reales "
                                "cuando exista un ejecutor autorizado.\n"
                            ),
                        },
                    },
                },
                {
                    "title": "Verificar estructura creada",
                    "depends_on": [1, 2],
                    "action": {"tool": "files.list_directory", "payload": {"path": name}},
                },
            ],
            status=PlanStatus.PENDING,
        )
        execution = await self.executor.run(plan["id"], user_id=user_id)
        return CoreActionResult(
            answer=(
                f"Estructura solicitada: **{objective}**.\n{execution.summary}\n"
                "No ejecuté pruebas de código porque aún no hay ejecutor de comandos "
                "autorizado registrado."
            ),
            conversation_id=conversation_id,
            state="executing",
            progress_label="Creando estructura de software...",
            done_label="Estructura procesada.",
            visual="code" if not execution.failed_step else "error",
            suggestions=[{"label": "Ver actividad", "prompt": "muéstrame los trabajos recientes"}],
            payload={"plan": execution.plan},
        )

    async def _resume_plan(
        self,
        plan_id: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        execution = await self.executor.run(plan_id, user_id=user_id)
        return CoreActionResult(
            answer=f"Reanudación del plan `{plan_id}`:\n{execution.summary}",
            conversation_id=conversation_id,
            state="executing",
            progress_label="Reanudando plan persistente...",
            done_label="Reanudación procesada.",
            visual=execution.visual,
            suggestions=[{"label": "Ver actividad", "prompt": "muéstrame los trabajos recientes"}],
            payload={"plan": execution.plan},
        )

    async def _execute_dynamic_plan(
        self,
        dynamic,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        if dynamic.question:
            return CoreActionResult(
                answer=dynamic.question,
                conversation_id=conversation_id,
                state="planning",
                progress_label="Faltan datos para planificar.",
                done_label="Pregunta preparada.",
                visual="planning",
            )
        if dynamic.limitation:
            return CoreActionResult(
                answer=f"No puedo completar ese plan todavía: {dynamic.limitation}",
                conversation_id=conversation_id,
                state="error",
                progress_label="Capacidad no disponible.",
                done_label="Plan detenido.",
                visual="error",
            )
        plan = self.planning.create_plan(
            user_id=user_id,
            conversation_id=conversation_id,
            objective=dynamic.objective,
            steps=dynamic.steps,
            status=PlanStatus.PENDING,
        )
        execution = await self.executor.run(plan["id"], user_id=user_id)
        return CoreActionResult(
            answer=f"Plan dinámico ejecutado: **{dynamic.objective}**.\n{execution.summary}",
            conversation_id=conversation_id,
            state="executing",
            progress_label="Ejecutando plan dinámico validado...",
            done_label="Plan dinámico procesado.",
            visual=execution.visual,
            suggestions=[{"label": "Ver actividad", "prompt": "muéstrame los trabajos recientes"}],
            payload={"plan": execution.plan},
        )

    def _create_execution_plan(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        objective = _extract_objective(message)
        tool_names = {
            tool.name
            for tool in self.tools.list()
            if tool.available and tool.can_execute
        }
        steps = [
            "Aclarar objetivo y restricciones.",
            "Identificar herramientas disponibles y permisos necesarios.",
            "Ejecutar pasos seguros y verificar resultados.",
            "Registrar resultados y próximos pasos.",
        ]
        if "research.web" in tool_names and re.search(
            r"\b(investiga|buscar|fuentes)\b",
            message,
            re.I,
        ):
            steps.insert(2, "Buscar fuentes y conservar URLs verificables.")
        plan = self.planning.create_plan(
            user_id=user_id,
            conversation_id=conversation_id,
            objective=objective,
            steps=steps,
            status=PlanStatus.PENDING,
        )
        answer = (
            f"Plan operativo registrado: **{objective}**.\n"
            "No ejecutaré acciones de riesgo sin autorización concreta. "
            f"Próxima acción: {plan['next_action']}"
        )
        return CoreActionResult(
            answer=answer,
            conversation_id=conversation_id,
            state="planning",
            progress_label="Descomponiendo objetivo en pasos...",
            done_label="Plan persistente preparado.",
            visual="planning",
            suggestions=[
                {"label": "Ver actividad", "prompt": "muéstrame los trabajos recientes"},
                {"label": "Continuar plan", "prompt": f"continúa el plan {plan['id']}"},
            ],
            payload={"plan": plan},
        )

    def _summarize_recent_work(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        projects = self.planning.list_projects(user_id=user_id, limit=5)
        plans = self.planning.list_plans(user_id=user_id, limit=5)
        activity = self.planning.list_activity(user_id=user_id, limit=5)
        reminders = self.reminders.upcoming(user_id=user_id, limit=5) if self.reminders else []
        project_line = ", ".join(item["name"] for item in projects) or "sin proyectos"
        plan_line = ", ".join(item["objective"] for item in plans) or "sin planes"
        reminder_line = ", ".join(item["message"] for item in reminders) or "sin recordatorios"
        answer = (
            "Estado reciente del sistema:\n"
            f"- Proyectos: {project_line}.\n"
            f"- Planes: {plan_line}.\n"
            f"- Recordatorios: {reminder_line}.\n"
            f"- Eventos registrados: {len(activity)}."
        )
        return CoreActionResult(
            answer=answer,
            conversation_id=conversation_id,
            state="verifying",
            progress_label="Consultando actividad persistente...",
            done_label="Actividad recuperada.",
            visual="activity",
            payload={
                "projects": projects,
                "plans": plans,
                "activity": activity,
                "reminders": reminders,
            },
        )


def _looks_like_project_request(lowered: str) -> bool:
    return bool(re.search(r"\b(crea|crear|organiza|prepara)\b.*\bproyecto\b", lowered))


def _looks_like_research_report(lowered: str) -> bool:
    return bool(re.search(r"\b(investiga|investigar)\b", lowered) and "informe" in lowered)


def _looks_like_software_structure(lowered: str) -> bool:
    return bool(
        re.search(r"\b(crea|crear|prepara)\b", lowered)
        and re.search(r"\b(estructura|api|software)\b", lowered)
    )


def _looks_like_planning_request(lowered: str) -> bool:
    return bool(re.search(r"\b(planifica|planea|prepara un plan|objetivo complejo)\b", lowered))


def _asks_for_recent_work(lowered: str) -> bool:
    return bool(
        re.search(
            r"\b(trabajos recientes|actividad|planes recientes|proyectos)\b",
            lowered,
        )
    )


def _extract_objective(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", message).strip(" .")
    match = re.search(r"\b(?:para|sobre|de)\s+(.+)$", cleaned, re.I)
    return (match.group(1) if match else cleaned).strip(" .")


def _extract_plan_id(message: str) -> str | None:
    match = re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        message,
        re.I,
    )
    return match.group(0) if match else None


def _title_from_objective(objective: str) -> str:
    words = objective.split()
    title = " ".join(words[:7]).strip()
    return title[:80] or "Proyecto MARTES"


def _project_tasks_for(objective: str) -> list[str]:
    return [
        f"Definir alcance de {objective}",
        "Reunir recursos y restricciones importantes",
        "Dividir el trabajo en hitos semanales",
        "Revisar avance y ajustar prioridades",
    ]


def _slug(value: str) -> str:
    text = value.lower()
    text = re.sub(r"[^a-z0-9áéíóúñü]+", "-", text, flags=re.I).strip("-")
    return text[:70] or "proyecto"
