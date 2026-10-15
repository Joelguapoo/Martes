import json

import pytest
from fastapi.testclient import TestClient

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.config.settings import get_settings
from app.core.intent import IntentResolver
from app.core.tool_calling import ToolCallingEngine
from app.main import create_app
from app.planning.executor import PlanExecutor
from app.planning.models import TaskStatus
from app.planning.store import PlanningStore
from app.tools.registry import ToolRegistry
from app.tools.tasks import TaskAgent, TaskToolError, register_task_tools

TASK_TITLE = "Revisar los tres recursos gratuitos seleccionados."
PROJECT_NAME = "Estudio Python - Semana 1"


class FakeProvider(LocalModelProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def status(self) -> ModelStatus:
        return ModelStatus(True, self.name, "fake-local", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "Respuesta simulada"


class NativeTaskProvider(FakeProvider):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.calls = 0
        self.messages: list[dict] = []

    @property
    def supports_native_tools(self) -> bool:
        return True

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str = "auto",
        timeout: float | None = None,
    ) -> dict:
        self.calls += 1
        self.messages = messages
        if self.calls == 1:
            return {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "task-update",
                            "type": "function",
                            "function": {
                                "name": "tasks_update_status",
                                "arguments": json.dumps(
                                    {"task_id": self.task_id, "status": "DONE"}
                                ),
                            },
                        }
                    ],
                }
            }
        return {"message": {"role": "assistant", "content": "Tarea completada."}}


