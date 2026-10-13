import pytest

from app.planning.executor import PlanExecutor
from app.planning.models import PlanStatus
from app.planning.store import PlanningStore
from app.security.permissions import RiskLevel
from app.tools.registry import ToolRegistry, ToolSpec


@pytest.mark.anyio
async def test_plan_executor_runs_steps_in_order_and_persists_results(tmp_path):
    calls = []

    async def first(payload):
        calls.append(("first", payload))
        return {"created": True, "path": payload["path"]}

    async def second(payload):
        calls.append(("second", payload))
        return {"created": True, "path": payload["path"]}

    registry = ToolRegistry()
    registry.register(
        ToolSpec("files.create_directory", "crear", RiskLevel.LOCAL_REVERSIBLE, first)
    )
    registry.register(ToolSpec("files.create_text_file", "txt", RiskLevel.LOCAL_REVERSIBLE, second))
    store = PlanningStore(tmp_path / "app.db")
    plan = store.create_plan(
        user_id="u1",
        objective="crear proyecto",
        steps=[
            {
                "title": "crear carpeta",
                "action": {"tool": "files.create_directory", "payload": {"path": "demo"}},
            },
            {
                "title": "crear archivo",
                "depends_on": [1],
                "action": {
                    "tool": "files.create_text_file",
                    "payload": {"path": "demo/notas.txt", "content": "hola"},
                },
            },
        ],
    )

    result = await PlanExecutor(planning=store, tools=registry).run(plan["id"], user_id="u1")
    saved = store.get_plan(plan["id"], user_id="u1")

    assert [name for name, _ in calls] == ["first", "second"]
    assert result.failed_step is None
    assert saved["status"] == PlanStatus.COMPLETED.value
    assert all(step["status"] == PlanStatus.COMPLETED.value for step in saved["steps"])
    assert saved["steps"][1]["result"]["verified"] is True


@pytest.mark.anyio
async def test_plan_executor_failure_keeps_previous_results_and_resume_skips_done(tmp_path):
    calls = {"ok": 0, "fail": 0}

    async def ok(payload):
        calls["ok"] += 1
        return {"created": True, "path": payload["path"]}

    async def fail(payload):
        calls["fail"] += 1
        raise ValueError("fallo controlado")

    registry = ToolRegistry()
    registry.register(ToolSpec("files.create_directory", "crear", RiskLevel.LOCAL_REVERSIBLE, ok))
    registry.register(ToolSpec("files.create_text_file", "txt", RiskLevel.LOCAL_REVERSIBLE, fail))
    store = PlanningStore(tmp_path / "app.db")
    plan = store.create_plan(
        user_id="u1",
        objective="fallar sin duplicar",
        steps=[
            {
                "title": "crear carpeta",
                "action": {"tool": "files.create_directory", "payload": {"path": "demo"}},
            },
            {
                "title": "crear archivo",
                "depends_on": [1],
                "action": {
                    "tool": "files.create_text_file",
                    "payload": {"path": "demo/notas.txt", "content": "hola"},
                },
            },
        ],
    )
    executor = PlanExecutor(planning=store, tools=registry)

    first = await executor.run(plan["id"], user_id="u1")
    second = await executor.run(plan["id"], user_id="u1")
    saved = store.get_plan(plan["id"], user_id="u1")

    assert first.failed_step is not None
    assert second.failed_step is None
    assert calls["ok"] == 1
    assert calls["fail"] == 1
    assert saved["steps"][0]["status"] == PlanStatus.COMPLETED.value
    assert saved["steps"][1]["status"] == PlanStatus.FAILED.value


@pytest.mark.anyio
async def test_plan_executor_requires_authorization_for_risky_tools(tmp_path):
    async def rename(payload):
        return {"renamed": True, "target": payload["target"]}

    registry = ToolRegistry()
    registry.register(ToolSpec("files.rename", "renombrar", RiskLevel.CONSEQUENCE, rename))
    store = PlanningStore(tmp_path / "app.db")
    plan = store.create_plan(
        user_id="u1",
        objective="mover",
        steps=[
            {
                "title": "renombrar",
                "action": {
                    "tool": "files.rename",
                    "payload": {"source": "a.txt", "target": "b.txt"},
                },
            }
        ],
    )

    result = await PlanExecutor(planning=store, tools=registry).run(plan["id"], user_id="u1")
    saved = store.get_plan(plan["id"], user_id="u1")

    assert result.failed_step is not None
    assert "autorizacion" in result.failed_step["error"]
    assert saved["status"] == PlanStatus.FAILED.value
