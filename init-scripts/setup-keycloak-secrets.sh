#!/usr/bin/env bash
# ============================================================================
# Keycloak Client Secrets generieren und injizieren
# ============================================================================
# Generiert sichere Secrets (falls nicht in .env vorhanden), speichert sie
# in .env und setzt sie in Keycloak via Admin API. So müssen Secrets nicht
# in der lakehouse-realm.json hardcodiert sein.
#
# Verwendung: ./setup-keycloak-secrets.sh
# Wird automatisch von start.sh nach dem Keycloak-Start aufgerufen.
# ============================================================================
set -euo pipefail

KEYCLOAK_URL="http://localhost:8082"
REALM="lakehouse"
ENV_FILE=".env"

# --- Sichere Secrets generieren -----------------------------------------------
generate_secret() {
  # 48 Bytes = 64 Base64 Zeichen (sehr sicher)
  openssl rand -base64 48 | tr -d '\n'
}

# --- Credentials aus .env lesen -----------------------------------------------
KC_ADMIN="${KEYCLOAK_ADMIN_USER:-admin}"
KC_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin123}"
KC_SECRET_AIRFLOW="${KEYCLOAK_CLIENT_SECRET_AIRFLOW:-}"
KC_SECRET_MINIO="${KEYCLOAK_CLIENT_SECRET_MINIO:-}"
KC_SECRET_TRINO="${KEYCLOAK_CLIENT_SECRET_TRINO:-}"

