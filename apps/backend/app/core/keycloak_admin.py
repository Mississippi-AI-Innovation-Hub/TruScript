"""
Keycloak Admin REST API client.

Uses the msbn-api service account (client_credentials grant) to perform
privileged operations: create users, assign roles, list users.

The service account must have the following roles from the realm-management
client in the msbn realm (granted by keycloak/provision-users.sh):
  - manage-users
  - view-users
  - query-users
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings


# ── Service account token ─────────────────────────────────────────────────────

async def _get_admin_token() -> str:
    """Obtain a short-lived access token via the msbn-api service account."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            settings.keycloak_token_uri,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
            },
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not obtain service account token from Keycloak.",
        )
    return resp.json()["access_token"]


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _base(settings) -> str:
    return f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}"


# ── User operations ───────────────────────────────────────────────────────────

async def create_user(
    *,
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    password: str,
    role: str = "msbn-staff",
) -> dict[str, Any]:
    """
    Create a new user in the msbn realm, set their password, and assign
    the requested realm role. Returns the Keycloak user representation.
    """
    settings = get_settings()
    base = _base(settings)
    token = await _get_admin_token()
    headers = _admin_headers(token)

    async with httpx.AsyncClient(timeout=15.0) as client:
        # ── 1. Create the user record ─────────────────────────────────────────
        create_resp = await client.post(
            f"{base}/users",
            headers=headers,
            json={
                "username": username,
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
                "enabled": True,
                "emailVerified": True,
            },
        )
        if create_resp.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{username}' already exists.",
            )
        if create_resp.status_code not in (201, 200):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Keycloak user creation failed ({create_resp.status_code}).",
            )

        # User ID is in the Location header: …/users/{uuid}
        location = create_resp.headers.get("Location", "")
        user_id = location.rstrip("/").split("/")[-1]
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Keycloak did not return a user ID.",
            )

        # ── 2. Set initial password ───────────────────────────────────────────
        pw_resp = await client.put(
            f"{base}/users/{user_id}/reset-password",
            headers=headers,
            json={"type": "password", "value": password, "temporary": False},
        )
        if pw_resp.status_code not in (204, 200):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to set user password.",
            )

        # ── 3. Resolve the realm role ─────────────────────────────────────────
        role_resp = await client.get(f"{base}/roles/{role}", headers=headers)
        if role_resp.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Role '{role}' does not exist in the msbn realm.",
            )
        role_resp.raise_for_status()
        role_obj = role_resp.json()

        # ── 4. Assign the realm role ──────────────────────────────────────────
        assign_resp = await client.post(
            f"{base}/users/{user_id}/role-mappings/realm",
            headers=headers,
            json=[{"id": role_obj["id"], "name": role_obj["name"]}],
        )
        assign_resp.raise_for_status()

        # ── 5. Fetch and return the full user object ──────────────────────────
        user_resp = await client.get(f"{base}/users/{user_id}", headers=headers)
        user_resp.raise_for_status()
        return user_resp.json()


async def list_users(search: str | None = None) -> list[dict[str, Any]]:
    """Return up to 100 users from the msbn realm, optionally filtered by search."""
    settings = get_settings()
    base = _base(settings)
    token = await _get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    params: dict[str, Any] = {"max": 100}
    if search:
        params["search"] = search

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base}/users", headers=headers, params=params)
        resp.raise_for_status()
    return resp.json()


async def get_user_realm_roles(user_id: str) -> list[str]:
    """Return the list of realm role names assigned to a user."""
    settings = get_settings()
    base = _base(settings)
    token = await _get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{base}/users/{user_id}/role-mappings/realm",
            headers=headers,
        )
        resp.raise_for_status()
    return [r["name"] for r in resp.json()]
