from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.dynamic_planner import DynamicPlanner
from app.core.intent import IntentResolution, IntentType
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
        intent: IntentResolution | None = None,
    ) -> CoreActionResult | None:
        lowered = message.lower().strip()
        if _looks_like_task_update_request(lowered):
            return await self._update_task_from_request(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if _looks_like_project_tasks_query(lowered):
            return self._answer_project_tasks(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if intent and intent.intent_type == IntentType.PROJECT_MANAGEMENT:
            return await self._create_project_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if intent and intent.intent_type == IntentType.PLANNING:
            return self._create_execution_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if (
            intent
            and intent.intent_type == IntentType.COMBINED
            and intent.has_tool("research.web")
            and _looks_like_research_study_project(lowered)
        ):
            return await self._create_research_study_project_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if (
            intent
            and intent.intent_type == IntentType.COMBINED
            and intent.has_tool("research.web")
            and (intent.has_tool("documents.create_markdown") or "informe" in lowered)
        ):
            return await self._create_research_report_plan(
                message,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        plan_id = _extract_plan_id(message)
        if plan_id and re.search(r"\b(continua|continúa|reanuda|ejecuta)\b", lowered):
            return await self._resume_plan(
                plan_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        if _looks_like_research_report(lowered):
            if _looks_like_research_study_project(lowered):
                return await self._create_research_study_project_plan(
                    message,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
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

    async def _update_task_from_request(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        project_name = _extract_task_project_name(message)
        task_title = _extract_task_title(message)
        if not project_name:
            return CoreActionResult(
                answer=(
                    "Para actualizar una tarea necesito identificar el proyecto de forma "
                    "inequívoca."
                ),
                conversation_id=conversation_id,
                state="planning",
                progress_label="Identificando tarea...",
                done_label="Falta información.",
                visual="error",
            )
        if task_title:
            try:
                found = await self.tools.run(
                    "tasks.find",
                    {
                        "user_id": user_id,
                        "project_name": project_name,
                        "title": task_title,
                        "conversation_id": conversation_id,
                    },
                )
            except (ValueError, KeyError, PermissionError) as exc:
                return self._task_error(str(exc), conversation_id)
            matches = found.get("matches") or []
            if not matches:
                return self._task_error(
                    f"No encontré la tarea **{task_title}** en el proyecto **{project_name}**.",
                    conversation_id,
                )
            if len(matches) > 1:
                options = "\n".join(f"- {task['id']}: {task['title']}" for task in matches)
                return self._task_error(
                    "Hay varias tareas con ese nombre; indique cuál desea modificar:\n" + options,
                    conversation_id,
                )
            task = matches[0]
        elif _references_first_task(message):
            try:
                listed = await self.tools.run(
                    "tasks.list",
                    {"user_id": user_id, "project_name": project_name},
                )
            except (ValueError, KeyError, PermissionError) as exc:
                return self._task_error(str(exc), conversation_id)
            tasks = listed.get("tasks") or []
            if not tasks:
                return self._task_error(
                    f"El proyecto **{project_name}** no tiene tareas para completar.",
                    conversation_id,
                )
            task = tasks[0]
        else:
            return self._task_error(
                "Indique el título de la tarea o una referencia inequívoca, por ejemplo "
                "“la primera tarea”.",
                conversation_id,
            )
        try:
            updated = await self.tools.run(
                "tasks.update_status",
                {
                    "user_id": user_id,
                    "task_id": task["id"],
                    "status": "DONE",
                    "conversation_id": conversation_id,
                },
            )
            verified = await self.tools.run(
                "tasks.get",
                {"user_id": user_id, "task_id": task["id"]},
            )
        except (ValueError, KeyError, PermissionError) as exc:
            return self._task_error(str(exc), conversation_id)
        saved = verified.get("task") or {}
        if not updated.get("verified") or saved.get("status") != "DONE":
            return self._task_error(
                "No pude verificar que el cambio quedara guardado; la tarea no fue confirmada "
                "como completada.",
                conversation_id,
            )
        return CoreActionResult(
            answer=(
                f"Listo. Marqué como completada la tarea **{saved['title']}** del proyecto "
                f"**{saved.get('project_name') or project_name}** y verifiqué el estado "
                "`DONE` en SQLite."
            ),
            conversation_id=conversation_id,
            state="verifying",
            progress_label="Actualizando tarea persistente...",
            done_label="Tarea completada y verificada.",
            visual="project",
            payload={"task": saved},
        )

    def _task_error(self, answer: str, conversation_id: str) -> CoreActionResult:
        return CoreActionResult(
            answer=answer,
            conversation_id=conversation_id,
            state="error",
            progress_label="No se modificó ninguna tarea.",
            done_label="Operación detenida.",
            visual="error",
        )

    def _answer_project_tasks(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        project_name = _extract_project_name_query(message)
        project = self.planning.find_project_by_name(user_id=user_id, query=project_name)
        if not project:
            known = self.planning.list_projects(user_id=user_id, limit=5)
            known_line = ", ".join(item["name"] for item in known) or "ninguno registrado"
            return CoreActionResult(
                answer=(
                    f"No encontré un proyecto llamado **{project_name}** en su memoria local. "
                    f"Proyectos disponibles: {known_line}."
                ),
                conversation_id=conversation_id,
                state="verifying",
                progress_label="Buscando proyecto persistente...",
                done_label="Consulta finalizada.",
                visual="activity",
                payload={"project_name": project_name, "projects": known},
            )

        tasks = project.get("tasks") or []
        if tasks:
            task_lines = "\n".join(f"- [{task['status']}] {task['title']}" for task in tasks)
        else:
            task_lines = "- Este proyecto aún no tiene tareas guardadas."
        answer = (
            f"El proyecto **{project['name']}** tiene {len(tasks)} tarea(s) guardadas:\n"
            f"{task_lines}"
        )
        return CoreActionResult(
            answer=answer,
            conversation_id=conversation_id,
            state="verifying",
            progress_label="Consultando tareas del proyecto...",
            done_label="Tareas recuperadas.",
            visual="activity",
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
            payload={"project": project},
        )

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

    async def _create_research_study_project_plan(
        self,
        message: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> CoreActionResult:
        objective = _extract_python_learning_objective(message)
        tasks = [
            "Revisar los tres recursos gratuitos seleccionados.",
            "Completar una lección introductoria de Python.",
            "Practicar sintaxis básica con ejercicios cortos.",
            "Crear un mini ejercicio de repaso.",
            "Anotar dudas y próximos temas para la siguiente semana.",
        ]
        folder = "informes"
        report_name = "recursos-python.md"
        plan = self.planning.create_plan(
            user_id=user_id,
            conversation_id=conversation_id,
            objective=f"Investigar recursos gratuitos para Python y guardar informe: {objective}",
            steps=[
                {
                    "title": "Crear proyecto de estudio con cinco tareas",
                    "action": {
                        "tool": "internal.create_study_project",
                        "payload": {
                            "name": "Estudio Python - Semana 1",
                            "objective": objective,
                            "description": (
                                f"Proyecto de estudio creado desde conversación: {message[:240]}"
                            ),
                            "tasks": tasks,
                        },
                    },
                },
                {
                    "title": "Verificar persistencia del proyecto y sus cinco tareas",
                    "depends_on": [1],
                    "action": {
                        "tool": "internal.verify_project_tasks",
                        "payload": {"expected_tasks": 5},
                    },
                },
                {
                    "title": "Crear carpeta de informes",
                    "action": {"tool": "files.create_directory", "payload": {"path": folder}},
                },
                {
                    "title": "Investigar tres recursos gratuitos",
                    "action": {
                        "tool": "research.web",
                        "payload": {
                            "query": "tres recursos gratuitos para aprender Python",
                            "min_sources": 3,
                        },
                    },
                },
                {
                    "title": "Validar resultados recuperados",
                    "depends_on": [4],
                    "action": {
                        "tool": "internal.validate_research_sources",
                        "payload": {"min_sources": 3},
                    },
                },
                {
                    "title": "Comparar fuentes verificadas",
                    "depends_on": [5],
                    "action": {"tool": "internal.compare_research_sources", "payload": {}},
                },
                {
                    "title": "Guardar informe comparativo con enlaces originales",
                    "depends_on": [3, 6],
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
                    "depends_on": [7],
                    "action": {"tool": "files.list_directory", "payload": {"path": folder}},
                },
                {
                    "title": "Preparar resumen final verificado",
                    "depends_on": [2, 8],
                    "action": {"tool": "internal.prepare_final_summary", "payload": {}},
                },
            ],
            status=PlanStatus.PENDING,
        )
        execution = await self.executor.run(plan["id"], user_id=user_id)
        project = _project_from_execution(execution.plan, self.planning, user_id=user_id)
        task_count = len(project.get("tasks") or []) if project else 0
        task_lines = (
            "\n".join(f"- {task['title']}" for task in project.get("tasks", []))
            if project
            else "- No hay tareas verificadas."
        )
        if execution.failed_step:
            if project and task_count == 5:
                project_line = (
                    f"Creé y verifiqué el proyecto persistente **{project['name']}** con "
                    f"{task_count} tarea(s) iniciales:\n{task_lines}\n\n"
                )
            else:
                project_line = (
                    "No pude verificar el proyecto con cinco tareas para esta ejecución.\n\n"
                )
            answer = (
                f"{project_line}"
                "La parte de investigación no quedó completa, así que no inventé fuentes ni "
                f"informe. Detalle: {execution.failed_step['error']}"
            )
            visual = "error"
        else:
            answer = (
                f"Creé y verifiqué el proyecto persistente **{project['name']}** con "
                f"{task_count} tareas iniciales:\n"
                f"{task_lines}\n\n"
                f"También guardé y verifiqué el informe en `{folder}/{report_name}` con las "
                f"fuentes originales recuperadas.\n{execution.summary}"
            )
            visual = "research" if execution.visual != "complete" else "project"
        return CoreActionResult(
            answer=answer,
            conversation_id=conversation_id,
            state="researching" if not execution.failed_step else "error",
            progress_label="Investigando recursos y creando proyecto de estudio...",
            done_label="Investigación y proyecto procesados.",
            visual=visual,
            suggestions=[
                {"label": "Ver actividad", "prompt": "muéstrame los trabajos recientes"},
                {"label": "Analizar informe", "prompt": "analiza los archivos de informes"},
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
            tool.name for tool in self.tools.list() if tool.available and tool.can_execute
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


def _looks_like_project_tasks_query(lowered: str) -> bool:
    return bool(
        re.search(r"\b(tareas|pendientes|actividades)\b", lowered)
        and re.search(r"\bproyecto\b", lowered)
        and re.search(r"\b(que|qué|cuales|cuáles|tiene|hay|muestra|listar|lista)\b", lowered)
    )


def _looks_like_task_update_request(lowered: str) -> bool:
    return bool(
        re.search(r"\b(tarea|tareas)\b", lowered)
        and re.search(
            r"\b(marca|marcar|completa|completada|completado|termine|terminé|"
            r"finalice|finalicé|hecho)\b",
            lowered,
        )
    )


def _extract_task_project_name(message: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", message).strip(" .?¿!¡")
    match = re.search(r"\b(?:mi\s+)?proyecto\s+(.+)$", cleaned, re.I)
    if not match:
        return None
    project = re.split(
        r"\s*(?:[,.]|\bcomprueba\b|\bverifica\b|\bconfirma\b|\by\s+comprueba\b)",
        match.group(1),
        maxsplit=1,
        flags=re.I,
    )[0]
    return project.strip(" .?¿!¡") or None


def _extract_task_title(message: str) -> str | None:
    quoted = re.search(r"[\"“]([^\"”]+)[\"”]", message)
    if quoted:
        return quoted.group(1).strip()
    match = re.search(
        r"\btarea\s+(.+?)\s+de\s+(?:mi\s+)?proyecto\b",
        message,
        re.I,
    )
    return match.group(1).strip(" .?¿!¡") if match else None


def _references_first_task(message: str) -> bool:
    return bool(re.search(r"\b(primera|primer|1ra|1er)\s+tarea\b", message, re.I))


def _extract_project_name_query(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", message).strip(" .?¿!¡")
    patterns = [
        r"\bproyecto\s+(.+?)(?:\?|$)",
        r"\btareas\s+(?:tiene|hay|del|de)\s+(?:mi\s+)?proyecto\s+(.+?)(?:\?|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.I)
        if match:
            return match.group(1).strip(" .?¿!¡") or cleaned
    return cleaned


def _looks_like_research_report(lowered: str) -> bool:
    return bool(re.search(r"\b(investiga|investigar)\b", lowered) and "informe" in lowered)


def _looks_like_research_study_project(lowered: str) -> bool:
    return bool(
        re.search(r"\b(investiga|investigar|recursos)\b", lowered)
        and re.search(r"\bpython\b", lowered)
        and re.search(r"\b(proyecto|tareas|semana)\b", lowered)
    )


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
    cleaned = re.sub(r"^martes[,\s]+", "", cleaned, flags=re.I)
    if re.search(r"\b(investiga|investigar)\b", cleaned, re.I):
        topic = re.sub(r"^.*?\b(?:investiga|investigar)\b\s*", "", cleaned, flags=re.I)
        topic = re.split(
            r",\s*(?:comp[aá]ralos|compara|guarda|crea)|\s+y\s+(?:guarda|crea|haz|prepara)\b|\s+con\s+\d+\s+tareas\b",
            topic,
            maxsplit=1,
            flags=re.I,
        )[0]
        topic = topic.strip(" .,")
        if topic:
            return topic
    match = re.search(r"\bproyecto\s+para\s+(.+)$", cleaned, re.I)
    if not match:
        match = re.search(r"\b(?:sobre|acerca de|para)\s+(.+)$", cleaned, re.I)
    return (match.group(1) if match else cleaned).strip(" .")


def _extract_python_learning_objective(message: str) -> str:
    if re.search(r"\bpython\b", message, re.I):
        return "aprender Python durante una semana con recursos gratuitos"
    return _extract_objective(message)


def _project_from_execution(
    plan: dict[str, Any],
    planning: PlanningStore,
    *,
    user_id: str,
) -> dict[str, Any] | None:
    for step in plan.get("steps", []):
        result = step.get("result") or {}
        if result.get("tool") != "internal.create_study_project":
            continue
        project_id = (result.get("result") or {}).get("project_id")
        if project_id:
            return planning.get_project(str(project_id), user_id=user_id)
    return None


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
