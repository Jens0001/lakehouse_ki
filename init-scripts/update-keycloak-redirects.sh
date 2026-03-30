#!/usr/bin/env bash
# ============================================================================
# Keycloak Redirect URIs aktualisieren für EXTERNAL_HOST
# ============================================================================
# Fügt Redirect URIs für den angegebenen Host zu allen OIDC-Clients hinzu.
# Bestehende localhost-Einträge bleiben erhalten.
#
# Verwendung: ./update-keycloak-redirects.sh <EXTERNAL_HOST>
# Wird automatisch von start.sh aufgerufen.
# ============================================================================
set -euo pipefail

EXTERNAL_HOST="${1:?Verwendung: $0 <EXTERNAL_HOST>}"
KEYCLOAK_URL="http://localhost:8082"

# Credentials aus .env lesen (Fallback auf Defaults)
KC_ADMIN="${KEYCLOAK_ADMIN_USER:-admin}"
KC_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin123}"
REALM="lakehouse"

# --- Admin Token holen -------------------------------------------------------
TOKEN=$(curl -sf -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=admin-cli" \
  -d "username=${KC_ADMIN}" \
  -d "password=${KC_PASS}" \
  -d "grant_type=password" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

if [ -z "$TOKEN" ]; then
  echo "FEHLER: Keycloak Admin Token konnte nicht geholt werden."
  exit 1
fi

echo "Keycloak Admin Token erhalten."

# --- Client Redirect URIs aktualisieren --------------------------------------
# Format: clientId|redirectUri(s, kommasepariert)|webOrigin(s, kommasepariert)|rootUrl|adminUrl
CLIENTS=(
  "minio|http://${EXTERNAL_HOST}:9001/oauth_callback|http://${EXTERNAL_HOST}:9001|http://${EXTERNAL_HOST}:9001|http://${EXTERNAL_HOST}:9001"
  "airflow|http://${EXTERNAL_HOST}:8081/auth/oauth-authorized/keycloak|http://${EXTERNAL_HOST}:8081|http://${EXTERNAL_HOST}:8081|http://${EXTERNAL_HOST}:8081"
  "trino|https://${EXTERNAL_HOST}:8443/oauth2/callback,https://${EXTERNAL_HOST}:8443/ui/api/login/oauth2/callback,http://${EXTERNAL_HOST}:8080/oauth2/callback|https://${EXTERNAL_HOST}:8443,http://${EXTERNAL_HOST}:8080|https://${EXTERNAL_HOST}:8443|https://${EXTERNAL_HOST}:8443"
)

for entry in "${CLIENTS[@]}"; do
  IFS='|' read -r CLIENT_ID NEW_REDIRECTS NEW_ORIGINS NEW_ROOT_URL NEW_ADMIN_URL <<< "$entry"

  # Client UUID holen
  CLIENT_UUID=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=${CLIENT_ID}" \
    -H "Authorization: Bearer ${TOKEN}" | python3 -c "import sys,json; clients=json.load(sys.stdin); print(clients[0]['id'] if clients else '')")

  if [ -z "$CLIENT_UUID" ]; then
    echo "  WARNUNG: Client '${CLIENT_ID}' nicht gefunden – übersprungen."
    continue
  fi

  # Bestehende Config holen
  CLIENT_JSON=$(curl -sf "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
    -H "Authorization: Bearer ${TOKEN}")

  # Redirect URIs, Web Origins, Root URL und Admin URL aktualisieren
  UPDATED_JSON=$(echo "$CLIENT_JSON" | python3 -c "
import sys, json

client = json.load(sys.stdin)
new_redirects = '${NEW_REDIRECTS}'.split(',')
new_origins = '${NEW_ORIGINS}'.split(',')
new_root_url = '${NEW_ROOT_URL}'
new_admin_url = '${NEW_ADMIN_URL}'

# Bestehende URIs beibehalten, neue hinzufügen
redirects = list(dict.fromkeys(client.get('redirectUris', []) + new_redirects))
origins = list(dict.fromkeys(client.get('webOrigins', []) + new_origins))

# Root URL und Admin URL setzen
client['rootUrl'] = new_root_url
client['adminUrl'] = new_admin_url

# post.logout.redirect.uris aktualisieren (##-getrennt in Keycloak)
attrs = client.get('attributes', {})
existing_logout = attrs.get('post.logout.redirect.uris', '')
logout_uris = [u for u in existing_logout.split('##') if u]
for r in new_redirects:
    base = r.rsplit('/', 1)[0]
    logout_uri = base + '/*'
    if logout_uri not in logout_uris:
        logout_uris.append(logout_uri)
attrs['post.logout.redirect.uris'] = '##'.join(logout_uris)

client['redirectUris'] = redirects
client['webOrigins'] = origins
client['attributes'] = attrs
print(json.dumps(client))
")

  # Client aktualisieren
  HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -X PUT \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$UPDATED_JSON")

  if [ "$HTTP_CODE" = "204" ]; then
    echo "  ✓ ${CLIENT_ID}: Redirect URIs für ${EXTERNAL_HOST} hinzugefügt"
  else
    echo "  ✗ ${CLIENT_ID}: Fehler (HTTP ${HTTP_CODE})"
  fi
done

echo "Keycloak Redirect URIs aktualisiert."
