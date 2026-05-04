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

# OM_URL automatisch an EXTERNAL_HOST anpassen
OM_URL_VAL="http://${EXTERNAL_HOST}:8585/api"
if grep -q '^OM_URL=' "$ENV_FILE"; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "s|^OM_URL=.*|OM_URL=${OM_URL_VAL}|" "$ENV_FILE"
  else
    sed -i "s|^OM_URL=.*|OM_URL=${OM_URL_VAL}|" "$ENV_FILE"
  fi
  echo "OM_URL in .env aktualisiert: ${OM_URL_VAL}"
else
  echo "OM_URL=${OM_URL_VAL}" >> "$ENV_FILE"
  echo "OM_URL zu .env hinzugefügt: ${OM_URL_VAL}"
fi

# KEYCLOAK_HOSTNAME automatisch setzen:
# - localhost → "keycloak" (Docker-interner Hostname; Container können "localhost" nicht auflösen)
# - LAN-IP  → IP direkt (Browser und Container können die IP erreichen)
if [ "${EXTERNAL_HOST}" = "localhost" ]; then
  KEYCLOAK_HOSTNAME_VAL="keycloak"
else
  KEYCLOAK_HOSTNAME_VAL="${EXTERNAL_HOST}"
fi
if grep -q '^KEYCLOAK_HOSTNAME=' "$ENV_FILE"; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "s|^KEYCLOAK_HOSTNAME=.*|KEYCLOAK_HOSTNAME=${KEYCLOAK_HOSTNAME_VAL}|" "$ENV_FILE"
  else
    sed -i "s|^KEYCLOAK_HOSTNAME=.*|KEYCLOAK_HOSTNAME=${KEYCLOAK_HOSTNAME_VAL}|" "$ENV_FILE"
  fi
  echo "KEYCLOAK_HOSTNAME in .env aktualisiert: ${KEYCLOAK_HOSTNAME_VAL}"
else
  echo "KEYCLOAK_HOSTNAME=${KEYCLOAK_HOSTNAME_VAL}" >> "$ENV_FILE"
  echo "KEYCLOAK_HOSTNAME zu .env hinzugefügt: ${KEYCLOAK_HOSTNAME_VAL}"
fi

# --- Pre-Start: Berechtigungen und Konfigurationen anpassen -------------------
echo ""
echo "Überprüfe und repariere Berechtigungen..."

mkdir -p ./airflow/logs 2>/dev/null || true
mkdir -p ./dbt/target 2>/dev/null || true
mkdir -p ./dbt/logs 2>/dev/null || true
mkdir -p /dbt/dbt_packages 2>/dev/null || true

# Airflow DAG- und Logs-Verzeichnis müssen vom airflow-User (im Container)
# beschreibbar sein. Setze auf 777 um Permission-Probleme zu vermeiden.
# __pycache__ wird ausgeschlossen (vom Container erstellt, nicht änderbar vom Host)
if [ -d "./airflow/dags" ]; then
  find ./airflow/dags -not -path '*/__pycache__/*' -exec chmod 777 {} + 2>/dev/null || true
  echo "  ✓ ./airflow/dags: Berechtigungen auf 777 gesetzt (ignoriert: __pycache__)"
fi

if [ -d "./airflow/logs" ]; then
  find ./airflow/logs -not -path '*/__pycache__/*' -exec chmod 777 {} + 2>/dev/null || true
  echo "  ✓ ./airflow/logs: Berechtigungen auf 777 gesetzt (ignoriert: __pycache__)"
fi

if [ -d "./dbt" ]; then
  sudo find ./dbt -not -path '*/__pycache__/*' -exec chmod 777 {} + 2>/dev/null || true
  echo "  ✓ ./dbt: Berechtigungen auf 777 gesetzt (ignoriert: __pycache__)"
fi

