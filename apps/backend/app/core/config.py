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
    secret_key: str = Field(default="dev-secret-key-change-in-production-use-32c", min_length=32)

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = Field(default="postgresql+asyncpg://msbn_user:msbn_pass@localhost:5432/msbn_poc")
    database_sync_url: str = Field(default="postgresql+psycopg2://msbn_user:msbn_pass@localhost:5432/msbn_poc")
    db_pool_size: int = Field(default=10, ge=1, le=50)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=5)

    # ── File Storage ──────────────────────────────────────────────────────────
    upload_dir: str = Field(default="/tmp/msbn_uploads")
    max_file_size_mb: int = Field(default=25, ge=1, le=100)
    allowed_mime_types: list[str] = Field(
        default=["application/pdf", "image/jpeg", "image/png", "image/tiff"]
    )

    # ── Keycloak ──────────────────────────────────────────────────────────────
    keycloak_url: str = Field(default="http://localhost:8080")
    keycloak_realm: str = Field(default="msbn")
    keycloak_client_id: str = Field(default="msbn-api")
    keycloak_client_secret: str = Field(default="msbn-api-secret-change-in-prod")

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── JWT (legacy — Keycloak tokens use RS256; this key is for internal use) ─
    access_token_expire_minutes: int = Field(default=60, ge=5)
    algorithm: str = Field(default="RS256")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # ── CORS ──────────────────────────────────────────────────────────────────
    allowed_origins: list[str] = Field(default=["http://localhost:3000", "http://localhost:8000"])

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

    @property
    def keycloak_jwks_uri(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/certs"

    @property
    def keycloak_token_uri(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/token"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
