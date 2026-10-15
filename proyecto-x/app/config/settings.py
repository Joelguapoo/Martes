from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = Field("127.0.0.1", alias="PROJECT_X_HOST")
    port: int = Field(8765, alias="PROJECT_X_PORT")
    data_dir: Path = Field(Path("./data"), alias="PROJECT_X_DATA_DIR")
    ollama_base_url: str = Field("http://127.0.0.1:11434", alias="PROJECT_X_OLLAMA_BASE_URL")
    model: str = Field("qwen2.5:7b-instruct-q4_K_M", alias="PROJECT_X_MODEL")
    provider_mode: str = Field("groq", alias="PROJECT_X_PROVIDER_MODE")
    groq_api_key: str | None = Field(None, alias="GROQ_API_KEY")
    groq_model: str = Field("openai/gpt-oss-120b", alias="GROQ_MODEL")
    groq_base_url: str = Field("https://api.groq.com/openai/v1", alias="GROQ_BASE_URL")
    remote_memory_enabled: bool = Field(False, alias="PROJECT_X_REMOTE_MEMORY_ENABLED")
    llama_cpp_executable: Path | None = Field(None, alias="PROJECT_X_LLAMA_CPP_EXECUTABLE")
    llama_cpp_model_path: Path | None = Field(None, alias="PROJECT_X_LLAMA_CPP_MODEL_PATH")
    remote_enabled: bool = Field(False, alias="PROJECT_X_REMOTE_ENABLED")
    remote_base_url: str | None = Field(None, alias="PROJECT_X_REMOTE_BASE_URL")
    remote_model: str | None = Field(None, alias="PROJECT_X_REMOTE_MODEL")
    remote_api_key: str | None = Field(None, alias="PROJECT_X_REMOTE_API_KEY")
    bootstrap_email: str = Field("kevynnquintero32@gmail.com", alias="MARTES_BOOTSTRAP_EMAIL")
    bootstrap_password: str | None = Field(None, alias="MARTES_BOOTSTRAP_PASSWORD")
    auth_required: bool = Field(True, alias="MARTES_AUTH_REQUIRED")
    tavily_api_key: str | None = Field(None, alias="TAVILY_API_KEY")
    tavily_enabled: bool = Field(False, alias="MARTES_TAVILY_ENABLED")
    browser_voice_enabled: bool = Field(False, alias="MARTES_BROWSER_VOICE_ENABLED")
    enable_web: bool = Field(True, alias="PROJECT_X_ENABLE_WEB")
    enable_memory: bool = Field(True, alias="PROJECT_X_ENABLE_MEMORY")
    enable_tone_analysis: bool = Field(True, alias="PROJECT_X_ENABLE_TONE_ANALYSIS")
    enable_voice: bool = Field(False, alias="PROJECT_X_ENABLE_VOICE")
    auto_learning: bool = Field(False, alias="PROJECT_X_AUTO_LEARNING")
    native_tool_calling_enabled: bool = Field(True, alias="MARTES_NATIVE_TOOL_CALLING_ENABLED")
    native_tool_calling_max_iterations: int = Field(
        4,
        alias="MARTES_NATIVE_TOOL_CALLING_MAX_ITERATIONS",
    )
    workspace_root: Path = Field(Path("./workspace"), alias="MARTES_WORKSPACE_ROOT")
    weather_cache_minutes: int = Field(45, alias="MARTES_WEATHER_CACHE_MINUTES")
    request_timeout_seconds: float = 20.0
    max_research_pages: int = 4

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "proyecto_x.db"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    return settings
