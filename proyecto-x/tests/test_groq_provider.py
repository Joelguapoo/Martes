import json

import httpx

from app.ai.base import ChatMessage, ProviderError
from app.ai.groq import GroqProvider


async def test_groq_status_reports_missing_key():
    provider = GroqProvider(api_key=None, model="openai/gpt-oss-120b")

    status = await provider.status()

    assert status.available is False
    assert status.details["code"] == "missing_api_key"
    assert "GROQ_API_KEY" in status.message


async def test_groq_status_reports_invalid_key():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {}}))
    provider = GroqProvider(api_key="bad", model="openai/gpt-oss-120b", transport=transport)

    status = await provider.status()

    assert status.available is False
    assert status.details["code"] == "invalid_auth"


async def test_groq_completion_uses_openai_compatible_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hola, estoy en linea."}}]},
        )

    provider = GroqProvider(
        api_key="secret",
        model="openai/gpt-oss-120b",
        transport=httpx.MockTransport(handler),
    )

    answer = await provider.complete([ChatMessage("user", "hola")])

    assert answer == "Hola, estoy en linea."
    assert captured["path"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer secret"
    assert captured["body"]["model"] == "openai/gpt-oss-120b"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hola"}]


async def test_groq_completion_maps_rate_limits():
    transport = httpx.MockTransport(lambda request: httpx.Response(429, json={"error": {}}))
    provider = GroqProvider(api_key="secret", model="openai/gpt-oss-120b", transport=transport)

    try:
        await provider.complete([ChatMessage("user", "hola")])
    except ProviderError as exc:
        assert exc.code == "rate_limited"
    else:
        raise AssertionError("Expected ProviderError")
