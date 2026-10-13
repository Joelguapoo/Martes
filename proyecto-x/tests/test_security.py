import pytest

from app.security.permissions import PermissionError, RiskLevel, require_authorization
from app.tools.registry import ToolRegistry, ToolSpec


def test_permission_blocks_consequence_without_authorization():
    with pytest.raises(PermissionError):
        require_authorization(RiskLevel.CONSEQUENCE, authorized=False)


async def test_registry_runs_safe_tool():
    async def handler(payload):
        return {"echo": payload["value"]}

    registry = ToolRegistry()
    registry.register(ToolSpec("echo", "eco", RiskLevel.QUERY, handler))

    assert await registry.run("echo", {"value": "ok"}) == {"echo": "ok"}
