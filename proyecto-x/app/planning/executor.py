from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.planning.models import PlanStatus
from app.planning.store import PlanningStore
from app.security.permissions import PermissionError
from app.tools.files import FileToolError
from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class PlanExecutionResult:
    plan: dict[str, Any]
    completed_steps: list[dict[str, Any]] = field(default_factory=list)
    failed_step: dict[str, Any] | None = None
    summary: str = ""
    visual: str = "verifying"


class PlanExecutor:
    def __init__(
        self,
        *,
        planning: PlanningStore,
        tools: ToolRegistry,
        max_steps_per_run: int = 12,
    ) -> None:
        self.planning = planning
        self.tools = tools
        self.max_steps_per_run = max_steps_per_run

    async def run(
        self,
        plan_id: str,
        *,
        user_id: str,
        authorized_tools: set[str] | None = None,
    ) -> PlanExecutionResult:
        authorized_tools = authorized_tools or set()
        plan = self.planning.get_plan(plan_id, user_id=user_id)
        if plan is None:
            raise ValueError("Plan no encontrado para este usuario.")
        if plan["status"] in {
            PlanStatus.COMPLETED.value,
            PlanStatus.CANCELLED.value,
            PlanStatus.FAILED.value,
        }:
            return PlanExecutionResult(
                plan=plan,
                summary=f"El plan ya está en estado {plan['status']}.",
                visual="error" if plan["status"] == PlanStatus.FAILED.value else "verifying",
            )

        self.planning.update_plan_status(
            plan_id,
            user_id=user_id,
            status=PlanStatus.RUNNING,
            next_action="Ejecutando siguiente paso seguro.",
        )
        self.planning.record_activity(
            user_id=user_id,
            conversation_id=plan.get("conversation_id"),
            plan_id=plan_id,
            kind="plan_running",
            title="Plan en ejecución",
            status=PlanStatus.RUNNING.value,
            detail=plan["objective"],
        )

        completed: list[dict[str, Any]] = []
        failed: dict[str, Any] | None = None
        latest_plan = plan
        for _ in range(self.max_steps_per_run):
            latest_plan = self.planning.get_plan(plan_id, user_id=user_id) or latest_plan
            next_step = self._next_runnable_step(latest_plan)
            if next_step is None:
                break
            result = await self._run_step(
                latest_plan,
                next_step,
                user_id=user_id,
                authorized=next_step["action"].get("tool") in authorized_tools,
            )
            if result.get("ok"):
                completed.append(result)
                continue
            failed = result
            break

        latest_plan = self.planning.get_plan(plan_id, user_id=user_id) or latest_plan
        pending = [
            step for step in latest_plan["steps"] if step["status"] == PlanStatus.PENDING.value
        ]
        running = [
            step for step in latest_plan["steps"] if step["status"] == PlanStatus.RUNNING.value
        ]
        if failed:
            self.planning.update_plan_status(
                plan_id,
                user_id=user_id,
                status=PlanStatus.FAILED,
                next_action=failed.get("error", "Revisar el paso fallido."),
            )
            visual = "error"
            summary = self._summary(latest_plan, completed, failed)
        elif pending or running:
            self.planning.update_plan_status(
                plan_id,
                user_id=user_id,
                status=PlanStatus.PENDING,
                next_action=pending[0]["title"] if pending else "Verificar paso en curso.",
            )
            visual = "planning"
            summary = self._summary(latest_plan, completed, None)
        else:
            self.planning.update_plan_status(
                plan_id,
                user_id=user_id,
                status=PlanStatus.COMPLETED,
                next_action="Completado y verificado.",
            )
            self.planning.record_activity(
                user_id=user_id,
                conversation_id=latest_plan.get("conversation_id"),
                plan_id=plan_id,
                kind="plan_completed",
                title="Trabajo completado",
                status=PlanStatus.COMPLETED.value,
                detail=latest_plan["objective"],
            )
            visual = "complete"
            summary = self._summary(latest_plan, completed, None)

        return PlanExecutionResult(
            plan=self.planning.get_plan(plan_id, user_id=user_id) or latest_plan,
            completed_steps=completed,
            failed_step=failed,
            summary=summary,
            visual=visual,
        )

    def _next_runnable_step(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        completed_positions = {
            step["position"]
            for step in plan["steps"]
            if step["status"] == PlanStatus.COMPLETED.value
        }
        for step in plan["steps"]:
            if step["status"] != PlanStatus.PENDING.value:
                continue
            dependencies = {int(item) for item in step.get("depends_on", [])}
            if dependencies.issubset(completed_positions):
                return step
        return None

    async def _run_step(
        self,
        plan: dict[str, Any],
        step: dict[str, Any],
        *,
        user_id: str,
        authorized: bool,
    ) -> dict[str, Any]:
        action = step.get("action") or {}
        tool_name = str(action.get("tool") or "").strip()
        payload = dict(action.get("payload") or {})
        if not tool_name:
            return self._fail_step(plan, step, user_id=user_id, error="Paso sin herramienta.")
        if tool_name.startswith("internal."):
            return self._run_internal_step(plan, step, user_id=user_id, payload=payload)
        if tool_name not in {tool.name for tool in self.tools.list()}:
            return self._fail_step(
                plan,
                step,
                user_id=user_id,
                error=f"Herramienta no disponible: {tool_name}",
            )

        self.planning.update_step_status(step["id"], status=PlanStatus.RUNNING)
        self.planning.record_activity(
            user_id=user_id,
            conversation_id=plan.get("conversation_id"),
            plan_id=plan["id"],
            kind="step_started",
            title=step["title"],
            status=PlanStatus.RUNNING.value,
            detail=tool_name,
            payload={"step_id": step["id"], "tool": tool_name},
        )
        try:
            payload = self._resolve_payload(tool_name, payload, plan)
            if tool_name.startswith(("tasks.", "projects.")):
                payload["user_id"] = user_id
            if tool_name.startswith("tasks.") and plan.get("conversation_id"):
                payload["conversation_id"] = plan["conversation_id"]
            if tool_name.startswith("tasks."):
                payload["plan_id"] = plan["id"]
            result = await self.tools.run(tool_name, payload, authorized=authorized)
            if tool_name == "research.web" and payload.get("min_sources"):
                minimum = int(payload["min_sources"])
                sources = result.get("sources") if isinstance(result, dict) else []
                if len(sources or []) < minimum:
                    return self._fail_step(
                        plan,
                        step,
                        user_id=user_id,
                        error=(
                            f"La investigacion recupero {len(sources or [])} fuente(s); "
                            f"se requerian {minimum}."
                        ),
                        result={"raw": result},
                    )
            verified = self._verify_result(tool_name, result)
            if not verified:
                return self._fail_step(
                    plan,
                    step,
                    user_id=user_id,
                    error=f"No pude verificar el resultado de {tool_name}.",
                    result={"raw": result},
                )
        except (FileToolError, PermissionError, ValueError, KeyError) as exc:
            return self._fail_step(plan, step, user_id=user_id, error=str(exc))
        except Exception as exc:
            return self._fail_step(plan, step, user_id=user_id, error=str(exc))

        saved_result = {"tool": tool_name, "payload": payload, "result": result, "verified": True}
        self.planning.update_step_status(
            step["id"],
            status=PlanStatus.COMPLETED,
            result=saved_result,
        )
        self.planning.record_activity(
            user_id=user_id,
            conversation_id=plan.get("conversation_id"),
            plan_id=plan["id"],
            kind="step_completed",
            title=step["title"],
            status=PlanStatus.COMPLETED.value,
            detail=tool_name,
            payload={"step_id": step["id"], "tool": tool_name, "verified": True},
        )
        return {"ok": True, "step": step, "result": saved_result}

    def _resolve_payload(
        self,
        tool_name: str,
        payload: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name == "files.create_text_file" and payload.get("content_from") == "research":
            content = self._research_report(plan)
            payload = {key: value for key, value in payload.items() if key != "content_from"}
            payload["content"] = content
        return payload

    def _research_report(self, plan: dict[str, Any]) -> str:
        for step in plan["steps"]:
            result = step.get("result") or {}
            if result.get("tool") != "research.web":
                continue
            research = result.get("result") or {}
            sources = research.get("sources") or []
            lines = [f"# Informe: {plan['objective']}", ""]
            answer = research.get("answer") or research.get("summary")
            if answer:
                lines.extend([str(answer), ""])
            comparison = self._comparison_result(plan)
            if comparison:
                lines.append("## Comparación")
                for item in comparison:
                    title = item.get("title") or "Fuente"
                    recommendation = item.get("recommendation") or "Revisar según el objetivo."
                    lines.append(f"- {title}: {recommendation}")
                lines.append("")
            lines.extend(
                [
                    "## Verificación",
                    "Las fuentes listadas provienen del resultado actual de research.web. "
                    "Las recomendaciones son orientación generada a partir de esas fuentes, "
                    "no hechos independientes.",
                    "",
                ]
            )
            lines.append("## Fuentes")
            if not sources:
                lines.append("- No se recuperaron fuentes verificables.")
            for source in sources:
                title = source.get("title") or source.get("url") or "Fuente"
                url = source.get("url") or "URL no disponible"
                lines.append(f"- {title}: {url}")
            return "\n".join(lines) + "\n"
        return f"# Informe: {plan['objective']}\n\nNo hay investigación verificada disponible.\n"

    def _verify_result(self, tool_name: str, result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if tool_name == "files.create_directory":
            return bool(result.get("created") and result.get("path"))
        if tool_name == "files.create_text_file":
            return bool(result.get("created") and result.get("path"))
        if tool_name == "files.list_directory":
            return "items" in result and bool(result.get("path"))
        if tool_name == "files.read_text_file":
            return "content" in result and bool(result.get("path"))
        if tool_name == "files.append_text_file":
            return bool(result.get("updated") and result.get("path"))
        if tool_name == "files.rename":
            return bool(result.get("renamed") and result.get("target"))
        if tool_name == "files.search":
            return "matches" in result
        if tool_name == "files.scaffold_project":
            return bool(result.get("created") and result.get("path"))
        if tool_name == "weather.current":
            return "temperature" in result and bool(result.get("source"))
        if tool_name == "research.web":
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
        if tool_name == "documents.read_text":
            return bool(result.get("path")) and "content" in result
        if tool_name == "documents.read_json":
            return bool(result.get("path")) and "data" in result
        if tool_name == "documents.read_csv":
            return bool(result.get("path")) and "rows" in result
        if tool_name == "documents.summarize":
            return bool(result.get("path")) and "summary" in result
        if tool_name == "documents.create_markdown":
            return bool(result.get("created") and result.get("path"))
        if tool_name == "reminders.create":
            return bool(result.get("created") and result.get("reminder", {}).get("id"))
        if tool_name in {"reminders.list", "reminders.upcoming"}:
            return "reminders" in result
        if tool_name == "reminders.cancel":
            return "cancelled" in result
        if tool_name == "reminders.done":
            return "done" in result
        return True

    def _fail_step(
        self,
        plan: dict[str, Any],
        step: dict[str, Any],
        *,
        user_id: str,
        error: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.planning.update_step_status(
            step["id"],
            status=PlanStatus.FAILED,
            result=result or {},
            error=error,
        )
        self.planning.record_activity(
            user_id=user_id,
            conversation_id=plan.get("conversation_id"),
            plan_id=plan["id"],
            kind="step_failed",
            title=step["title"],
            status=PlanStatus.FAILED.value,
            detail=error,
            payload={"step_id": step["id"], "tool": step.get("action", {}).get("tool")},
        )
        return {"ok": False, "step": step, "error": error, "result": result or {}}

    def _run_internal_step(
        self,
        plan: dict[str, Any],
        step: dict[str, Any],
        *,
        user_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name = str(step.get("action", {}).get("tool") or "")
        self.planning.update_step_status(step["id"], status=PlanStatus.RUNNING)
        self.planning.record_activity(
            user_id=user_id,
            conversation_id=plan.get("conversation_id"),
            plan_id=plan["id"],
            kind="step_started",
            title=step["title"],
            status=PlanStatus.RUNNING.value,
            detail=tool_name,
            payload={"step_id": step["id"], "tool": tool_name},
        )
        try:
            result = self._internal_result(tool_name, payload, plan, user_id=user_id)
        except (ValueError, KeyError) as exc:
            return self._fail_step(plan, step, user_id=user_id, error=str(exc))
        saved_result = {"tool": tool_name, "payload": payload, "result": result, "verified": True}
        self.planning.update_step_status(
            step["id"],
            status=PlanStatus.COMPLETED,
            result=saved_result,
        )
        self.planning.record_activity(
            user_id=user_id,
            conversation_id=plan.get("conversation_id"),
            plan_id=plan["id"],
            kind="step_completed",
            title=step["title"],
            status=PlanStatus.COMPLETED.value,
            detail=tool_name,
            payload={"step_id": step["id"], "tool": tool_name, "verified": True},
        )
        return {"ok": True, "step": step, "result": saved_result}

    def _internal_result(
        self,
        tool_name: str,
        payload: dict[str, Any],
        plan: dict[str, Any],
        *,
        user_id: str,
    ) -> dict[str, Any]:
        if tool_name == "internal.verify_project_tasks":
            project_id = str(payload.get("project_id") or "")
            if not project_id:
                project_id = self._project_id_from_plan(plan)
            expected = int(payload.get("expected_tasks") or 0)
            project = self.planning.get_project(project_id, user_id=user_id)
            if project is None:
                raise ValueError("No pude localizar el proyecto creado para esta ejecución.")
            task_count = len(project.get("tasks") or [])
            if task_count != expected:
                raise ValueError(
                    f"El proyecto tiene {task_count} tarea(s); se esperaban {expected}."
                )
            return {
                "project_id": project_id,
                "project_name": project["name"],
                "task_count": task_count,
            }
        if tool_name == "internal.create_study_project":
            name = str(payload.get("name") or "").strip()
            objective = str(payload.get("objective") or "").strip()
            tasks = payload.get("tasks") or []
            if not name or not objective or not isinstance(tasks, list):
                raise ValueError("Datos insuficientes para crear el proyecto.")
            project = self.planning.create_project(
                user_id=user_id,
                conversation_id=plan.get("conversation_id"),
                plan_id=plan["id"],
                name=name,
                objective=objective,
                description=str(payload.get("description") or ""),
                tasks=[str(task) for task in tasks],
            )
            return {
                "project_id": project["id"],
                "project_name": project["name"],
                "task_count": len(project.get("tasks") or []),
            }
        if tool_name == "internal.validate_research_sources":
            minimum = int(payload.get("min_sources") or 0)
            research = self._research_result(plan)
            sources = research.get("sources") or []
            if len(sources) < minimum:
                raise ValueError(
                    f"La investigación recuperó {len(sources)} fuente(s); se requerían {minimum}."
                )
            return {
                "source_count": len(sources),
                "sources": [
                    {"title": source.get("title"), "url": source.get("url")} for source in sources
                ],
            }
        if tool_name == "internal.compare_research_sources":
            research = self._research_result(plan)
            sources = research.get("sources") or []
            if not sources:
                raise ValueError("No hay fuentes verificadas para comparar.")
            comparison = [
                {
                    "title": source.get("title") or "Fuente",
                    "url": source.get("url"),
                    "recommendation": "Revisar según nivel, práctica disponible y claridad.",
                }
                for source in sources
            ]
            return {"comparison": comparison}
        if tool_name == "internal.prepare_final_summary":
            return {"ready": True, "plan_id": plan["id"]}
        raise ValueError(f"Herramienta interna no disponible: {tool_name}")

    def _project_id_from_plan(self, plan: dict[str, Any]) -> str:
        for step in plan["steps"]:
            result = step.get("result") or {}
            if result.get("tool") != "internal.create_study_project":
                continue
            project_id = (result.get("result") or {}).get("project_id")
            if project_id:
                return str(project_id)
        raise ValueError("No hay proyecto registrado en esta ejecución.")

    def _research_result(self, plan: dict[str, Any]) -> dict[str, Any]:
        for step in plan["steps"]:
            result = step.get("result") or {}
            if result.get("tool") == "research.web":
                return result.get("result") or {}
        raise ValueError("No hay investigación verificada disponible en esta ejecución.")

    def _comparison_result(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        for step in plan["steps"]:
            result = step.get("result") or {}
            if result.get("tool") != "internal.compare_research_sources":
                continue
            comparison = (result.get("result") or {}).get("comparison") or []
            if isinstance(comparison, list):
                return [item for item in comparison if isinstance(item, dict)]
        return []

    def _summary(
        self,
        plan: dict[str, Any],
        completed: list[dict[str, Any]],
        failed: dict[str, Any] | None,
    ) -> str:
        lines = [
            f"Objetivo: {plan['objective']}",
            f"Pasos completados en esta ejecución: {len(completed)}.",
        ]
        if completed:
            lines.extend(f"- {item['step']['title']}" for item in completed)
        if failed:
            lines.append(f"Paso detenido: {failed['step']['title']}.")
            lines.append(f"Motivo: {failed['error']}")
        else:
            lines.append("Resultado verificado por el backend.")
        return "\n".join(lines)
