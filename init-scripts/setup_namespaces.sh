#!/bin/bash
# Nessie/Iceberg Namespace-Initialisierung
# Erstellt die Lakehouse-Schichten als Iceberg Namespaces via Trino.

set -e

TRINO_URL="http://trino:8080"

echo "⏳ Warte auf Trino..."
until curl -sf -o /dev/null "${TRINO_URL}/v1/info"; do
  echo "  Trino noch nicht bereit, warte 3s..."
  sleep 3
done
echo "✅ Trino erreichbar"

# Funktion: SQL via Trino REST API absenden und vollständig pollen
run_sql() {
  local sql="$1"
  echo "  → ${sql}"

  local response next_uri state
  response=$(curl -sf -X POST "${TRINO_URL}/v1/statement" \
    -H "X-Trino-User: admin" \
    -H "X-Trino-Catalog: iceberg" \
    -H "X-Trino-Schema: raw" \
    -H "Content-Type: text/plain" \
    -d "${sql}")

  # nextUri aus JSON mit sed/grep extrahieren
  next_uri=$(echo "${response}" | grep -o '"nextUri":"[^"]*"' | sed 's/"nextUri":"//;s/"//')

  local attempts=0
  while [ -n "${next_uri}" ] && [ "${attempts}" -lt 30 ]; do
    sleep 1
    response=$(curl -sf -H "X-Trino-User: admin" "${next_uri}" 2>/dev/null || echo '{}')
    next_uri=$(echo "${response}" | grep -o '"nextUri":"[^"]*"' | sed 's/"nextUri":"//;s/"//')
    # Fehler prüfen
    if echo "${response}" | grep -q '"FAILED"'; then
      local errmsg
      errmsg=$(echo "${response}" | grep -o '"message":"[^"]*"' | head -1 | sed 's/"message":"//;s/"//')
      echo "  ⚠️  Ignoriert: ${errmsg}"
      return 0
    fi
    attempts=$((attempts + 1))
  done
}

echo "📦 Erstelle Iceberg Namespaces..."
run_sql "CREATE SCHEMA IF NOT EXISTS iceberg.raw"
run_sql "CREATE SCHEMA IF NOT EXISTS iceberg.data_vault"
run_sql "CREATE SCHEMA IF NOT EXISTS iceberg.business_vault"
run_sql "CREATE SCHEMA IF NOT EXISTS iceberg.marts"
echo "✅ Namespaces erstellt"
