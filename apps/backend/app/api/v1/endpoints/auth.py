"""
Authentication & user-management endpoints.

Design rules (per requirements):
  - No self-signup. Only an msbn-admin can create new accounts.
  - Login works for any enabled Keycloak user; the returned token carries
    the user's realm roles (msbn-staff / msbn-admin) as claims.
  - Logout revokes the refresh token in Keycloak (server-side invalidation).
  - User listing / creation is admin-only.

Endpoints:
  POST /auth/login            → exchange credentials for tokens (no auth required)
  POST /auth/logout           → revoke refresh token  (auth required)
  POST /auth/users            → create staff/admin user (msbn-admin only)
  GET  /auth/users            → list all users        (msbn-admin only)
  GET  /auth/me               → current user profile  (any authenticated user)
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, Security, status
from pydantic import BaseModel, EmailStr, Field

from app.core import keycloak_admin
from app.core.auth import get_current_user, require_role
from app.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["Authentication & Users"])


# ── Request / response schemas ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, example="staff_user")
    password: str = Field(..., min_length=1, example="StaffPass1!")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int

    model_config = {"json_schema_extra": {
        "example": {
            "access_token": "<JWT>",
            "refresh_token": "<JWT>",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_expires_in": 1800,
        }
    }}


class LogoutRequest(BaseModel):
    refresh_token: str = Field(
        ...,
        description="The refresh_token received from /auth/login.",
    )


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, example="jane_doe")
    email: EmailStr = Field(..., example="jane@msbn.gov")
    first_name: str = Field(..., min_length=1, max_length=128, example="Jane")
    last_name: str = Field(..., min_length=1, max_length=128, example="Doe")
    password: str = Field(
        ...,
        min_length=8,
        description="Initial password — must be changed by the user after first login.",
        example="Welcome1!",
    )
    role: Literal["msbn-staff", "msbn-admin"] = Field(
        default="msbn-staff",
        description="Realm role to assign. Defaults to msbn-staff.",
    )


class UserResponse(BaseModel):
    id: str
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None
    enabled: bool
    roles: list[str] = Field(description="msbn-* realm roles assigned to the user.")


class MeResponse(BaseModel):
    sub: str
    username: str
    email: str | None
    name: str | None
    roles: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_user_response(kc_user: dict[str, Any], roles: list[str]) -> UserResponse:
    return UserResponse(
        id=kc_user["id"],
        username=kc_user["username"],
        email=kc_user.get("email"),
        first_name=kc_user.get("firstName"),
        last_name=kc_user.get("lastName"),
        enabled=kc_user.get("enabled", True),
        roles=[r for r in roles if r.startswith("msbn-")],
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login — exchange credentials for tokens",
    description=(
        "Authenticates the user against Keycloak and returns a short-lived "
        "**access token** (used as `Authorization: Bearer <token>` on every "
        "subsequent request) and a **refresh token** (used to get a new access "
        "token without re-entering credentials). "
        "Works for both `msbn-staff` and `msbn-admin` accounts — the token "
        "carries the user's roles in the `realm_access.roles` claim."
    ),
)
async def login(body: LoginRequest) -> TokenResponse:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            settings.keycloak_token_uri,
            data={
                "grant_type": "password",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "username": body.username,
                "password": body.password,
                "scope": "openid",
            },
        )

    if resp.status_code in (401, 400):
        detail = resp.json().get("error_description", "Invalid credentials.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
    resp.raise_for_status()
    d = resp.json()
    return TokenResponse(
        access_token=d["access_token"],
        refresh_token=d["refresh_token"],
        token_type=d.get("token_type", "Bearer"),
        expires_in=d["expires_in"],
        refresh_expires_in=d.get("refresh_expires_in", 0),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Logout — revoke refresh token",
    description=(
        "Revokes the refresh token server-side in Keycloak, invalidating "
        "the session. The client must also discard the stored access token. "
        "Always returns 204 (idempotent — already-expired tokens are ignored)."
    ),
)
async def logout(
    body: LogoutRequest,
    _: Annotated[dict, Security(get_current_user)],
) -> Response:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(
            f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
            "/protocol/openid-connect/logout",
            data={
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "refresh_token": body.refresh_token,
            },
        )
    # Keycloak returns 204 on success or 400 if already expired — both are fine.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user profile",
    description="Returns the decoded claims from the caller's access token.",
)
async def me(
    current_user: Annotated[dict, Security(get_current_user)],
) -> MeResponse:
    roles: list[str] = current_user.get("realm_access", {}).get("roles", [])
    return MeResponse(
        sub=current_user.get("sub", ""),
        username=current_user.get("preferred_username", ""),
        email=current_user.get("email"),
        name=current_user.get("name"),
        roles=[r for r in roles if r.startswith("msbn-")],
    )


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new MSBN user (admin only)",
    description=(
        "**Requires `msbn-admin` role.** Self-signup is not permitted. "
        "The admin provides the initial password; the user should change it "
        "on first login. Role defaults to `msbn-staff`."
    ),
    dependencies=[Depends(require_role("msbn-admin"))],
)
async def create_user(
    body: CreateUserRequest,
    _: Annotated[dict, Security(get_current_user)],
) -> UserResponse:
    kc_user = await keycloak_admin.create_user(
        username=body.username,
        email=body.email,
        first_name=body.first_name,
        last_name=body.last_name,
        password=body.password,
        role=body.role,
    )
    roles = await keycloak_admin.get_user_realm_roles(kc_user["id"])
    return _to_user_response(kc_user, roles)


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List all MSBN users (admin only)",
    description=(
        "**Requires `msbn-admin` role.** "
        "Returns all users in the msbn Keycloak realm. "
        "Optionally filter by `search` (matches username, email, name)."
    ),
    dependencies=[Depends(require_role("msbn-admin"))],
)
async def list_users(
    search: str | None = Query(default=None, description="Filter by username, email, or name"),
    _: Annotated[dict, Security(get_current_user)] = None,
) -> list[UserResponse]:
    users = await keycloak_admin.list_users(search=search)
    result: list[UserResponse] = []
    for u in users:
        roles = await keycloak_admin.get_user_realm_roles(u["id"])
        result.append(_to_user_response(u, roles))
    return result