# Elasticsearch Wrapper-Script muss ausführbar sein (wird als Volume gemountet)
# Ohne chmod +x crasht der Start, da das gemountete Script keine Execute-Berechtigung hat
if [ -f "./elasticsearch/elasticsearch-wrapper.sh" ]; then
  chmod 777 ./elasticsearch/elasticsearch-wrapper.sh 2>/dev/null || true
  echo "  ✓ ./elasticsearch/elasticsearch-wrapper.sh: Berechtigungen auf 777 gesetzt"
fi

echo ""
echo "Überprüfe Airflow Fernet Key..."

# Airflow braucht einen gültigen Fernet Key für Encryption
# Generiere einen neuen, wenn:
# - Key nicht vorhanden
# - Key ist ein Dummy (CHANGE_ME, TODO, leer)
# - Key hat falsche Länge (sollte ~44 Base64-Zeichen sein)

FERNET_KEY=$(grep '^AIRFLOW_FERNET_KEY=' "$ENV_FILE" | cut -d'=' -f2- | tr -d ' ')

is_valid_fernet() {
  local key="$1"
  # Prüfe: nicht leer, nicht Dummy, richtige Länge (Fernet Keys sind ~44 Zeichen Base64)
  if [ -z "$key" ] || [[ "$key" =~ ^(CHANGE_ME|TODO) ]]; then
    return 1
  fi
  # Basis-Check: sollte min. 40 Zeichen sein
  if [ ${#key} -lt 40 ]; then
    return 1
  fi
  return 0
}

if ! is_valid_fernet "$FERNET_KEY"; then
  echo "  ⚠️  Airflow Fernet Key ungültig oder fehlt, generiere neuen..."
  NEW_FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null)
  if [ -z "$NEW_FERNET_KEY" ]; then
    echo "  ❌ Konnte Fernet Key nicht generieren. Installiere: pip install cryptography"
    exit 1
  fi

  # Aktualisiere oder füge hinzu
  if grep -q '^AIRFLOW_FERNET_KEY=' "$ENV_FILE"; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
      sed -i '' "s|^AIRFLOW_FERNET_KEY=.*|AIRFLOW_FERNET_KEY=${NEW_FERNET_KEY}|" "$ENV_FILE"
    else
      sed -i "s|^AIRFLOW_FERNET_KEY=.*|AIRFLOW_FERNET_KEY=${NEW_FERNET_KEY}|" "$ENV_FILE"
    fi
    echo "  ✓ Airflow Fernet Key aktualisiert"
  else
    echo "AIRFLOW_FERNET_KEY=${NEW_FERNET_KEY}" >> "$ENV_FILE"
    echo "  ✓ Airflow Fernet Key zu .env hinzugefügt"
  fi
else
  echo "  ✓ Airflow Fernet Key ist gültig"
fi

# --- Docker Compose starten -------------------------------------------------
echo ""
echo "Starte Stack..."

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

  # --- Business Glossary Ingestion ---------------------------------------------
  echo ""
  echo "Importiere Business Glossary..."
  # Lade OM-Variablen aus .env, damit das Python-Skript sie sieht
  if [ -f "$ENV_FILE" ]; then
    export OM_URL=$(grep '^OM_URL=' "$ENV_FILE" | cut -d'=' -f2- | tr -d ' ' || echo "http://localhost:8585/api")
    export OM_TOKEN=$(grep '^OM_TOKEN=' "$ENV_FILE" | cut -d'=' -f2- | tr -d ' ' || echo "")
  fi

  if [ -n "$OM_TOKEN" ] && [[ "$OM_TOKEN" != "CHANGE_ME"* ]]; then
    python3 scripts/om_glossary_ingest.py glossary_structure.json || echo "  ⚠️  Glossary-Ingestion fehlgeschlagen (evtl. OM noch nicht voll bereit)"
  else
    echo "  ⚠️  OM_TOKEN fehlt oder ist noch ein Platzhalter (CHANGE_ME) in der .env."
    echo "      Bitte generieren Sie ein Token in der OM-UI: Settings -> Users -> [User] -> Token"
    echo "      Und tragen Sie es als OM_TOKEN in die .env ein."
    echo "      Glossary-Ingestion wird übersprungen."
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