if [ -f "$ENV_FILE" ]; then
  KC_ADMIN=$(grep "^KEYCLOAK_ADMIN_USER=" "$ENV_FILE" | cut -d'=' -f2- || echo "admin")
  KC_PASS=$(grep "^KEYCLOAK_ADMIN_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2- || echo "admin123")
  KC_SECRET_AIRFLOW=$(grep "^KEYCLOAK_CLIENT_SECRET_AIRFLOW=" "$ENV_FILE" | cut -d'=' -f2- || echo "")
  KC_SECRET_MINIO=$(grep "^KEYCLOAK_CLIENT_SECRET_MINIO=" "$ENV_FILE" | cut -d'=' -f2- || echo "")
  KC_SECRET_TRINO=$(grep "^KEYCLOAK_CLIENT_SECRET_TRINO=" "$ENV_FILE" | cut -d'=' -f2- || echo "")
fi

# --- Helper: Prüfe ob Secret leer oder Placeholder ist ---------------------
is_valid_secret() {
  local secret="$1"
  # Leer?
  if [ -z "$secret" ]; then return 1; fi
  # Placeholder-Pattern? (CHANGE_ME, TODO, REPLACE, xxx, ...)
  if [[ "$secret" =~ ^(CHANGE_ME|TODO|REPLACE|XXX|_|example|placeholder).*$ ]]; then
    return 1
  fi
  # Länger als 20 Zeichen (realistische Secrets sind lang)?
  if [ ${#secret} -lt 20 ]; then return 1; fi
  return 0
}

# --- Fehlende/ungültige Secrets generieren und in .env speichern -------------------------
SECRETS_UPDATED=false

if ! is_valid_secret "$KC_SECRET_AIRFLOW"; then
  KC_SECRET_AIRFLOW=$(generate_secret)
  if [ -f "$ENV_FILE" ]; then
    if grep -q "^KEYCLOAK_CLIENT_SECRET_AIRFLOW=" "$ENV_FILE"; then
      sed -i.bak "s|^KEYCLOAK_CLIENT_SECRET_AIRFLOW=.*|KEYCLOAK_CLIENT_SECRET_AIRFLOW=${KC_SECRET_AIRFLOW}|" "$ENV_FILE"
    else
      echo "KEYCLOAK_CLIENT_SECRET_AIRFLOW=${KC_SECRET_AIRFLOW}" >> "$ENV_FILE"
    fi
  fi
  echo "  ⚙ Neuer Secret für airflow generiert"
  SECRETS_UPDATED=true
fi

if ! is_valid_secret "$KC_SECRET_MINIO"; then
  KC_SECRET_MINIO=$(generate_secret)
  if [ -f "$ENV_FILE" ]; then
    if grep -q "^KEYCLOAK_CLIENT_SECRET_MINIO=" "$ENV_FILE"; then
      sed -i.bak "s|^KEYCLOAK_CLIENT_SECRET_MINIO=.*|KEYCLOAK_CLIENT_SECRET_MINIO=${KC_SECRET_MINIO}|" "$ENV_FILE"
    else
      echo "KEYCLOAK_CLIENT_SECRET_MINIO=${KC_SECRET_MINIO}" >> "$ENV_FILE"
    fi
  fi
  echo "  ⚙ Neuer Secret für minio generiert"
  SECRETS_UPDATED=true
fi

if ! is_valid_secret "$KC_SECRET_TRINO"; then
  KC_SECRET_TRINO=$(generate_secret)
  if [ -f "$ENV_FILE" ]; then
    if grep -q "^KEYCLOAK_CLIENT_SECRET_TRINO=" "$ENV_FILE"; then
      sed -i.bak "s|^KEYCLOAK_CLIENT_SECRET_TRINO=.*|KEYCLOAK_CLIENT_SECRET_TRINO=${KC_SECRET_TRINO}|" "$ENV_FILE"
    else
      echo "KEYCLOAK_CLIENT_SECRET_TRINO=${KC_SECRET_TRINO}" >> "$ENV_FILE"
    fi
  fi
  echo "  ⚙ Neuer Secret für trino generiert"
  SECRETS_UPDATED=true
fi

if [ "$SECRETS_UPDATED" = true ]; then
  echo "Neue Secrets in .env gespeichert"
  # Cleanup backup
  rm -f "${ENV_FILE}.bak"
fi

# --- Admin Token holen -------------------------------------------------------
echo "Hole Keycloak Admin Token..."
TOKEN=$(curl -sf -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=admin-cli" \
  -d "username=${KC_ADMIN}" \
  -d "password=${KC_PASS}" \
  -d "grant_type=password" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "WARNUNG: Keycloak Admin Token konnte nicht geholt werden – Secrets übersprungen."
  exit 0
fi

echo "Admin Token erhalten. Aktualisiere Client Secrets..."

# --- Client Secrets setzen ---------------------------------------------------
# Format: clientId|secretEnvironmentVariable
CLIENTS=(
  "airflow|KC_SECRET_AIRFLOW"
  "minio|KC_SECRET_MINIO"
  "trino|KC_SECRET_TRINO"
)

for entry in "${CLIENTS[@]}"; do
  IFS='|' read -r CLIENT_ID SECRET_VAR <<< "$entry"
  SECRET_VALUE="${!SECRET_VAR:-}"

  # Falls Secret leer ist, überspringe
  if [ -z "$SECRET_VALUE" ]; then
    echo "  ⊘ ${CLIENT_ID}: Kein Secret in .env definiert – übersprungen"
    continue
  fi

  # Client UUID holen
  CLIENT_UUID=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=${CLIENT_ID}" \
    -H "Authorization: Bearer ${TOKEN}" | python3 -c "import sys,json; clients=json.load(sys.stdin); print(clients[0]['id'] if clients else '')" 2>/dev/null)

  if [ -z "$CLIENT_UUID" ]; then
    echo "  ✗ ${CLIENT_ID}: Client nicht gefunden"
    continue
  fi

  # Client konfigurieren: confidential + secret setzen
  CLIENT_JSON=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
    -H "Authorization: Bearer ${TOKEN}" 2>/dev/null)

  UPDATED_JSON=$(echo "$CLIENT_JSON" | python3 -c "
import sys, json
client = json.load(sys.stdin)
client['clientAuthenticatorType'] = 'client-secret'
client['secret'] = '${SECRET_VALUE}'
print(json.dumps(client))
")

  HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X PUT \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$UPDATED_JSON" 2>/dev/null)

  if [ "$HTTP_CODE" = "204" ]; then
    echo "  ✓ ${CLIENT_ID}: Secret gesetzt"
  else
    echo "  ✗ ${CLIENT_ID}: Fehler (HTTP ${HTTP_CODE})"
  fi
done

echo "Keycloak Client Secrets aktualisiert."
