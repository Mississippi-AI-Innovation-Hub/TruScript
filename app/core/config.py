"""
Canonical application settings — single source of truth.
All internal modules import from here; the root config.py re-exports this.
"""
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_env: str = Field(default="development")
    app_name: str = Field(default="MSBN Transcript Verification API")
    app_version: str = Field(default="0.1.0")
    debug: bool = Field(default=False)
    secret_key: str = Field(..., min_length=32)

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(..., description="Async PostgreSQL DSN (asyncpg)")
    database_sync_url: str = Field(..., description="Sync PostgreSQL DSN (Alembic)")
    db_pool_size: int = Field(default=10, ge=1, le=50)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=5)

    # ── File Storage ──────────────────────────────────────────────────────────
    upload_dir: str = Field(default="/tmp/msbn_uploads")
    max_file_size_mb: int = Field(default=25, ge=1, le=100)
    allowed_mime_types: list[str] = Field(
        default=["application/pdf", "image/jpeg", "image/png", "image/tiff"]
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── JWT ───────────────────────────────────────────────────────────────────
    access_token_expire_minutes: int = Field(default=60, ge=5)
    algorithm: str = Field(default="HS256")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # ── CORS ──────────────────────────────────────────────────────────────────
    allowed_origins: list[str] = Field(default=["http://localhost:3000"])

    @field_validator("allowed_mime_types", "allowed_origins", mode="before")
    @classmethod
    def parse_comma_separated(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",")]
        return v

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
