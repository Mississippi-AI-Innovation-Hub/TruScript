"""
Application entry point and factory.

`create_app()` wires together:
  - FastAPI configuration from settings
  - API versioned routers
  - CORS middleware
  - Lifespan (startup / shutdown) events
  - OpenAPI / Swagger UI with Bearer auth scheme

Run locally:
    uvicorn main:app --reload

Run via Docker:
    docker compose up --build
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.v1.router import api_v1_router
from app.core.config import get_settings

_settings = get_settings()

_DESCRIPTION = "AI-assisted transcript verification system for the Mississippi State Board of Nursing (MSBN)."


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup tasks before yielding; run shutdown tasks after."""
    # Alembic handles migrations separately via entrypoint.sh
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=_settings.app_name,
        description=_DESCRIPTION,
        version=_settings.app_version,
        debug=_settings.debug,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        redirect_slashes=False,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(api_v1_router, prefix="/api")

    # ── Health check (unauthenticated) ────────────────────────────────────────
    @app.get("/health", tags=["Health"], include_in_schema=True, summary="Service health check")
    async def health() -> dict:
        return {"status": "ok", "version": _settings.app_version, "env": _settings.app_env}

    # ── Custom OpenAPI: inject Bearer security scheme ─────────────────────────
    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        # Replace all auto-generated security schemes (e.g. HTTPBearer) with a
        # single named BearerAuth so Swagger UI's Authorize dialog works correctly.
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": (
                    "Keycloak access token. "
                    f"Token URL: `{_settings.keycloak_token_uri}`"
                ),
            }
        }

        # Force BearerAuth on every operation except /health, overriding whatever
        # FastAPI generated from the HTTPBearer dependency.
        for path, path_item in schema.get("paths", {}).items():
            if path == "/health":
                continue
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation["security"] = [{"BearerAuth": []}]

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


app = create_app()
