from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.planning.models import PlanStatus, TaskStatus


class PlanningStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_plans (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT,
                    objective TEXT NOT NULL,
                    context TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    risk_level INTEGER NOT NULL DEFAULT 0,
                    next_action TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_plan_steps (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    tool_name TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES execution_plans(id) ON DELETE CASCADE
                )
                """
            )
            step_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(execution_plan_steps)").fetchall()
            }
            if "action_json" not in step_columns:
                conn.execute(
                    "ALTER TABLE execution_plan_steps "
                    "ADD COLUMN action_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "depends_on_json" not in step_columns:
                conn.execute(
                    "ALTER TABLE execution_plan_steps "
                    "ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]'"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    objective TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    due_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    due_at TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    conversation_id TEXT,
                    plan_id TEXT,
                    project_id TEXT,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activity_user_created
                ON activity_events(user_id, created_at DESC)
                """
            )

    def create_plan(
        self,
        *,
        user_id: str,
        objective: str,
        steps: list[str | dict[str, Any]],
        conversation_id: str | None = None,
        context: str = "",
        risk_level: int = 0,
        status: PlanStatus = PlanStatus.PLANNING,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        plan_id = str(uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_plans
                (id, user_id, conversation_id, objective, context, status, risk_level,
                 next_action, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    user_id,
                    conversation_id,
                    objective,
                    context,
                    status.value,
                    risk_level,
                    self._step_title(steps[0]) if steps else "Esperando siguiente acción.",
                    now,
                    now,
                ),
            )
            for index, step in enumerate(steps, start=1):
                title = self._step_title(step)
                action = step.get("action", {}) if isinstance(step, dict) else {}
                depends_on = step.get("depends_on", []) if isinstance(step, dict) else []
                conn.execute(
                    """
                    INSERT INTO execution_plan_steps
                    (id, plan_id, position, title, tool_name, status, result_json,
                     action_json, depends_on_json, error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?, NULL, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        plan_id,
                        index,
                        title,
                        action.get("tool"),
                        PlanStatus.PENDING.value,
                        json.dumps(action, ensure_ascii=False),
                        json.dumps(depends_on, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        self.record_activity(
            user_id=user_id,
            conversation_id=conversation_id,
            plan_id=plan_id,
            kind="planning",
            title="Plan preparado",
            status=status.value,
            detail=objective,
            payload={"steps": [self._step_title(step) for step in steps]},
        )
        return self.get_plan(plan_id, user_id=user_id) or {}

    def complete_plan(self, plan_id: str, *, user_id: str, result: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE execution_plans
                SET status = ?, next_action = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (PlanStatus.COMPLETED.value, "Completado y verificado.", now, plan_id, user_id),
            )
            conn.execute(
                """
                UPDATE execution_plan_steps
                SET status = ?, result_json = ?, updated_at = ?
                WHERE plan_id = ?
                """,
                (
                    PlanStatus.COMPLETED.value,
                    json.dumps({"result": result}, ensure_ascii=False),
                    now,
                    plan_id,
                ),
            )

    def get_plan(self, plan_id: str, *, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            plan = conn.execute(
                "SELECT * FROM execution_plans WHERE id = ? AND user_id = ?",
                (plan_id, user_id),
            ).fetchone()
            if not plan:
                return None
            steps = conn.execute(
                """
                SELECT * FROM execution_plan_steps
                WHERE plan_id = ?
                ORDER BY position ASC
                """,
                (plan_id,),
            ).fetchall()
        item = dict(plan)
        item["steps"] = [self._step_to_dict(row) for row in steps]
        return item

    def list_plans(self, *, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM execution_plans
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_plan_status(
        self,
        plan_id: str,
        *,
        user_id: str,
        status: PlanStatus,
        next_action: str = "",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE execution_plans
                SET status = ?, next_action = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (status.value, next_action, now, plan_id, user_id),
            )

    def update_step_status(
        self,
        step_id: str,
        *,
        status: PlanStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE execution_plan_steps
                SET status = ?, result_json = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    json.dumps(result or {}, ensure_ascii=False),
                    error,
                    now,
                    step_id,
                ),
            )

    def cancel_plan(self, plan_id: str, *, user_id: str) -> bool:
        plan = self.get_plan(plan_id, user_id=user_id)
        if not plan:
            return False
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE execution_plans
                SET status = ?, next_action = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    PlanStatus.CANCELLED.value,
                    "Cancelado por el usuario.",
                    now,
                    plan_id,
                    user_id,
                ),
            )
            conn.execute(
                """
                UPDATE execution_plan_steps
                SET status = ?, updated_at = ?
                WHERE plan_id = ? AND status = ?
                """,
                (PlanStatus.CANCELLED.value, now, plan_id, PlanStatus.PENDING.value),
            )
        self.record_activity(
            user_id=user_id,
            conversation_id=plan.get("conversation_id"),
            plan_id=plan_id,
            kind="plan_cancelled",
            title="Plan cancelado",
            status=PlanStatus.CANCELLED.value,
            detail=plan["objective"],
        )
        return True

    def create_project(
        self,
        *,
        user_id: str,
        name: str,
        objective: str,
        description: str = "",
        priority: str = "normal",
        tasks: list[str] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        project_id = str(uuid4())
        tasks = tasks or []
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects
                (id, user_id, name, description, objective, status, priority,
                 due_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'BACKLOG', ?, NULL, ?, ?)
                """,
                (project_id, user_id, name, description, objective, priority, now, now),
            )
            for task_title in tasks:
                conn.execute(
                    """
                    INSERT INTO tasks
                    (id, user_id, project_id, title, description, status, priority,
                     due_at, completed_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, '', ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        user_id,
                        project_id,
                        task_title,
                        TaskStatus.TODO.value,
                        priority,
                        now,
                        now,
                    ),
                )
        self.record_activity(
            user_id=user_id,
            project_id=project_id,
            kind="project",
            title="Proyecto creado",
            status="COMPLETED",
            detail=name,
            payload={"tasks": tasks},
        )
        return self.get_project(project_id, user_id=user_id) or {}

    def get_project(self, project_id: str, *, user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            project = conn.execute(
                "SELECT * FROM projects WHERE id = ? AND user_id = ?",
                (project_id, user_id),
            ).fetchone()
            if not project:
                return None
            tasks = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at ASC",
                (project_id,),
            ).fetchall()
        item = dict(project)
        item["tasks"] = [dict(row) for row in tasks]
        return item

    def list_projects(self, *, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM projects
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks(self, *, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tasks.*, projects.name AS project_name
                FROM tasks
                LEFT JOIN projects ON projects.id = tasks.project_id
                WHERE tasks.user_id = ?
                ORDER BY tasks.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_activity(
        self,
        *,
        user_id: str,
        kind: str,
        title: str,
        status: str,
        detail: str = "",
        conversation_id: str | None = None,
        plan_id: str | None = None,
        project_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        event_id = str(uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO activity_events
                (id, user_id, conversation_id, plan_id, project_id, kind, title,
                 status, detail, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    user_id,
                    conversation_id,
                    plan_id,
                    project_id,
                    kind,
                    title,
                    status,
                    detail,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now,
                ),
            )
        return {
            "id": event_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "plan_id": plan_id,
            "project_id": project_id,
            "kind": kind,
            "title": title,
            "status": status,
            "detail": detail,
            "payload": payload or {},
            "created_at": now,
        }

    def list_activity(self, *, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM activity_events
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._activity_to_dict(row) for row in rows]

    def _step_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json") or "{}")
        item["action"] = json.loads(item.pop("action_json", "{}") or "{}")
        item["depends_on"] = json.loads(item.pop("depends_on_json", "[]") or "[]")
        return item

    def _activity_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def _step_title(self, step: str | dict[str, Any]) -> str:
        if isinstance(step, str):
            return step
        return str(step.get("title") or step.get("action", {}).get("tool") or "Paso").strip()
