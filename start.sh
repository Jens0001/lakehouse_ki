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

# Portabler sed -i: Linux braucht kein Suffix-Argument, macOS schon.
sed_inplace() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "$@"
  else
    sed -i "$@"
  fi
}

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
  sed_inplace "s|^EXTERNAL_HOST=.*|EXTERNAL_HOST=${EXTERNAL_HOST}|" "$ENV_FILE"
  echo "EXTERNAL_HOST in .env aktualisiert: ${EXTERNAL_HOST}"
else
  # Variable existiert noch nicht → am Anfang einfügen
  echo "EXTERNAL_HOST=${EXTERNAL_HOST}" | cat - "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  echo "EXTERNAL_HOST zu .env hinzugefügt: ${EXTERNAL_HOST}"
fi

# OM_URL automatisch an EXTERNAL_HOST anpassen
OM_URL_VAL="http://${EXTERNAL_HOST}:8585/api"
if grep -q '^OM_URL=' "$ENV_FILE"; then
  sed_inplace "s|^OM_URL=.*|OM_URL=${OM_URL_VAL}|" "$ENV_FILE"
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
KEYCLOAK_URL_VAL="http://${KEYCLOAK_HOSTNAME_VAL}:8082"

if grep -q '^KEYCLOAK_HOSTNAME=' "$ENV_FILE"; then
  sed_inplace "s|^KEYCLOAK_HOSTNAME=.*|KEYCLOAK_HOSTNAME=${KEYCLOAK_HOSTNAME_VAL}|" "$ENV_FILE"
else
  echo "KEYCLOAK_HOSTNAME=${KEYCLOAK_HOSTNAME_VAL}" >> "$ENV_FILE"
fi
echo "KEYCLOAK_HOSTNAME in .env aktualisiert: ${KEYCLOAK_HOSTNAME_VAL}"

if grep -q '^KEYCLOAK_URL=' "$ENV_FILE"; then
  sed_inplace "s|^KEYCLOAK_URL=.*|KEYCLOAK_URL=${KEYCLOAK_URL_VAL}|" "$ENV_FILE"
else
  echo "KEYCLOAK_URL=${KEYCLOAK_URL_VAL}" >> "$ENV_FILE"
fi
echo "KEYCLOAK_URL in .env aktualisiert: ${KEYCLOAK_URL_VAL}"

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
    sed_inplace "s|^AIRFLOW_FERNET_KEY=.*|AIRFLOW_FERNET_KEY=${NEW_FERNET_KEY}|" "$ENV_FILE"
    echo "  ✓ Airflow Fernet Key aktualisiert"
  else
    echo "AIRFLOW_FERNET_KEY=${NEW_FERNET_KEY}" >> "$ENV_FILE"
    echo "  ✓ Airflow Fernet Key zu .env hinzugefügt"
  fi
else
  echo "  ✓ Airflow Fernet Key ist gültig"
fi

echo ""
echo "Prüfe Keycloak Client Secrets..."

is_valid_secret() {
  local s="$1"
  [ -n "$s" ] && ! [[ "$s" =~ ^(CHANGE_ME|TODO|REPLACE|XXX) ]] && [ ${#s} -ge 20 ]
}

gen_secret() {
  openssl rand -base64 48 | tr -d '\n'
}

for VAR in KEYCLOAK_CLIENT_SECRET_TRINO KEYCLOAK_CLIENT_SECRET_MINIO KEYCLOAK_CLIENT_SECRET_AIRFLOW; do
  VAL=$(grep "^${VAR}=" "$ENV_FILE" | cut -d'=' -f2- || true)
  if ! is_valid_secret "$VAL"; then
    NEW_VAL=$(gen_secret)
    if grep -q "^${VAR}=" "$ENV_FILE"; then
      sed_inplace "s|^${VAR}=.*|${VAR}=${NEW_VAL}|" "$ENV_FILE"
    else
      echo "${VAR}=${NEW_VAL}" >> "$ENV_FILE"
    fi
    echo "  ✓ ${VAR}: neuer Secret generiert"
  else
    echo "  ✓ ${VAR}: vorhanden"
  fi
done

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

# --- OpenMetadata: ingestion-bot Token für OpenLineage holen ----------------
echo ""
echo "Warte auf OpenMetadata Health..."
OM_READY=false
for i in $(seq 1 36); do
  if curl -sf "http://localhost:8585/api/v1/system/version" >/dev/null 2>&1; then
    echo "OpenMetadata ist bereit."
    OM_READY=true
    break
  fi
  echo "  Warte... ($((i*5))s)"
  sleep 5
done

if [ "$OM_READY" = true ]; then
  echo "Hole ingestion-bot JWT-Token von OpenMetadata..."

  ADMIN_TOKEN=$(curl -s -X POST "http://localhost:8585/api/v1/users/login" \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@open-metadata.org","password":"admin"}' | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('jwtToken',''))" 2>/dev/null || true)

  if [ -z "$ADMIN_TOKEN" ]; then
    echo "  ⚠️  Login fehlgeschlagen – ingestion-bot Token wird übersprungen."
  else
    BOT_TOKEN=$(curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
      "http://localhost:8585/api/v1/bots/name/ingestion-bot" | \
      python3 -c "
import sys, json
d = json.load(sys.stdin)
token = (d.get('botUser') or {})
# Token kann direkt im botUser oder in authenticationMechanism liegen
for key in ['jwtToken', 'JWTToken']:
    if key in token:
        print(token[key]); sys.exit()
config = (token.get('authenticationMechanism') or {}).get('config') or {}
for key in ['JWTToken', 'jwtToken']:
    if key in config:
        print(config[key]); sys.exit()
" 2>/dev/null || true)

    if [ -z "$BOT_TOKEN" ]; then
      echo "  ⚠️  ingestion-bot Token konnte nicht gelesen werden."
    else
      OLD_TOKEN=$(grep '^OPENMETADATA_INGESTION_BOT_TOKEN=' "$ENV_FILE" | cut -d'=' -f2- | tr -d ' ' || true)
      if [ "$OLD_TOKEN" != "$BOT_TOKEN" ]; then
        if grep -q '^OPENMETADATA_INGESTION_BOT_TOKEN=' "$ENV_FILE"; then
          sed_inplace "s|^OPENMETADATA_INGESTION_BOT_TOKEN=.*|OPENMETADATA_INGESTION_BOT_TOKEN=${BOT_TOKEN}|" "$ENV_FILE"
        else
          echo "OPENMETADATA_INGESTION_BOT_TOKEN=${BOT_TOKEN}" >> "$ENV_FILE"
        fi
        echo "  ✓ ingestion-bot Token in .env aktualisiert – starte Airflow neu..."
        docker compose up -d airflow
      else
        echo "  ✓ ingestion-bot Token unverändert, kein Neustart nötig."
      fi
    fi
  fi
else
  echo "  ⚠️  OpenMetadata nicht erreichbar – ingestion-bot Token wird übersprungen."
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
