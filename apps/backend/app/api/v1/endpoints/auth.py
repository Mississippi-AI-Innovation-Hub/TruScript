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

import asyncio
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
                "scope": "openid profile email",
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
    try:
        users = await keycloak_admin.list_users(search=search)
    except Exception:
        # Keycloak admin unavailable — return empty list so UI degrades gracefully
        return []

    async def _fetch_roles(user_id: str) -> list[str]:
        try:
            return await keycloak_admin.get_user_realm_roles(user_id)
        except Exception:
            return []

    # Fetch all role lists concurrently instead of sequentially
    roles_list = await asyncio.gather(*[_fetch_roles(u["id"]) for u in users])
    return [_to_user_response(u, roles) for u, roles in zip(users, roles_list)]


@router.get(
    "/staff",
    response_model=list[UserResponse],
    summary="List staff available for assignment",
    description=(
        "Returns all **enabled** users who hold an `msbn-*` role. "
        "Accessible to any authenticated user (staff or admin) so that "
        "reviewers can be selected when assigning a verification."
    ),
)
async def list_staff(
    _current_user: Annotated[dict, Security(get_current_user)],
) -> list[UserResponse]:
    """
    Returns all enabled Keycloak users with at least one msbn-* realm role.
    Used by the assignment UI to populate the reviewer dropdown.
    Falls back to a mock list if Keycloak is not reachable (dev/demo mode).
    """
    try:
        users = await keycloak_admin.list_users(search=None)
    except Exception:
        # Keycloak not yet configured — return a deterministic mock list for demo
        return _mock_staff_list()

    enabled_users = [u for u in users if u.get("enabled", True)]

    async def _fetch_roles_safe(user_id: str) -> list[str]:
        try:
            return await keycloak_admin.get_user_realm_roles(user_id)
        except Exception:
            return []

    # Fetch all role lists concurrently
    roles_list = await asyncio.gather(*[_fetch_roles_safe(u["id"]) for u in enabled_users])

    result: list[UserResponse] = [
        _to_user_response(u, roles)
        for u, roles in zip(enabled_users, roles_list)
        if any(r.startswith("msbn-") for r in roles)
    ]

    # If Keycloak returned no users (realm not yet bootstrapped), fall back to mocks
    return result if result else _mock_staff_list()


def _mock_staff_list() -> list[UserResponse]:
    """Deterministic demo staff — shown when Keycloak realm is not yet set up."""
    return [
        UserResponse(
            id="mock-staff-001",
            username="j.anderson",
            email="j.anderson@msbn.gov",
            first_name="Jamie",
            last_name="Anderson",
            enabled=True,
            roles=["msbn-staff"],
        ),
        UserResponse(
            id="mock-staff-002",
            username="m.chen",
            email="m.chen@msbn.gov",
            first_name="Morgan",
            last_name="Chen",
            enabled=True,
            roles=["msbn-staff"],
        ),
        UserResponse(
            id="mock-staff-003",
            username="r.patel",
            email="r.patel@msbn.gov",
            first_name="Riley",
            last_name="Patel",
            enabled=True,
            roles=["msbn-staff"],
        ),
        UserResponse(
            id="mock-admin-001",
            username="s.williams",
            email="s.williams@msbn.gov",
            first_name="Sam",
            last_name="Williams",
            enabled=True,
            roles=["msbn-admin"],
        ),
        UserResponse(
            id="mock-staff-004",
            username="t.nguyen",
            email="t.nguyen@msbn.gov",
            first_name="Taylor",
            last_name="Nguyen",
            enabled=True,
            roles=["msbn-staff"],
        ),
    ]
