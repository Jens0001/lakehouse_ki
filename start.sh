#!/usr/bin/env bash
# ============================================================================
# Lakehouse KI – Start-Script mit automatischer EXTERNAL_HOST-Erkennung
# ============================================================================
# Erkennt die Netzwerk-IP und setzt EXTERNAL_HOST in .env, damit alle
# Browser-Redirects (MinIO, Keycloak OIDC) auf die richtige Adresse zeigen.
#
# Verwendung:
#   ./start.sh              # Automatische IP-Erkennung
#   ./start.sh 192.168.1.50 # Manuelle IP/Hostname
#   ./start.sh localhost    # Explizit localhost (lokale Entwicklung)
# ============================================================================
set -euo pipefail

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"

# --- IP-Erkennung -----------------------------------------------------------
detect_ip() {
  local ip=""
  case "$(uname -s)" in
    Linux*)
      # Erste nicht-localhost IPv4-Adresse
      ip=$(hostname -I 2>/dev/null | awk '{print $1}')
      ;;
    Darwin*)
      # macOS: aktives Interface (en0 = WLAN, en1 = Ethernet)
      ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
      ;;
  esac
  echo "${ip:-localhost}"
}

if [ $# -ge 1 ]; then
  EXTERNAL_HOST="$1"
else
  EXTERNAL_HOST=$(detect_ip)
fi

echo "=== Lakehouse KI Start ==="
echo "EXTERNAL_HOST: ${EXTERNAL_HOST}"

# --- .env aktualisieren -----------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  echo "FEHLER: $ENV_FILE nicht gefunden. Bitte erst 'cp .env.example .env' ausführen."
  exit 1
fi

# EXTERNAL_HOST in .env setzen (ersetzen oder hinzufügen)
if grep -q '^EXTERNAL_HOST=' "$ENV_FILE"; then
  # Bestehenden Wert ersetzen (plattformübergreifend)
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "s|^EXTERNAL_HOST=.*|EXTERNAL_HOST=${EXTERNAL_HOST}|" "$ENV_FILE"
  else
    sed -i "s|^EXTERNAL_HOST=.*|EXTERNAL_HOST=${EXTERNAL_HOST}|" "$ENV_FILE"
  fi
  echo "EXTERNAL_HOST in .env aktualisiert: ${EXTERNAL_HOST}"
else
  # Variable existiert noch nicht → am Anfang einfügen
  echo "EXTERNAL_HOST=${EXTERNAL_HOST}" | cat - "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  echo "EXTERNAL_HOST zu .env hinzugefügt: ${EXTERNAL_HOST}"
fi

# --- Docker Compose starten -------------------------------------------------
echo ""
echo "Starte Stack..."
docker compose up -d --build

# --- Keycloak Redirect URIs aktualisieren ------------------------------------
if [ "${EXTERNAL_HOST}" != "localhost" ]; then
  echo ""
  echo "Warte auf Keycloak Health..."
  # Max 120s warten
  for i in $(seq 1 24); do
    if docker exec lakehouse_keycloak bash -c "exec 3<>/dev/tcp/localhost/9000 && echo -e 'GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3 && timeout 2 cat <&3 | grep -q '200 OK'" 2>/dev/null; then
      echo "Keycloak ist bereit."
      echo "Aktualisiere Keycloak Redirect URIs für ${EXTERNAL_HOST}..."
      bash init-scripts/update-keycloak-redirects.sh "${EXTERNAL_HOST}"
      break
    fi
    echo "  Warte... ($((i*5))s)"
    sleep 5
  done
fi

# --- Hinweise ----------------------------------------------------------------
echo ""
echo "=== Stack gestartet ==="
echo ""
echo "Service-URLs:"
echo "  MinIO Console:  http://${EXTERNAL_HOST}:9001"
echo "  Airflow:        http://${EXTERNAL_HOST}:8081"
echo "  Trino UI:       https://${EXTERNAL_HOST}:8443"
echo "  Keycloak Admin: http://${EXTERNAL_HOST}:8082"
echo "  OpenMetadata:   http://${EXTERNAL_HOST}:8585"
echo "  Dremio:         http://${EXTERNAL_HOST}:9047"

if [ "${EXTERNAL_HOST}" != "localhost" ]; then
  echo ""
  echo "⚠️  WICHTIG: Jeder Client-Rechner braucht folgenden /etc/hosts Eintrag:"
  echo "    ${EXTERNAL_HOST} keycloak"
  echo ""
  echo "  Auf dem Server selbst:"
  echo "    echo '127.0.0.1 keycloak' | sudo tee -a /etc/hosts"
  echo ""
  echo "  Auf jedem Client-Rechner:"
  echo "    echo '${EXTERNAL_HOST} keycloak' | sudo tee -a /etc/hosts"
fi
