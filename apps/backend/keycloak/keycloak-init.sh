#!/bin/bash
# Keycloak startup wrapper — single responsibility: patch master realm SSL.
#
# Problem: master realm defaults to sslRequired=EXTERNAL. Docker port-mapping
# routes host and inter-container traffic via bridge IPs that Keycloak treats
# as "external" → HTTPS required.
#
# Fix: connect from 127.0.0.1 inside this container (genuinely localhost,
# bypasses the EXTERNAL check) and set sslRequired=none on master realm.
# User password provisioning is handled by the init-keycloak sidecar service.

ADMIN="${KEYCLOAK_ADMIN:-admin}"
PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin}"
KCADM="/opt/keycloak/bin/kcadm.sh"

log() { echo "[keycloak-init] $*"; }

log "Starting Keycloak in background..."
/opt/keycloak/bin/kc.sh start-dev --import-realm &
KC_PID=$!

log "Waiting for port 8080 to open..."
for i in $(seq 1 90); do
    (echo > /dev/tcp/127.0.0.1/8080) 2>/dev/null && break
    sleep 2
done
sleep 5  # Let the HTTP layer finish initialising
log "Keycloak is accepting connections."

log "Authenticating with kcadm.sh from 127.0.0.1..."
"$KCADM" config credentials \
    --server http://127.0.0.1:8080 \
    --realm master \
    --user "$ADMIN" \
    --password "$PASS" 2>&1

log "Patching master realm: sslRequired → none (enables HTTP from all IPs)..."
RESULT=$("$KCADM" update realms/master -s sslRequired=none 2>&1)
log "patch: ${RESULT:-OK}"

log "SSL patched. User passwords will be set by the init-keycloak sidecar."
log "Foregrounding Keycloak (PID $KC_PID)..."
wait $KC_PID
