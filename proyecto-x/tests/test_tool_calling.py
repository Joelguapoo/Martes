import json

from app.ai.base import ChatMessage, LocalModelProvider, ModelStatus
from app.core.intent import IntentResolver
from app.core.tool_calling import ToolCallingEngine
from app.security.permissions import RiskLevel
from app.tools.registry import ToolRegistry, ToolSpec


class ToolCallingProvider(LocalModelProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.messages = []
        self.tools = []

    @property
    def name(self) -> str:
        return "tool-fake"

    @property
    def supports_native_tools(self) -> bool:
        return True

    async def status(self) -> ModelStatus:
        return ModelStatus(True, self.name, "fake-tools", "ok")

    async def complete(self, messages: list[ChatMessage], timeout: float | None = None) -> str:
        return "directo"

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
        self.tools = tools
        if self.calls == 1:
            return {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "weather_current",
                                "arguments": json.dumps({"city": "Bogotá"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        return {
            "message": {
                "role": "assistant",
                "content": "En Bogotá hay 18 grados y llovizna.",
            },
            "finish_reason": "stop",
        }


async def test_tool_calling_engine_executes_registered_tool_and_returns_final_answer():
    async def weather(payload):
        return {"temperature": 18, "source": "test", "city": payload["city"]}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            "weather.current",
            "Consultar clima",
            RiskLevel.QUERY,
            weather,
            input_schema={"city": "ciudad"},
            required_params=["city"],
        )
    )
    intent = IntentResolver(registry).resolve("Cómo está el clima en Bogotá?")
    provider = ToolCallingProvider()

    result = await ToolCallingEngine(model=provider, tools=registry).run(
        [{"role": "user", "content": "Cómo está el clima en Bogotá?"}],
        intent=intent,
        user_id="u1",
    )

    assert result is not None
    assert result.answer == "En Bogotá hay 18 grados y llovizna."
    assert result.model_calls == 2
    assert result.tool_events[0].tool == "weather.current"
    assert result.tool_events[0].verified is True
    assert provider.tools[0]["function"]["name"] == "weather_current"


async def test_tool_calling_engine_rejects_unknown_tool_call():
    registry = ToolRegistry()
    registry.register(ToolSpec("weather.current", "Consultar clima", RiskLevel.QUERY, lambda _: {}))
    intent = IntentResolver(registry).resolve("clima en Bogotá")
    provider = ToolCallingProvider()

    async def bad_complete_with_tools(messages, tools, *, tool_choice="auto", timeout=None):
        provider.calls += 1
        if provider.calls == 1:
            return {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_bad",
                            "type": "function",
                            "function": {"name": "admin_delete_all", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        return {"message": {"role": "assistant", "content": "No ejecuté eso."}}

    provider.complete_with_tools = bad_complete_with_tools

    result = await ToolCallingEngine(model=provider, tools=registry).run(
        [{"role": "user", "content": "clima en Bogotá"}],
        intent=intent,
        user_id="u1",
    )

    assert result is not None
    assert result.tool_events[0].ok is False
    assert "no registrada" in result.tool_events[0].error
