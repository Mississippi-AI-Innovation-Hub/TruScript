#!/bin/sh
# Runs once after Keycloak is healthy (via init-keycloak sidecar).
#
# 1. Sets plain-text passwords for the two seed test users.
#    (Keycloak 24 silently drops plain-text credentials on realm JSON import.)
#
# 2. Grants the msbn-api service account the realm-management roles it needs
#    so the API can create/list/manage users via the admin REST API using
#    client_credentials (no master-admin secrets stored in the API container).
#    Roles granted: manage-users, view-users, query-users
set -e

KC="http://keycloak:8080"

log() { echo "[kc-provision] $*"; }

# ── 1. Get master-realm admin token ──────────────────────────────────────────
log "Getting admin token..."
TOKEN=$(curl -sf -X POST "$KC/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" \
    | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

[ -z "$TOKEN" ] && log "ERROR: could not obtain admin token." && exit 1
log "Token obtained."

# ── 2. Set seed user passwords ────────────────────────────────────────────────
set_password() {
    USERNAME="$1"; PASSWORD="$2"
    USER_ID=$(curl -sf -H "Authorization: Bearer $TOKEN" \
        "$KC/admin/realms/msbn/users?username=$USERNAME" \
        | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ -z "$USER_ID" ] && log "ERROR: user $USERNAME not found." && return 1
    curl -sf -X PUT "$KC/admin/realms/msbn/users/$USER_ID/reset-password" \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"password\",\"value\":\"$PASSWORD\",\"temporary\":false}"
    log "Password set for $USERNAME."
}

set_password "staff_user" "StaffPass1!"
set_password "admin_user" "AdminPass1!"

# ── 3. Grant service account roles for user management ───────────────────────
log "Resolving realm-management client in msbn realm..."
RM_ID=$(curl -sf -H "Authorization: Bearer $TOKEN" \
    "$KC/admin/realms/msbn/clients?clientId=realm-management" \
    | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -z "$RM_ID" ] && log "ERROR: realm-management client not found." && exit 1
log "realm-management client ID: $RM_ID"

log "Resolving msbn-api client..."
MSBN_API_ID=$(curl -sf -H "Authorization: Bearer $TOKEN" \
    "$KC/admin/realms/msbn/clients?clientId=msbn-api" \
    | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -z "$MSBN_API_ID" ] && log "ERROR: msbn-api client not found." && exit 1

log "Resolving msbn-api service account user..."
SA_USER_ID=$(curl -sf \
    -H "Authorization: Bearer $TOKEN" \
    "$KC/admin/realms/msbn/clients/$MSBN_API_ID/service-account-user" \
    | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -z "$SA_USER_ID" ] && log "ERROR: service account user not found." && exit 1
log "Service account user ID: $SA_USER_ID"

# Build a JSON array of the roles to assign
fetch_role() {
    ROLE_NAME="$1"
    curl -sf -H "Authorization: Bearer $TOKEN" \
        "$KC/admin/realms/msbn/clients/$RM_ID/roles/$ROLE_NAME"
}

MANAGE_USERS=$(fetch_role "manage-users")
VIEW_USERS=$(fetch_role "view-users")
QUERY_USERS=$(fetch_role "query-users")
VIEW_REALM=$(fetch_role "view-realm")

ROLES_JSON="[$MANAGE_USERS,$VIEW_USERS,$QUERY_USERS,$VIEW_REALM]"

log "Assigning manage-users, view-users, query-users, view-realm to msbn-api service account..."
curl -sf -X POST \
    "$KC/admin/realms/msbn/users/$SA_USER_ID/role-mappings/clients/$RM_ID" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$ROLES_JSON"
log "Service account roles granted (manage-users, view-users, query-users, view-realm)."

log "All provisioning complete."
