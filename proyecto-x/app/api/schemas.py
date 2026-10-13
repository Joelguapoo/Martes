from pydantic import BaseModel, Field

from app.memory.models import MemoryCategory


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    conversation_id: str | None = None


class ToolRunRequest(BaseModel):
    tool: str = Field(min_length=3, max_length=80)
    payload: dict = Field(default_factory=dict)
    authorized: bool = False


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=80)


class ConversationRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class GroqSettingsRequest(BaseModel):
    provider_mode: str = Field(default="groq", max_length=32)
    api_key: str | None = Field(default=None, max_length=400)
    model: str = Field(default="openai/gpt-oss-120b", max_length=120)
    base_url: str = Field(default="https://api.groq.com/openai/v1", max_length=240)
    remote_memory_enabled: bool = False


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=512)
    confirm_password: str = Field(min_length=8, max_length=512)


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    welcome_enabled: bool | None = None
    task_reminders_enabled: bool | None = None
    contextual_suggestions_enabled: bool | None = None
    periodic_checks_enabled: bool | None = None
    voice_enabled: bool | None = None
    tone_analysis_enabled: bool | None = None


class MemoryCreateRequest(BaseModel):
    category: MemoryCategory
    content: str


class MemoryUpdateRequest(BaseModel):
    category: MemoryCategory
    content: str
