import asyncio
import json
import os
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.ai.base import ChatMessage, LocalModelProvider, ProviderError
from app.ai.groq import GROQ_KEYS_URL, GroqProvider
from app.ai.llama_cpp import LlamaCppProvider
from app.ai.manager import ProviderManager
from app.ai.ollama import OllamaProvider
from app.ai.remote import RemoteCompatibleProvider
from app.api.schemas import (
    ChatRequest,
    ConversationCreateRequest,
    ConversationRenameRequest,
    GroqSettingsRequest,
    LoginRequest,
    MemoryCreateRequest,
    MemoryUpdateRequest,
    ProfileUpdateRequest,
    ToolRunRequest,
    UserCreateRequest,
)
from app.auth import CSRF_HEADER, OWNER_EMAIL, SESSION_COOKIE, AuthStore, AuthUser
from app.config.settings import get_settings
from app.core.briefing import BriefingService
from app.core.identity import MARTES_SYSTEM_PROMPT
from app.core.martes_core import CoreActionResult, MartesCore
from app.core.orchestrator import AssistantOrchestrator
from app.memory.conversation import ConversationStore
from app.memory.models import MemoryItem
from app.memory.store import MemoryStore
from app.planning.store import PlanningStore
from app.reminders.store import ReminderStore
from app.research.web import WebResearcher
from app.security.permissions import RiskLevel
from app.tools.documents import DocumentAgent, DocumentToolError, register_document_tools
from app.tools.files import FileAgent, FileToolError, register_file_tools
from app.tools.registry import ToolRegistry, ToolSpec
from app.tools.reminders import ReminderAgent, ReminderToolError, register_reminder_tools
from app.users import UserProfile, UserProfileStore
from app.voice.services import VoiceService
from app.weather import WeatherService


