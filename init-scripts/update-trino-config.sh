#!/usr/bin/env bash
# ============================================================================
# Trino Keycloak-URLs aktualisieren für externe Erreichbarkeit
# ============================================================================
# Trino muss die richtige externe Keycloak-URL kennen für OIDC Discovery.
# Diese Config wird beim Start angepasst basierend auf EXTERNAL_HOST.
#
# Verwendung: ./update-trino-config.sh <EXTERNAL_HOST>
# Wird automatisch von start.sh aufgerufen.
# ============================================================================
set -euo pipefail

EXTERNAL_HOST="${1:?Verwendung: $0 <EXTERNAL_HOST>}"
TRINO_CONFIG="./trino/etc/config.properties"

if [ ! -f "$TRINO_CONFIG" ]; then
  echo "WARNUNG: Trino config nicht gefunden: $TRINO_CONFIG"
  exit 0
fi

echo "Aktualisiere Trino Keycloak-URLs für ${EXTERNAL_HOST}..."

# Ersetze Docker-interne keycloak-URL mit externer URL
sed -i.bak "s|http://keycloak:8082|http://${EXTERNAL_HOST}:8082|g" "$TRINO_CONFIG"

# Auch die KEYCLOAK_URL Variable aktualisieren (falls in der Datei)
sed -i "s|KEYCLOAK_URL=http://keycloak:8082|KEYCLOAK_URL=http://${EXTERNAL_HOST}:8082|g" "$TRINO_CONFIG"

echo "  ✓ Trino config aktualisiert: keycloak:8082 → ${EXTERNAL_HOST}:8082"
rm -f "${TRINO_CONFIG}.bak"