def _client(tmp_path, monkeypatch) -> tuple[TestClient, PlanningStore]:
    monkeypatch.setenv("MARTES_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    get_settings.cache_clear()
    db_path = tmp_path / "app.db"
    app = create_app(
        model_provider=FakeProvider(),
        database_path=db_path,
        enable_web=False,
        auth_required=False,
    )
    return TestClient(app), PlanningStore(db_path)


def _project(store: PlanningStore, user_id: str = "test-user") -> dict:
    return store.create_project(
        user_id=user_id,
        name=PROJECT_NAME,
        objective="Aprender Python con recursos gratuitos.",
        tasks=[TASK_TITLE, "Completar una lección introductoria de Python."],
    )


def test_chat_marks_task_done_and_persists_across_connections(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    project = _project(store)
    conversation = client.post("/api/conversations", json={"title": "Seguimiento"}).json()

    response = client.post(
        "/api/chat",
        json={
            "conversation_id": conversation["id"],
            "message": (
                "MARTES, marca como completada la tarea “Revisar los tres recursos "
                "gratuitos seleccionados” de mi proyecto Estudio Python - Semana 1. "
                "Comprueba que el cambio quedó guardado"
            ),
        },
    ).json()

    task = store.find_tasks_by_name(
        user_id="test-user", project_id=project["id"], query=TASK_TITLE
    )[0]
    reopened = PlanningStore(tmp_path / "app.db").get_task(task["id"], user_id="test-user")
    activity = client.get("/api/activity").json()["activity"]

    assert "estado `DONE`" in response["answer"]
    assert reopened is not None
    assert reopened["status"] == TaskStatus.DONE.value
    assert reopened["completed_at"] is not None
    assert any(event["kind"] == "task_updated" for event in activity)
    assert any(event["kind"] == "task_verified" for event in activity)
    workspace_files = [item for item in (tmp_path / "workspace").rglob("*") if item.is_file()]
    assert workspace_files == []


def test_task_query_from_new_conversation_returns_persisted_tasks(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    _project(store)
    conversation = client.post("/api/conversations", json={"title": "Consulta nueva"}).json()

    response = client.post(
        "/api/chat",
        json={
            "conversation_id": conversation["id"],
            "message": "MARTES, ¿qué tareas tiene mi proyecto Estudio Python - Semana 1?",
        },
    ).json()

    assert TASK_TITLE in response["answer"]
    assert response["payload"]["project"]["name"] == PROJECT_NAME


def test_chat_can_complete_first_task_when_project_is_explicit(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    project = _project(store)

    response = client.post(
        "/api/chat",
        json={
            "message": "Terminé la primera tarea de mi proyecto Estudio Python - Semana 1",
        },
    ).json()

    first_task = store.list_project_tasks(project["id"], user_id="test-user")[0]
    assert "estado `DONE`" in response["answer"]
    assert first_task["status"] == TaskStatus.DONE.value


def test_missing_and_ambiguous_task_never_modify_another_task(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    project = store.create_project(
        user_id="test-user",
        name=PROJECT_NAME,
        objective="Prueba",
        tasks=["Duplicada", "Duplicada"],
    )
    conversation = client.post("/api/conversations", json={"title": "Ambigua"}).json()

    missing = client.post(
        "/api/chat",
        json={
            "conversation_id": conversation["id"],
            "message": (
                "Marca como completada la tarea “No existe” de mi proyecto "
                "Estudio Python - Semana 1"
            ),
        },
    ).json()
    ambiguous = client.post(
        "/api/chat",
        json={
            "conversation_id": conversation["id"],
            "message": (
                "Marca como completada la tarea “Duplicada” de mi proyecto "
                "Estudio Python - Semana 1"
            ),
        },
    ).json()
    tasks = store.list_project_tasks(project["id"], user_id="test-user")

    assert "No encontré la tarea" in missing["answer"]
    assert "varias tareas" in ambiguous["answer"]
    assert {task["status"] for task in tasks} == {TaskStatus.TODO.value}


@pytest.mark.anyio
async def test_task_tools_validate_owner_fields_and_database_failure(tmp_path):
    store = PlanningStore(tmp_path / "app.db")
    project = _project(store, user_id="owner")
    task = project["tasks"][0]
    agent = TaskAgent(store)

    with pytest.raises(TaskToolError):
        await agent.update_status({"user_id": "other", "task_id": task["id"], "status": "DONE"})

    updated = await agent.update(
        {
            "user_id": "owner",
            "task_id": task["id"],
            "title": "Revisar recursos verificados",
            "description": "Comparar los tres enlaces.",
            "priority": "high",
            "due_at": "2026-10-20T09:00:00",
        }
    )
    assert updated["verified"] is True
    assert updated["task"]["priority"] == "high"
    assert updated["task"]["due_at"] == "2026-10-20T09:00:00"

    original = store.update_task
    store.update_task = lambda *args, **kwargs: None
    with pytest.raises(TaskToolError):
        await agent.update_status({"user_id": "owner", "task_id": task["id"], "status": "DONE"})
    store.update_task = original
    assert store.get_task(task["id"], user_id="owner")["status"] == TaskStatus.TODO.value


@pytest.mark.anyio
async def test_native_tool_calling_receives_verified_task_result(tmp_path):
    store = PlanningStore(tmp_path / "app.db")
    project = _project(store, user_id="u1")
    task = project["tasks"][0]
    registry = ToolRegistry()
    register_task_tools(registry, TaskAgent(store))
    intent = IntentResolver(registry).resolve(
        "Marca como completada la tarea Revisar los tres recursos gratuitos seleccionados"
    )
    provider = NativeTaskProvider(task["id"])

    result = await ToolCallingEngine(model=provider, tools=registry).run(
        [{"role": "user", "content": "Marca esa tarea como completada"}],
        intent=intent,
        user_id="u1",
        conversation_id="conversation-1",
    )

    assert result is not None
    assert result.answer == "Tarea completada."
    assert result.tool_events[0].verified is True
    assert store.get_task(task["id"], user_id="u1")["status"] == TaskStatus.DONE.value
    tool_message = provider.messages[-1]["content"]
    assert '"status": "DONE"' in tool_message


@pytest.mark.anyio
async def test_plan_executor_runs_verified_task_update_with_plan_activity(tmp_path):
    store = PlanningStore(tmp_path / "app.db")
    project = _project(store, user_id="u1")
    task = project["tasks"][0]
    registry = ToolRegistry()
    register_task_tools(registry, TaskAgent(store))
    plan = store.create_plan(
        user_id="u1",
        conversation_id="conversation-plan",
        objective="Completar tarea de estudio",
        steps=[
            {
                "title": "Completar tarea de recursos",
                "action": {
                    "tool": "tasks.update_status",
                    "payload": {"task_id": task["id"], "status": "DONE"},
                },
            }
        ],
    )

    result = await PlanExecutor(planning=store, tools=registry).run(plan["id"], user_id="u1")
    activity = store.list_activity(user_id="u1")

    assert result.failed_step is None
    assert store.get_task(task["id"], user_id="u1")["status"] == TaskStatus.DONE.value
    assert any(
        event["kind"] == "task_updated" and event["plan_id"] == plan["id"] for event in activity
    )
