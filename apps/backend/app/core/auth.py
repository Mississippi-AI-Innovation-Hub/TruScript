"""
Keycloak JWT authentication dependency.

Flow:
  1. Client obtains a Bearer token from Keycloak's token endpoint.
  2. Client sends: Authorization: Bearer <token>
  3. This module fetches Keycloak's public JWKS (cached with 1-hour TTL),
     validates the token signature, expiry, and issuer, then returns the
     decoded payload as `current_user`.

Usage in endpoints:
    from app.core.auth import get_current_user, require_role

    @router.get("/protected")
    async def protected(user: dict = Security(get_current_user)):
        ...

    @router.get("/admin-only")
    async def admin_only(user: dict = Security(require_role("msbn-admin"))):
        ...
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from jose import JWTError, jwt

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=True)

# ── JWKS cache (module-level, refreshed every hour) ───────────────────────────
_jwks_cache: dict | None = None
_jwks_fetched_at: datetime | None = None
_JWKS_TTL = timedelta(hours=1)


async def _fetch_jwks() -> dict:
    """Fetch and cache Keycloak's public key set (JWKS)."""
    global _jwks_cache, _jwks_fetched_at

    now = datetime.now(timezone.utc)
    if (
        _jwks_cache is None
        or _jwks_fetched_at is None
        or (now - _jwks_fetched_at) > _JWKS_TTL
    ):
        settings = get_settings()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(settings.keycloak_jwks_uri)
            resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now

    return _jwks_cache  # type: ignore[return-value]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict[str, Any]:
    """
    FastAPI dependency — validates the Keycloak Bearer token and returns
    the decoded JWT payload.

    Raises HTTP 401 if the token is missing, expired, or invalid.
    Raises HTTP 503 if Keycloak is unreachable.
    """
    settings = get_settings()
    token = credentials.credentials

    _unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        jwks = await _fetch_jwks()
        payload: dict[str, Any] = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={
                "verify_aud": False,   # Keycloak may omit `aud`; verified by issuer
                "verify_at_hash": False,
            },
        )
    except JWTError:
        raise _unauthorized
    except httpx.HTTPError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service (Keycloak) is unreachable.",
        )

    # Validate issuer matches configured realm
    expected_issuer = f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
    if payload.get("iss") != expected_issuer:
        raise _unauthorized

    return payload


def require_role(role: str):
    """
    Returns a FastAPI dependency that enforces a specific Keycloak realm role.

    Example:
        @router.delete("/...", dependencies=[Depends(require_role("msbn-admin"))])
    """
    async def _check(user: dict[str, Any] = Security(get_current_user)) -> dict[str, Any]:
        realm_access = user.get("realm_access", {})
        roles: list[str] = realm_access.get("roles", [])
        if role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' is required.",
            )
        return user

    return _check
