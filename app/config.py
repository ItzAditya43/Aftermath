from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, read from environment variables (or a local `.env`)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Which AgentService implementation to use: "mock" (built in) or "real" (app/agent/real_agent.py).
    agent_mode: Literal["mock", "real"] = "mock"

    # Used only by the real agent.
    groq_api_key: str = ""
    hindsight_api_key: str = ""
    hindsight_bank_id: str = "aftermath-incidents"

    database_url: str = "sqlite:///./aftermath.db"

    # Comma-separated list, e.g. "http://localhost:3000,https://aftermath.vercel.app"
    frontend_origin: str = "http://localhost:3000"

    # Load seed data on startup when the database is empty (useful on hosts with ephemeral disks).
    auto_seed: bool = False

    # Delay between streamed tokens in the mock agent. Set to 0 in tests.
    mock_token_delay_ms: int = 30

    # Protects /admin/seed and /admin/reset. When set, callers must send it in the X-Admin-Key header.
    # When empty, the admin routes only accept requests from localhost.
    admin_api_key: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origin.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
