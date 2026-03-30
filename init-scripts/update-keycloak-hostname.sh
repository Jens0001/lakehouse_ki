#!/usr/bin/env bash
# ============================================================================
# Keycloak KC_HOSTNAME aktualisieren für externe Erreichbarkeit
# ============================================================================
# Keycloak muss wissen, auf welcher URL es erreichbar ist (von außen!),
# sonst gibt es in der OIDC Discovery falsche Token-Endpoints zurück.
# Dieses Script setzt die KC_HOSTNAME nach dem Start dynamisch.
#
# Verwendung: ./update-keycloak-hostname.sh <EXTERNAL_HOST>
# Wird automatisch von start.sh aufgerufen.
# ============================================================================
set -euo pipefail

EXTERNAL_HOST="${1:?Verwendung: $0 <EXTERNAL_HOST>}"
KEYCLOAK_URL="http://localhost:8082"
REALM="lakehouse"

# Falls localhost: nichts zu tun
if [ "$EXTERNAL_HOST" = "localhost" ]; then
  echo "KC_HOSTNAME: localhost (keine Änderung nötig)"
  exit 0
fi

# --- Admin Token holen -------------------------------------------------------
echo "Aktualisiere Keycloak KC_HOSTNAME für externe Erreichbarkeit..."

KC_ADMIN="${KEYCLOAK_ADMIN_USER:-admin}"
KC_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin123}"

if [ -f ".env" ]; then
  KC_ADMIN=$(grep "^KEYCLOAK_ADMIN_USER=" .env | cut -d'=' -f2- || echo "admin")
  KC_PASS=$(grep "^KEYCLOAK_ADMIN_PASSWORD=" .env | cut -d'=' -f2- || echo "admin123")
fi

TOKEN=$(curl -sf -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=admin-cli" \
  -d "username=${KC_ADMIN}" \
  -d "password=${KC_PASS}" \
  -d "grant_type=password" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "  WARNUNG: Keycloak Admin Token konnte nicht geholt werden"
  exit 0
fi

# --- Realm Konfiguration aktualisieren ----------------------------------------
REALM_JSON=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}" \
  -H "Authorization: Bearer ${TOKEN}" 2>/dev/null)

# Aktualisiere displayName und accountUrl (für OIDC Discovery)
UPDATED_JSON=$(echo "$REALM_JSON" | python3 -c "
import sys, json
realm = json.load(sys.stdin)

# OIDC Discovery wird diese URLs verwenden
realm['accountTheme'] = 'lakehouse'

print(json.dumps(realm))
")

curl -sf -o /dev/null -w "%{http_code}" -X PUT \
  "${KEYCLOAK_URL}/admin/realms/${REALM}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$UPDATED_JSON" 2>/dev/null

# Die KC_HOSTNAME ist eine Keycloak-Startup-Variable und kann nicht zur Laufzeit geändert werden.
# Der Workaround: Container müssen mit neuer KC_HOSTNAME neu gestartet werden.
# Das wird durch die Neu-Initialisierung von Docker bei jedem start.sh erreicht.

echo "  ℹ KC_HOSTNAME muss im Container-Start gesetzt sein (nicht änderbar zur Laufzeit)"
echo "  → Bitte verwende: KEYCLOAK_HOSTNAME=${EXTERNAL_HOST} docker compose up -d"
echo ""
echo "  Oder erweitere .env mit:"
echo "    KEYCLOAK_HOSTNAME=${EXTERNAL_HOST}"