def create_app(
    *,
    model_provider: LocalModelProvider | None = None,
    database_path: Path | None = None,
    enable_web: bool | None = None,
    auth_required: bool | None = None,
) -> FastAPI:
    settings = get_settings()
    db_path = database_path or settings.database_path
    auth_enabled = settings.auth_required if auth_required is None else auth_required
    auth = AuthStore(db_path)
    auth.bootstrap_owner(
        settings.bootstrap_email or OWNER_EMAIL,
        settings.bootstrap_password,
    )
    memory = MemoryStore(db_path)
    conversations = ConversationStore(db_path)
    planning = PlanningStore(db_path)
    reminders = ReminderStore(db_path)
    profiles = UserProfileStore(db_path)
    weather = WeatherService(
        db_path,
        cache_minutes=settings.weather_cache_minutes,
        timeout=settings.request_timeout_seconds,
    )

    def build_provider() -> LocalModelProvider:
        return model_provider or ProviderManager(
            [
                OllamaProvider(settings.ollama_base_url, settings.model),
                LlamaCppProvider(settings.llama_cpp_executable, settings.llama_cpp_model_path),
                GroqProvider(
                    api_key=settings.groq_api_key,
                    model=settings.groq_model,
                    base_url=settings.groq_base_url,
                ),
                RemoteCompatibleProvider(
                    enabled=settings.remote_enabled,
                    base_url=settings.remote_base_url,
                    model=settings.remote_model,
                    api_key=settings.remote_api_key,
                ),
            ],
            mode=settings.provider_mode,
        )

    model_holder: dict[str, LocalModelProvider] = {"model": build_provider()}

    def current_model() -> LocalModelProvider:
        return model_holder["model"]

    web_enabled = settings.enable_web if enable_web is None else enable_web
    researcher = (
        WebResearcher(
            current_model(),
            max_pages=settings.max_research_pages,
            timeout=settings.request_timeout_seconds,
            tavily_enabled=settings.tavily_enabled,
            tavily_api_key=settings.tavily_api_key,
        )
        if web_enabled
        else None
    )
    file_agent = FileAgent(settings.workspace_root)
    document_agent = DocumentAgent(file_agent)
    reminder_agent = ReminderAgent(reminders)
    tools = ToolRegistry()
    register_file_tools(tools, file_agent)
    register_document_tools(tools, document_agent)
    register_reminder_tools(tools, reminder_agent)
    tools.register(
        ToolSpec(
            "weather.current",
            "Obtener clima actual real para la ciudad configurada o solicitada con Open-Meteo.",
            RiskLevel.QUERY,
            lambda payload: _weather_tool(weather, payload),
            permissions_required=["sesion autenticada", "ciudad o coordenadas configuradas"],
            input_schema={"city": "ciudad opcional"},
            expected_result="Temperatura, condicion, probabilidad de precipitacion y fuente.",
        )
    )
    tools.register(
        ToolSpec(
            "research.web",
            "Investigar en internet cuando Tavily o busqueda web esten habilitados.",
            RiskLevel.QUERY,
            lambda payload: _research_tool(researcher, payload),
            available=web_enabled,
            can_execute=web_enabled,
            permissions_required=["sesion autenticada"],
            input_schema={"query": "tema a investigar"},
            expected_result="Fuentes recuperadas y URL reales.",
        )
    )
    orchestrator = AssistantOrchestrator(
        current_model(),
        memory,
        researcher,
        enable_memory=settings.enable_memory,
        remote_memory_enabled=settings.remote_memory_enabled,
        enable_tone=settings.enable_tone_analysis,
        auto_learning=settings.auto_learning,
        capabilities_context=tools.capability_context(),
    )
    martes_core = MartesCore(planning=planning, tools=tools, reminders=reminders)

    app = FastAPI(title="MARTES", version="0.2.0")
    ui_dir = Path(__file__).parent / "ui"
    app.mount("/static", StaticFiles(directory=ui_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (ui_dir / "index.html").read_text(encoding="utf-8")

    def require_user(request: Request) -> AuthUser:
        if not auth_enabled:
            return AuthUser(
                id="test-user",
                email="test@example.local",
                role="owner",
                must_change_password=False,
                created_at=datetime.now(UTC),
                disabled_at=None,
            )
        session_id = request.cookies.get(SESSION_COOKIE)
        resolved = auth.get_session(session_id)
        if resolved is None:
            raise HTTPException(status_code=401, detail="Sesion requerida.")
        user, session = resolved
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = request.headers.get(CSRF_HEADER)
            if token != session.csrf_token:
                raise HTTPException(status_code=403, detail="Token CSRF invalido.")
        return user

    def require_owner(request: Request) -> AuthUser:
        user = require_user(request)
        if user.role != "owner":
            raise HTTPException(status_code=403, detail="Solo el propietario puede hacer esto.")
        return user

    def can_include_unowned(user: AuthUser) -> bool:
        return user.role == "owner"

    def redact_sensitive_chat(text: str) -> tuple[str, bool]:
        markers = [
            r"(?i)\b(password|contrasena|contraseña|api\s*key|token|secret)\b\s*[:=]\s*\S+",
            r"(?i)\bGROQ_API_KEY\s*=\s*\S+",
            r"(?i)\bTAVILY_API_KEY\s*=\s*\S+",
        ]
        redacted = text
        changed = False
        for pattern in markers:
            redacted, count = re.subn(pattern, "[credencial redactada]", redacted)
            changed = changed or count > 0
        return redacted, changed

    @app.get("/api/auth/me")
    async def me(request: Request) -> dict[str, object]:
        user = require_user(request)
        profile = profiles.ensure(user.id)
        session_id = request.cookies.get(SESSION_COOKIE)
        session = auth.get_session(session_id)[1] if auth_enabled and session_id else None
        return {
            "authenticated": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
                "must_change_password": user.must_change_password,
                "profile": profile_to_dict(profile),
            },
            "csrf_token": session.csrf_token if session else "test-csrf",
        }

    @app.get("/api/auth/bootstrap")
    async def bootstrap_status() -> dict[str, object]:
        return {
            "required": auth_enabled and not auth.has_users(),
            "email": settings.bootstrap_email or OWNER_EMAIL,
        }

    @app.post("/api/auth/bootstrap")
    async def bootstrap(payload: LoginRequest, request: Request) -> JSONResponse:
        if not auth_enabled:
            raise HTTPException(status_code=400, detail="Autenticacion desactivada.")
        _require_local_origin(request)
        if auth.has_users():
            raise HTTPException(status_code=409, detail="El propietario ya existe.")
        expected_email = (settings.bootstrap_email or OWNER_EMAIL).strip().lower()
        if payload.email.strip().lower() != expected_email:
            raise HTTPException(status_code=403, detail="Correo de propietario no autorizado.")
        if len(payload.password) < 8:
            raise HTTPException(
                status_code=422,
                detail="La contrasena debe tener al menos 8 caracteres.",
            )
        user = auth.create_user(
            email=expected_email,
            password=payload.password,
            role="owner",
            must_change_password=False,
        )
        profiles.ensure(user.id)
        session = auth.create_session(user.id)
        response = JSONResponse(content={"bootstrapped": True, "authenticated": True})
        response.set_cookie(
            SESSION_COOKIE,
            session.id,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=12 * 60 * 60,
        )
        return response

    @app.post("/api/auth/login")
    async def login(payload: LoginRequest) -> JSONResponse:
        user = auth.authenticate(payload.email, payload.password)
        if user is None:
            raise HTTPException(status_code=401, detail="Credenciales invalidas.")
        profiles.ensure(user.id)
        session = auth.create_session(user.id)
        response = JSONResponse(content={"authenticated": True})
        response.set_cookie(
            SESSION_COOKIE,
            session.id,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=12 * 60 * 60,
        )
        return response

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        require_user(request)
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id:
            auth.delete_session(session_id)
        response = JSONResponse(content={"logged_out": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.post("/api/users")
    async def create_user(payload: UserCreateRequest, request: Request) -> dict[str, object]:
        owner = require_owner(request)
        if payload.password != payload.confirm_password:
            raise HTTPException(status_code=422, detail="Las contrasenas no coinciden.")
        if auth.user_exists(payload.email):
            raise HTTPException(status_code=409, detail="No se pudo crear la cuenta.")
        user = auth.create_user(email=payload.email, password=payload.password, role="user")
        profiles.ensure(user.id, created_by_user_id=owner.id)
        return {
            "created": True,
            "user": {
                "email": user.email,
                "role": user.role,
                "created_at": user.created_at.isoformat(),
            },
        }

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        model = current_model()
        model_status = await model.status()
        provider_statuses = []
        if isinstance(model, ProviderManager):
            provider_statuses = [status.__dict__ for status in (await model.inspect()).statuses]
        return {
            "backend": {"available": True, "message": "Backend disponible"},
            "model": model_status.__dict__,
            "providers": provider_statuses or [model_status.__dict__],
            "voice": VoiceService().status().__dict__,
            "settings": {
                "web": web_enabled,
                "memory": settings.enable_memory,
                "tone_analysis": settings.enable_tone_analysis,
                "voice": settings.enable_voice,
                "database": str(settings.database_path),
                "provider_mode": settings.provider_mode,
                "llama_cpp_executable": str(settings.llama_cpp_executable or ""),
                "llama_cpp_model_path": str(settings.llama_cpp_model_path or ""),
                "remote_enabled": settings.remote_enabled,
                "remote_memory_enabled": settings.remote_memory_enabled,
                "auth_required": auth_enabled,
                "tavily_enabled": settings.tavily_enabled,
                "browser_voice_enabled": settings.browser_voice_enabled,
                "workspace_root": str(settings.workspace_root),
                "groq": {
                    "configured": bool(settings.groq_api_key),
                    "model": settings.groq_model,
                    "base_url": settings.groq_base_url,
                    "keys_url": GROQ_KEYS_URL,
                },
            },
        }

    @app.get("/api/capabilities")
    async def capabilities(request: Request) -> dict[str, object]:
        require_user(request)
        return {
            "tools": tools.public_specs(),
            "planning": {
                "plans": True,
                "projects": True,
                "tasks": True,
                "activity": True,
            },
            "workspace_root": str(settings.workspace_root.resolve()),
            "weather_source": "https://open-meteo.com/",
        }

    @app.get("/api/activity")
    async def activity(request: Request) -> dict[str, object]:
        user = require_user(request)
        return {
            "activity": planning.list_activity(user_id=user.id),
            "plans": planning.list_plans(user_id=user.id),
            "projects": planning.list_projects(user_id=user.id),
            "tasks": planning.list_tasks(user_id=user.id),
            "reminders": reminders.upcoming(user_id=user.id),
        }

    @app.get("/api/plans")
    async def plans(request: Request) -> list[dict[str, object]]:
        user = require_user(request)
        return planning.list_plans(user_id=user.id)

    @app.post("/api/plans/{plan_id}/resume")
    async def resume_plan(plan_id: str, request: Request) -> dict[str, object]:
        user = require_user(request)
        result = await martes_core.executor.run(plan_id, user_id=user.id)
        return {
            "ok": True,
            "plan": result.plan,
            "summary": result.summary,
            "failed_step": result.failed_step,
        }

    @app.post("/api/plans/{plan_id}/cancel")
    async def cancel_plan(plan_id: str, request: Request) -> dict[str, object]:
        user = require_user(request)
        cancelled = planning.cancel_plan(plan_id, user_id=user.id)
        if not cancelled:
            raise HTTPException(status_code=404, detail="Plan no encontrado.")
        return {"ok": True, "cancelled": True}

    @app.get("/api/projects")
    async def projects(request: Request) -> list[dict[str, object]]:
        user = require_user(request)
        return planning.list_projects(user_id=user.id)

    @app.get("/api/profile")
    async def get_profile(request: Request) -> dict[str, object]:
        user = require_user(request)
        return profile_to_dict(profiles.ensure(user.id))

    @app.put("/api/profile")
    async def update_profile(payload: ProfileUpdateRequest, request: Request) -> dict[str, object]:
        user = require_user(request)
        settings_payload = {
            key: value
            for key, value in {
                "welcome_enabled": payload.welcome_enabled,
                "task_reminders_enabled": payload.task_reminders_enabled,
                "contextual_suggestions_enabled": payload.contextual_suggestions_enabled,
                "periodic_checks_enabled": payload.periodic_checks_enabled,
                "voice_enabled": payload.voice_enabled,
                "tone_analysis_enabled": payload.tone_analysis_enabled,
            }.items()
            if value is not None
        }
        profile = profiles.update(
            user.id,
            display_name=payload.display_name,
            city=payload.city,
            settings=settings_payload,
        )
        if payload.city:
            try:
                location = await weather.geocode(payload.city)
            except httpx.HTTPError:
                location = None
            if location:
                profile = profiles.update(
                    user.id,
                    city=payload.city,
                    weather_latitude=location.latitude,
                    weather_longitude=location.longitude,
                    weather_location_label=location.label,
                )
        return profile_to_dict(profile)

    @app.get("/api/welcome")
    async def welcome(request: Request) -> dict[str, object]:
        user = require_user(request)
        profile = profiles.ensure(user.id)
        if not profile.welcome_enabled:
            profiles.touch_seen(user.id)
            return {"enabled": False}
        first_time = profile.initial_welcome_shown_at is None
        forecast = await _forecast_for_profile(weather, profile)
        text = build_welcome_text(profile, first_time=first_time, forecast=forecast)
        profiles.mark_welcome_shown(user.id)
        return {
            "enabled": True,
            "first_time": first_time,
            "message": text,
            "profile": profile_to_dict(profile),
            "weather": forecast,
        }

    @app.get("/api/weather/current")
    async def weather_current(request: Request, city: str | None = None) -> dict[str, object]:
        user = require_user(request)
        profile = profiles.ensure(user.id)
        if city:
            location = await weather.geocode(city)
            if location is None:
                raise HTTPException(status_code=404, detail="No encontre esa ciudad.")
            profile = profiles.update(
                user.id,
                city=city,
                weather_latitude=location.latitude,
                weather_longitude=location.longitude,
                weather_location_label=location.label,
            )
        forecast = await _forecast_for_profile(weather, profile)
        if forecast is None:
            raise HTTPException(status_code=404, detail="Configure una ciudad para el clima.")
        return forecast

    @app.post("/api/tools/run")
    async def run_tool(payload: ToolRunRequest, request: Request) -> dict[str, object]:
        user = require_user(request)
        tool_payload = dict(payload.payload)
        if payload.tool.startswith("reminders."):
            tool_payload["user_id"] = user.id
        try:
            result = await tools.run(payload.tool, tool_payload, authorized=payload.authorized)
        except (FileToolError, DocumentToolError, ReminderToolError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"tool": payload.tool, "ok": True, "result": result}

    @app.get("/api/briefing")
    async def briefing(request: Request) -> dict[str, object]:
        require_user(request)
        model = current_model()
        model_status = await model.status()
        if isinstance(model, ProviderManager):
            provider_statuses = (await model.inspect()).statuses
        else:
            provider_statuses = [model_status]
        voice_status = VoiceService().status()
        service = BriefingService(conversations, memory)
        return service.build(
            model_status=model_status,
            provider_statuses=provider_statuses,
            backend_available=True,
            web_enabled=web_enabled,
            voice_available=voice_status.stt_available or voice_status.tts_available,
        ).to_dict()

    @app.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request) -> dict[str, object]:
        user = require_user(request)
        message, was_redacted = redact_sensitive_chat(payload.message.strip())
        if not message:
            raise HTTPException(status_code=422, detail="El mensaje no puede estar vacio.")
        conversation = (
            conversations.get_conversation(
                payload.conversation_id,
                user_id=user.id,
                include_unowned=can_include_unowned(user),
            )
            if payload.conversation_id
            else conversations.ensure_default_conversation(user_id=user.id)
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversacion no encontrada.")
        core_action = await martes_core.handle(
            message,
            user_id=user.id,
            conversation_id=conversation.id,
        )
        if core_action:
            conversations.add("user", message, conversation_id=conversation.id, user_id=user.id)
            conversations.add(
                "assistant",
                core_action.answer,
                conversation_id=conversation.id,
                user_id=user.id,
            )
            return _core_action_response(core_action)
        file_action = await maybe_handle_file_instruction(
            message,
            conversations,
            user.id,
            tools,
            conversation_id=payload.conversation_id,
            persist=True,
        )
        if file_action:
            return file_action
        conversation = (
            conversations.get_conversation(
                payload.conversation_id,
                user_id=user.id,
                include_unowned=can_include_unowned(user),
            )
            if payload.conversation_id
            else conversations.ensure_default_conversation(user_id=user.id)
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversacion no encontrada.")
        prior = [
            ChatMessage(item.role, item.content)
            for item in conversations.messages(conversation.id, limit=12, user_id=user.id)
            if item.role in {"user", "assistant"}
        ]
        if was_redacted:
            conversations.add(
                "system",
                "Se detecto una posible credencial y fue redactada antes de procesar el mensaje.",
                kind="notice",
                conversation_id=conversation.id,
                user_id=user.id,
            )
        conversations.add("user", message, conversation_id=conversation.id, user_id=user.id)
        result = await orchestrator.handle(message, history=prior, user_id=user.id)
        if result.answer and result.model_available:
            conversations.add(
                "assistant",
                result.answer,
                conversation_id=conversation.id,
                user_id=user.id,
            )
        elif result.notice:
            conversations.add(
                "system",
                result.notice,
                kind="notice",
                conversation_id=conversation.id,
                user_id=user.id,
            )
        return {
            "conversation_id": conversation.id,
            "answer": result.answer if result.model_available else None,
            "notice": result.notice,
            "latency_ms": result.latency_ms,
            "model_available": result.model_available,
            "model_message": result.model_message,
            "tone": result.tone.__dict__ if result.tone else None,
            "used_memory": [item.model_dump(mode="json") for item in result.used_memory],
            "proposed_memories": [
                item.model_dump(mode="json") for item in result.proposed_memories
            ],
            "research": result.research.model_dump(mode="json") if result.research else None,
        }

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        user = require_user(request)
        message, was_redacted = redact_sensitive_chat(payload.message.strip())
        if not message:
            raise HTTPException(status_code=422, detail="El mensaje no puede estar vacio.")
        conversation = (
            conversations.get_conversation(
                payload.conversation_id,
                user_id=user.id,
                include_unowned=can_include_unowned(user),
            )
            if payload.conversation_id
            else conversations.ensure_default_conversation(user_id=user.id)
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversacion no encontrada.")
        core_action = await martes_core.handle(
            message,
            user_id=user.id,
            conversation_id=conversation.id,
        )
        if core_action:
            conversations.add("user", message, conversation_id=conversation.id, user_id=user.id)
            conversations.add(
                "assistant",
                core_action.answer,
                conversation_id=conversation.id,
                user_id=user.id,
            )
            return StreamingResponse(
                _core_action_events(core_action),
                media_type="application/x-ndjson",
            )
        file_action = await maybe_handle_file_instruction(
            message,
            conversations,
            user.id,
            tools,
            conversation_id=conversation.id,
            persist=True,
        )
        if file_action:
            async def tool_events():
                def line(event: str, data: dict[str, object]) -> str:
                    return json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"

                yield line(
                    "state",
                    {
                        "conversation_id": file_action["conversation_id"],
                        "state": file_action.get("state", "executing"),
                        "label": file_action.get(
                            "progress_label",
                            "Ejecutando herramienta local...",
                        ),
                        "visual": file_action.get("visual", file_action.get("action", "files")),
                    },
                )
                await asyncio.sleep(0.55)
                yield line(
                    "tool_done",
                    {
                        "conversation_id": file_action["conversation_id"],
                        "label": file_action.get("done_label", "Operación verificada."),
                        "path": file_action.get("tool_path"),
                        "suggestions": file_action.get("suggestions", []),
                        "visual": file_action.get("visual", file_action.get("action", "files")),
                    },
                )
                await asyncio.sleep(0.45)
                yield line("token", {"text": file_action["answer"]})
                yield line(
                    "done",
                    {
                        "conversation_id": file_action["conversation_id"],
                        "suggestions": file_action.get("suggestions", []),
                    },
                )

            return StreamingResponse(tool_events(), media_type="application/x-ndjson")
        prior = [
            ChatMessage(item.role, item.content)
            for item in conversations.messages(conversation.id, limit=12, user_id=user.id)
            if item.role in {"user", "assistant"}
        ]
        if was_redacted:
            conversations.add(
                "system",
                "Se detecto una posible credencial y fue redactada antes de procesar el mensaje.",
                kind="notice",
                conversation_id=conversation.id,
                user_id=user.id,
            )
        conversations.add("user", message, conversation_id=conversation.id, user_id=user.id)

        async def events():
            def line(event: str, data: dict[str, object]) -> str:
                return json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"

            model = current_model()
            status = await model.status()
            if not status.available:
                notice = (
                    "Todavia no puedo conversar porque el proveedor de IA no esta configurado "
                    f"o no esta disponible. Detalle: {status.message}"
                )
                conversations.add(
                    "system",
                    notice,
                    kind="notice",
                    conversation_id=conversation.id,
                    user_id=user.id,
                )
                yield line("notice", {"conversation_id": conversation.id, "notice": notice})
                return
            remote_provider = status.provider in {"groq", "remote-authorized"}
            can_use_memory = settings.enable_memory and (
                not remote_provider or settings.remote_memory_enabled
            )
            relevant = memory.search(message, limit=5, user_id=user.id) if can_use_memory else []
            context = "No hay contexto adicional pertinente."
            parts = []
            if relevant:
                parts.append(
                    "Memoria pertinente:\n"
                    + "\n".join(f"- {item.content}" for item in relevant)
                )
            parts.append(tools.capability_context())
            context = "\n\n".join(parts)
            messages = [
                ChatMessage("system", MARTES_SYSTEM_PROMPT),
                ChatMessage("system", context),
                *prior[-10:],
                ChatMessage("user", message),
            ]
            answer_parts: list[str] = []
            yield line("state", {"conversation_id": conversation.id, "state": "receiving"})
            try:
                async for chunk in model.stream(messages):
                    answer_parts.append(chunk)
                    yield line("token", {"text": chunk})
            except ProviderError as exc:
                notice = (
                    "El proveedor de IA no pudo completar la respuesta. "
                    f"Detalle: {exc.message}"
                )
                conversations.add(
                    "system",
                    notice,
                    kind="notice",
                    conversation_id=conversation.id,
                    user_id=user.id,
                )
                yield line("error", {"notice": notice, "code": exc.code})
                return
            answer = "".join(answer_parts).strip()
            if answer:
                conversations.add(
                    "assistant",
                    answer,
                    conversation_id=conversation.id,
                    user_id=user.id,
                )
            yield line("done", {"conversation_id": conversation.id})

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.get("/api/history")
    async def history(
        request: Request,
        conversation_id: str | None = None,
    ) -> list[dict[str, object]]:
        user = require_user(request)
        return [
            item.model_dump(mode="json")
            for item in conversations.recent(
                conversation_id=conversation_id,
                user_id=user.id,
                include_unowned=can_include_unowned(user),
            )
        ]

    @app.get("/api/conversations")
    async def list_conversations(request: Request) -> list[dict[str, object]]:
        user = require_user(request)
        items = conversations.list_conversations(
            user_id=user.id,
            include_unowned=can_include_unowned(user),
        )
        if not items:
            items = [conversations.create_conversation(user_id=user.id)]
        return [item.model_dump(mode="json") for item in items]

    @app.post("/api/conversations")
    async def create_conversation(
        request: Request,
        payload: ConversationCreateRequest | None = None,
    ) -> dict[str, object]:
        user = require_user(request)
        title = payload.title if payload and payload.title else "Nuevo chat"
        return conversations.create_conversation(title, user_id=user.id).model_dump(mode="json")

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request) -> dict[str, object]:
        user = require_user(request)
        item = conversations.get_conversation(
            conversation_id,
            user_id=user.id,
            include_unowned=can_include_unowned(user),
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Conversacion no encontrada.")
        messages = [
            msg.model_dump(mode="json")
            for msg in conversations.messages(conversation_id, user_id=user.id)
        ]
        payload = item.model_dump(mode="json")
        payload["messages"] = messages
        return payload

    @app.put("/api/conversations/{conversation_id}")
    async def rename_conversation(
        conversation_id: str,
        payload: ConversationRenameRequest,
        request: Request,
    ) -> dict[str, object]:
        user = require_user(request)
        item = conversations.rename_conversation(conversation_id, payload.title, user_id=user.id)
        if item is None:
            raise HTTPException(status_code=404, detail="Conversacion no encontrada.")
        return item.model_dump(mode="json")

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str, request: Request) -> dict[str, bool]:
        user = require_user(request)
        return {"deleted": conversations.delete_conversation(conversation_id, user_id=user.id)}

    @app.get("/api/settings/provider")
    async def provider_settings(request: Request) -> dict[str, object]:
        require_user(request)
        return {
            "provider_mode": settings.provider_mode,
            "groq": {
                "configured": bool(settings.groq_api_key),
                "model": settings.groq_model,
                "base_url": settings.groq_base_url,
                "remote_memory_enabled": settings.remote_memory_enabled,
                "keys_url": GROQ_KEYS_URL,
            },
        }

    @app.post("/api/settings/groq")
    async def save_groq_settings(
        payload: GroqSettingsRequest,
        request: Request,
    ) -> dict[str, object]:
        require_owner(request)
        _require_local_origin(request)
        _write_env_values(
            Path(".env"),
            {
                "PROJECT_X_PROVIDER_MODE": payload.provider_mode,
                "GROQ_MODEL": payload.model,
                "GROQ_BASE_URL": payload.base_url,
                "PROJECT_X_REMOTE_MEMORY_ENABLED": str(payload.remote_memory_enabled).lower(),
                **({"GROQ_API_KEY": payload.api_key.strip()} if payload.api_key else {}),
            },
        )
        settings.provider_mode = payload.provider_mode
        settings.groq_model = payload.model
        settings.groq_base_url = payload.base_url
        settings.remote_memory_enabled = payload.remote_memory_enabled
        if payload.api_key:
            settings.groq_api_key = payload.api_key.strip()
            os.environ["GROQ_API_KEY"] = payload.api_key.strip()
        model_holder["model"] = build_provider()
        orchestrator.model = current_model()
        test_provider = GroqProvider(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=settings.groq_base_url,
        )
        provider_status = await test_provider.status()
        return {
            "saved": True,
            "status": provider_status.__dict__,
            "configured": bool(settings.groq_api_key),
        }

    @app.get("/api/memory")
    async def list_memory(request: Request) -> list[dict[str, object]]:
        user = require_user(request)
        return [
            item.model_dump(mode="json")
            for item in memory.list(
                include_discarded=True,
                user_id=user.id,
                include_unowned=can_include_unowned(user),
            )
        ]

    @app.post("/api/memory")
    async def create_memory(payload: MemoryCreateRequest, request: Request) -> dict[str, object]:
        user = require_user(request)
        item = memory.add(
            MemoryItem(category=payload.category, content=payload.content),
            user_id=user.id,
        )
        return item.model_dump(mode="json")

    @app.put("/api/memory/{item_id}")
    async def update_memory(
        item_id: str,
        payload: MemoryUpdateRequest,
        request: Request,
    ) -> dict[str, object]:
        user = require_user(request)
        item = memory.update(
            item_id,
            content=payload.content,
            category=payload.category,
            user_id=user.id,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Recuerdo no encontrado.")
        return item.model_dump(mode="json")

    @app.delete("/api/memory/{item_id}")
    async def delete_memory(item_id: str, request: Request) -> dict[str, bool]:
        user = require_user(request)
        return {"deleted": memory.delete(item_id, user_id=user.id)}

    @app.delete("/api/memory")
    async def clear_memory(request: Request) -> dict[str, bool]:
        user = require_user(request)
        memory.clear(user_id=user.id)
        return {"cleared": True}

    return app


app = create_app()


def _core_action_response(action: CoreActionResult) -> dict[str, object]:
    return {
        "conversation_id": action.conversation_id,
        "answer": action.answer,
        "notice": None,
        "latency_ms": 0,
        "model_available": True,
        "model_message": "Accion coordinada por MartesCore.",
        "tone": None,
        "used_memory": [],
        "proposed_memories": [],
        "research": None,
        "suggestions": action.suggestions,
        "payload": action.payload,
    }


async def _core_action_events(action: CoreActionResult):
    def line(event: str, data: dict[str, object]) -> str:
        return json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"

    yield line(
        "state",
        {
            "conversation_id": action.conversation_id,
            "state": action.state,
            "label": action.progress_label,
            "visual": action.visual,
        },
    )
    await asyncio.sleep(0.55)
    yield line(
        "tool_done",
        {
            "conversation_id": action.conversation_id,
            "label": action.done_label,
            "suggestions": action.suggestions,
            "visual": action.visual,
        },
    )
    await asyncio.sleep(0.35)
    yield line("token", {"text": action.answer})
    yield line(
        "done",
        {
            "conversation_id": action.conversation_id,
            "suggestions": action.suggestions,
        },
    )


def profile_to_dict(profile: UserProfile) -> dict[str, object]:
    return {
        "user_id": profile.user_id,
        "display_name": profile.display_name,
        "city": profile.city,
        "weather_location_label": profile.weather_location_label,
        "initial_welcome_shown": profile.initial_welcome_shown_at is not None,
        "last_seen_at": profile.last_seen_at.isoformat() if profile.last_seen_at else None,
        "settings": {
            "welcome_enabled": profile.welcome_enabled,
            "task_reminders_enabled": profile.task_reminders_enabled,
            "contextual_suggestions_enabled": profile.contextual_suggestions_enabled,
            "periodic_checks_enabled": profile.periodic_checks_enabled,
            "voice_enabled": profile.voice_enabled,
            "tone_analysis_enabled": profile.tone_analysis_enabled,
        },
    }


async def _forecast_for_profile(
    weather: WeatherService,
    profile: UserProfile,
) -> dict[str, object] | None:
    if profile.weather_latitude is None or profile.weather_longitude is None:
        return None
    try:
        return await weather.current_forecast(
            latitude=profile.weather_latitude,
            longitude=profile.weather_longitude,
            label=profile.weather_location_label or profile.city,
        )
    except httpx.HTTPError:
        return None


def build_welcome_text(
    profile: UserProfile,
    *,
    first_time: bool,
    forecast: dict[str, object] | None,
) -> str:
    name = profile.display_name or "Señor"
    try:
        bogota_tz = ZoneInfo("America/Bogota")
    except ZoneInfoNotFoundError:
        bogota_tz = timezone(timedelta(hours=-5), name="America/Bogota")
    now = datetime.now(bogota_tz)
    weekdays = [
        "lunes",
        "martes",
        "miercoles",
        "jueves",
        "viernes",
        "sabado",
        "domingo",
    ]
    months = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]
    hour = now.strftime("%I:%M %p").lower().replace("am", "a. m.").replace("pm", "p. m.")
    date_line = (
        f"{weekdays[now.weekday()]} {now.day} de {months[now.month - 1]} "
        f"de {now.year}, {hour}"
    )
    weather_line = ""
    if forecast:
        precipitation = forecast.get("precipitation_probability")
        rain = (
            f", probabilidad de precipitacion {precipitation}%"
            if precipitation is not None
            else ""
        )
        weather_line = (
            f" Clima en {forecast.get('location') or profile.city}: "
            f"{forecast.get('temperature')}{forecast.get('temperature_unit', '°C')}, "
            f"{forecast.get('condition') or 'condicion no disponible'}{rain}."
        )
    elif profile.city:
        weather_line = " No pude actualizar el clima ahora mismo; sigo operativo."
    else:
        weather_line = " Si configura una ciudad, le mostrare el clima al entrar."
    if first_time:
        return (
            f"Hola, {name}. Hoy es {date_line};{weather_line} "
            "Soy MARTES, su asistente personal. "
            "Puedo investigar, recordar preferencias autorizadas y trabajar con archivos "
            "dentro del espacio permitido."
        )
    return (
        f"Buenos dias, {name}. Hoy es {date_line};{weather_line} "
        "Todo listo por aqui. ¿Que tenemos entre manos?"
    )


async def _weather_tool(weather: WeatherService, payload: dict[str, object]) -> dict[str, object]:
    city = str(payload.get("city") or "").strip()
    if not city:
        raise ValueError("Indique una ciudad para consultar el clima.")
    location = await weather.geocode(city)
    if location is None:
        raise ValueError("No encontre esa ciudad en Open-Meteo.")
    forecast = await weather.current_forecast(
        latitude=location.latitude,
        longitude=location.longitude,
        timezone=location.timezone,
        label=location.label,
    )
    if forecast is None:
        raise ValueError("No hay clima disponible para esa ubicacion.")
    return forecast


async def _research_tool(
    researcher: WebResearcher | None,
    payload: dict[str, object],
) -> dict[str, object]:
    if researcher is None:
        raise ValueError("La investigacion web no esta habilitada.")
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ValueError("Consulta requerida.")
    result = await researcher.research(query)
    return result.model_dump(mode="json")


async def maybe_handle_file_instruction(
    message: str,
    conversations: ConversationStore,
    user_id: str,
    tools: ToolRegistry,
    *,
    conversation_id: str | None = None,
    persist: bool = False,
) -> dict[str, object] | None:
    lowered = message.lower()
    cleaned = lowered.strip()
    conversation = (
        conversations.get_conversation(conversation_id, user_id=user_id, include_unowned=True)
        if conversation_id
        else conversations.ensure_default_conversation(user_id=user_id)
    )
    if conversation is None:
        return None

    action = "create_directory"
    suggestions: list[dict[str, str]] = []
    progress_label = "Creando carpeta en el espacio autorizado..."
    done_label = "Carpeta creada y verificada."
    state = "executing"
    folder = None
    verified_path: str | None = None

    if _wants_compound_folder_text_file(lowered):
        folder = _extract_folder_target(message)
        action = "create_folder_text_file"
        progress_label = "Creando carpeta y escribiendo archivo TXT..."
        done_label = "Carpeta y archivo creados."
    elif _wants_folder_analysis(lowered):
        folder = _folder_from_previous_path(conversations, conversation.id, user_id)
        action = "analyze_directory"
        progress_label = "Analizando archivos de la carpeta..."
        done_label = "Análisis de carpeta completado."
        state = "researching"
    elif _wants_text_file(lowered):
        folder = _folder_from_previous_path(conversations, conversation.id, user_id)
        action = "create_text_file"
        progress_label = "Generando archivo TXT en la carpeta..."
        done_label = "Archivo TXT creado y verificado."
    elif cleaned in {"si", "sí", "confirmo", "dale", "hazlo"}:
        folder = _folder_from_previous_confirmation(conversations, conversation.id, user_id)
    else:
        folder = _extract_folder_target(message)

    if cleaned.startswith("files."):
        folder = None
        answer = (
            "Dígamelo en lenguaje natural, señor. Por ejemplo: "
            "`crear carpeta Joel en el escritorio`."
        )
    elif folder is None:
        return None
    elif action == "analyze_directory":
        if persist:
            conversations.add("user", message, conversation_id=conversation.id, user_id=user_id)
        listing = await tools.run("files.list_directory", {"path": folder})
        verified_path = str(listing["path"])
        items = listing.get("items", [])
        directories = [item for item in items if item.get("type") == "directory"]
        files = [item for item in items if item.get("type") == "file"]
        preview_items = items[:8]
        preview = (
            ", ".join(str(item.get("name")) for item in preview_items)
            or "sin elementos visibles"
        )
        answer = (
            f"Listo, señor. Analicé {verified_path}. "
            f"Encontré {len(items)} elemento(s): {len(directories)} carpeta(s) y "
            f"{len(files)} archivo(s). Vista rápida: {preview}."
        )
        suggestions = [
            {
                "label": "Crear archivo TXT aquí",
                "prompt": "crear archivo notas.txt en esa carpeta",
            },
            {
                "label": "Buscar por nombre",
                "prompt": "busca archivos por nombre en esa carpeta",
            },
        ]
    elif action == "create_text_file":
        if persist:
            conversations.add("user", message, conversation_id=conversation.id, user_id=user_id)
        file_name = _extract_text_file_name(message) or "notas.txt"
        content = _extract_text_file_content(message) or "Notas creadas por MARTES.\n"
        file_result = await tools.run(
            "files.create_text_file",
            {
                "path": f"{folder}/{file_name}",
                "content": content,
            },
        )
        verified_path = str(file_result["path"])
        answer = f"Listo, señor. Archivo TXT creado y verificado: {verified_path}."
        suggestions = [
            {
                "label": "Analizar esa carpeta",
                "prompt": "analiza los archivos de esa carpeta",
            }
        ]
    elif action == "create_folder_text_file":
        if persist:
            conversations.add("user", message, conversation_id=conversation.id, user_id=user_id)
        created = await tools.run("files.create_directory", {"path": folder})
        verified_path = str(created["path"])
        file_name = _extract_text_file_name(message) or "archivo.txt"
        content = _extract_text_file_content(message) or "Notas creadas por MARTES.\n"
        file_result = await tools.run(
            "files.create_text_file",
            {
                "path": f"{folder}/{file_name}",
                "content": content,
            },
        )
        file_path = str(file_result["path"])
        answer = (
            "Listo, señor. Carpeta y archivo creados y verificados: "
            f"{verified_path} | {file_path}."
        )
        suggestions = [
            {
                "label": "Analizar esa carpeta",
                "prompt": "analiza los archivos de esa carpeta",
            },
            {
                "label": "Crear otro TXT aquí",
                "prompt": "crea otro archivo txt en esa carpeta",
            },
        ]
    else:
        if persist:
            conversations.add("user", message, conversation_id=conversation.id, user_id=user_id)
        created = await tools.run("files.create_directory", {"path": folder})
        verified_path = str(created["path"])
        answer = f"Listo, señor. Carpeta creada y verificada: {verified_path}."
        suggestions = [
            {
                "label": "Crear archivo TXT aquí",
                "prompt": "crear archivo notas.txt en esa carpeta",
            },
            {
                "label": "Analizar archivos",
                "prompt": "analiza los archivos de esa carpeta",
            },
        ]

    file_result = None
    content_match = re.search(
        r"archivo (?:llamado |con nombre )?['\"]?([^'\"\n]+?\.txt)['\"]?",
        message,
        re.I,
    )
    if content_match and folder and action == "create_directory":
        file_name = content_match.group(1).strip()
        file_result = await tools.run(
            "files.create_text_file",
            {
                "path": f"{folder}/{file_name}",
                "content": "Objetivos de esta semana:\n- \n",
            },
        )
        answer += f" Archivo creado: {file_result['path']}."
    if persist:
        conversations.add("assistant", answer, conversation_id=conversation.id, user_id=user_id)
    return {
        "conversation_id": conversation.id,
        "answer": answer,
        "tool_path": verified_path if folder else None,
        "action": action,
        "state": state,
        "progress_label": progress_label,
        "done_label": done_label,
        "visual": {
            "create_directory": "folder-create",
            "create_text_file": "file-write",
            "create_folder_text_file": "folder-file",
            "analyze_directory": "file-scan",
        }.get(action, "files"),
        "suggestions": suggestions,
        "notice": None,
        "latency_ms": 0,
        "model_available": True,
        "model_message": "Accion ejecutada por herramienta local verificada.",
        "tone": None,
        "used_memory": [],
        "proposed_memories": [],
        "research": None,
    }


def _extract_folder_target(message: str) -> str | None:
    lowered = message.lower()
    wants_folder = any(
        marker in lowered
        for marker in [
            "crear carpeta",
            "crea carpeta",
            "crear una carpeta",
            "crea una carpeta",
            "crear acrpeta",
            "crea acrpeta",
            "carpeta se llame",
            "se llame",
        ]
    )
    if not wants_folder:
        return None
    patterns = [
        r"(?:crear|crea)\s+(?:una\s+)?(?:carpeta|acrpeta)\s+(?:en\s+(?:el\s+)?(?:escritorio|desktop)\s+)?(?:llamada|llamda|con\s+nombre)\s+['\"]?([^'\"\n,]+?)['\"]?(?:,|\s+y\s+|\s+dentro\s+|$)",
        r"(?:crear|crea)\s+(?:una\s+)?(?:carpeta|acrpeta)\s+(?:llamada|llamda|con\s+nombre)?\s*['\"]?([^'\"\n,]+?)['\"]?(?:,|\s+en\s+|\s+y\s+|\s+dentro\s+|$)",
        r"(?:se\s+llame|llamada|llamda|con\s+nombre)\s+['\"]?([^'\"\n,]+?)['\"]?(?:,|\s+y\s+|\s+en\s+|\s+dentro\s+|$)",
    ]
    folder = None
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            folder = match.group(1).strip()
            break
    if not folder:
        return None
    folder = re.sub(r"\b(en\s+el\s+)?escritorio\b", "", folder, flags=re.I)
    folder = re.sub(r"\bdesktop\b", "", folder, flags=re.I)
    folder = folder.strip(" .:/\\")
    if not folder:
        return None
    if re.search(r"\b(escritorio|desktop)\b", message, re.I):
        return folder
    return folder


def _wants_folder_analysis(lowered: str) -> bool:
    return bool(
        re.search(r"\b(analiza|analizar|revisa|revisar|escanea|explora)\b", lowered)
        and re.search(r"\b(archivo|archivos|carpeta|directorio|folder)\b", lowered)
    )


def _wants_text_file(lowered: str) -> bool:
    return bool(
        re.search(r"\b(crea|crear|genera|generar)\b", lowered)
        and re.search(r"\b(archivo|txt|texto)\b", lowered)
    )


def _wants_compound_folder_text_file(lowered: str) -> bool:
    return bool(
        re.search(r"\b(crea|crear|genera|generar)\b", lowered)
        and re.search(r"\b(carpeta|acrpeta|directorio)\b", lowered)
        and re.search(r"\b(archivo|txt|texto)\b", lowered)
    )


def _extract_text_file_name(message: str) -> str | None:
    match = re.search(
        r"(?:archivo|txt|texto)(?:\s+(?:llamado|llamada|con\s+nombre))?\s+['\"]?([^'\"\n,]+?\.txt)['\"]?(?:,|\s+en\s+|\s+y\s+|$)",
        message,
        re.I,
    )
    if not match:
        return None
    name = Path(match.group(1).strip()).name
    return name if name.lower().endswith(".txt") else f"{name}.txt"


def _extract_text_file_content(message: str) -> str | None:
    match = re.search(
        r"(?:diga|que\s+diga|con\s+el\s+texto|contenido|escriba)\s+['\"]?(.+?)['\"]?\s*$",
        message,
        re.I,
    )
    if not match:
        return None
    content = match.group(1).strip()
    if not content:
        return None
    return content if content.endswith("\n") else f"{content}\n"


def _folder_from_previous_path(
    conversations: ConversationStore,
    conversation_id: str,
    user_id: str,
) -> str | None:
    recent = conversations.messages(conversation_id, limit=12, user_id=user_id)
    for item in reversed(recent):
        if item.role != "assistant":
            continue
        match = re.search(
            r"(?:verificada|Analicé):?\s*([A-Za-z]:\\[^\n]+?)(?:\.\s|$)",
            item.content,
            re.I,
        )
        if not match:
            match = re.search(
                r"([A-Za-z]:\\Users\\[^\n]+?\\Desktop\\[^\n]+?)(?:\.\s|$)",
                item.content,
            )
        if match:
            path = Path(match.group(1).strip())
            if path.suffix.lower() == ".txt" or "archivo txt" in item.content.lower():
                path = path.parent
            try:
                desktop_index = [part.lower() for part in path.parts].index("desktop")
            except ValueError:
                return path.name
            return str(Path(*path.parts[desktop_index + 1 :]))
    return _folder_from_previous_confirmation(conversations, conversation_id, user_id)


def _folder_from_previous_confirmation(
    conversations: ConversationStore,
    conversation_id: str,
    user_id: str,
) -> str | None:
    recent = conversations.messages(conversation_id, limit=6, user_id=user_id)
    for item in reversed(recent):
        if item.role != "assistant":
            continue
        backtick = re.search(r"`(?:Desktop/|Escritorio/)?([^`/\\]+)`", item.content)
        if backtick:
            return backtick.group(1).strip()
        bold = re.search(r"carpeta\s+\*\*([^*]+)\*\*", item.content, re.I)
        if bold:
            return bold.group(1).strip()
    return None


def _require_local_origin(request: Request) -> None:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return
    host = urlparse(origin).hostname
    if host not in {"127.0.0.1", "localhost"}:
        raise HTTPException(status_code=403, detail="Origen no autorizado para guardar ajustes.")


def _write_env_values(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing: dict[str, int] = {}
    for index, line in enumerate(lines):
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        existing[key] = index
    for key, value in values.items():
        safe_value = value.replace("\n", "").replace("\r", "")
        line = f"{key}={safe_value}"
        if key in existing:
            lines[existing[key]] = line
        else:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
