from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "anthropic/claude-3.5-sonnet"
    OPENROUTER_MODEL_FALLBACK: str = "openai/gpt-4o-mini"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    RESEND_API_KEY: str = ""
    FROM_EMAIL: str = "bot@generationconscious.co"
    ESCALATION_EMAIL: str = "Info@GenerationConscious.co"
    PIPEDRIVE_API_TOKEN: str = ""
    PIPEDRIVE_DOMAIN: str = ""
    ALLOWED_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:5500"
    RATE_LIMIT_PER_MINUTE: int = 20
    DAILY_COST_CAP_USD: float = 10.0

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
