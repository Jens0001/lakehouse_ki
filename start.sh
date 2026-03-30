#!/usr/bin/env bash
# ============================================================================
# Lakehouse KI – Start-Script mit automatischer EXTERNAL_HOST-Erkennung
# ============================================================================
# Erkennt die Netzwerk-IP und setzt EXTERNAL_HOST in .env, damit alle
# Browser-Redirects (MinIO, Keycloak OIDC) auf die richtige Adresse zeigen.
#
# Verwendung:
#   ./start.sh                    # Automatische IP-Erkennung (schneller Start)
#   ./start.sh 192.168.1.50       # Manuelle IP/Hostname
#   ./start.sh localhost          # Explizit localhost (lokale Entwicklung)
#   ./start.sh --build            # Mit Docker Image Rebuild (nach Code-Änderungen)
#   ./start.sh 192.168.1.50 --build # IP + Rebuild
#
# Hinweise:
#   - --build ist nur nötig nach Airflow Dockerfile/Dependency-Änderungen
#   - DAG/Plugin-Änderungen brauchen KEIN --build (schneller Restart)
# ============================================================================
set -euo pipefail

COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
BUILD_FLAG=""

# --- Parameter-Verarbeitung --------------------------------------------------
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

# Parse arguments: IP kann 1. oder 2. Argument sein, --build ist optional
EXTERNAL_HOST=""
for arg in "$@"; do
  if [ "$arg" = "--build" ]; then
    BUILD_FLAG="--build"
  elif [ -z "$EXTERNAL_HOST" ]; then
    EXTERNAL_HOST="$arg"
  fi
done

# Falls keine IP angegeben, auto-detect
if [ -z "$EXTERNAL_HOST" ]; then
  EXTERNAL_HOST=$(detect_ip)
fi

echo "=== Lakehouse KI Start ==="
echo "EXTERNAL_HOST: ${EXTERNAL_HOST}"
if [ -n "$BUILD_FLAG" ]; then
  echo "Build Mode: JA (--build)"
else
  echo "Build Mode: NEIN (schneller Start)"
fi

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

# --- Pre-Start: Konfigurationen anpassen -------------------------------------
echo ""
echo "Aktualisiere Service-Konfigurationen..."

# Trino muss die richtige externe Keycloak-URL kennen (BEVOR Container starten!)
if [ "${EXTERNAL_HOST}" != "localhost" ]; then
  bash init-scripts/update-trino-config.sh "${EXTERNAL_HOST}"
fi

# --- Docker Compose starten -------------------------------------------------
echo ""
echo "Starte Stack..."

# Keycloak muss wissen, auf welcher URL es erreichbar ist (für OIDC Discovery)
# Falls nicht localhost, nutze EXTERNAL_HOST; sonst nutze "keycloak" (Docker-intern)
if [ "${EXTERNAL_HOST}" = "localhost" ]; then
  export KEYCLOAK_HOSTNAME="keycloak"
  export KEYCLOAK_URL="http://keycloak:8082"
else
  export KEYCLOAK_HOSTNAME="${EXTERNAL_HOST}"
  export KEYCLOAK_URL="http://${EXTERNAL_HOST}:8082"
fi

if [ -n "$BUILD_FLAG" ]; then
  echo "  (mit Docker Image Rebuild)"
  docker compose up -d --build
else
  echo "  (ohne Rebuild – schneller Start)"
  docker compose up -d
fi

# --- Keycloak Konfigurationen nach Start aktualisieren -------------------------
echo ""
echo "Warte auf Keycloak Health..."
# Max 120s warten
KEYCLOAK_READY=false
for i in $(seq 1 24); do
  if docker exec lakehouse_keycloak bash -c "exec 3<>/dev/tcp/localhost/9000 && echo -e 'GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3 && timeout 2 cat <&3 | grep -q '200 OK'" 2>/dev/null; then
    echo "Keycloak ist bereit."
    KEYCLOAK_READY=true
    break
  fi
  echo "  Warte... ($((i*5))s)"
  sleep 5
done

if [ "$KEYCLOAK_READY" = true ]; then
  # Secrets aus .env injizieren (immer, auch bei localhost)
  echo "Injiziere Keycloak Client Secrets..."
  bash init-scripts/setup-keycloak-secrets.sh

  # Redirect URIs aktualisieren (nur bei nicht-localhost)
  if [ "${EXTERNAL_HOST}" != "localhost" ]; then
    echo "Aktualisiere Keycloak Redirect URIs für ${EXTERNAL_HOST}..."
    bash init-scripts/update-keycloak-redirects.sh "${EXTERNAL_HOST}"
  fi
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
