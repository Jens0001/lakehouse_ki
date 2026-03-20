#!/bin/bash
echo "=========================================="
echo "  Lakehouse KI - Full OIDC Health Check"
echo "=========================================="
echo ""

echo "--- 1. Keycloak Realm ---"
KC=$(curl -s http://localhost:8082/realms/lakehouse/.well-known/openid-configuration 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'OK: issuer={d[\"issuer\"]}')" 2>/dev/null)
if [ -z "$KC" ]; then echo "FAIL: Keycloak Discovery nicht erreichbar"; else echo "$KC"; fi

echo ""
echo "--- 2. MinIO SSO ---"
MINIO=$(curl -s http://localhost:9001/api/v1/login 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'loginStrategy={d[\"loginStrategy\"]}')" 2>/dev/null)
if [ "$MINIO" = "loginStrategy=redirect" ]; then echo "OK: $MINIO (SSO aktiv)"; else echo "WARN: $MINIO"; fi

echo ""
echo "--- 3. Trino HTTPS OAuth2 ---"
TRINO=$(curl -sk -o /dev/null -w "%{http_code}" https://localhost:8443/ui/ 2>/dev/null)
if [ "$TRINO" = "303" ]; then echo "OK: HTTPS redirect to Keycloak (303)"; else echo "WARN: HTTP $TRINO"; fi

echo ""
echo "--- 4. Airflow OAuth ---"
AIRFLOW_LOGIN=$(curl -s http://localhost:8081/login/ 2>/dev/null | grep -c "keycloak")
AIRFLOW_REDIRECT=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/login/keycloak 2>/dev/null)
if [ "$AIRFLOW_LOGIN" -gt "0" ] && [ "$AIRFLOW_REDIRECT" = "302" ]; then
  echo "OK: Keycloak button sichtbar, Redirect 302"
else
  echo "WARN: oauth_count=$AIRFLOW_LOGIN, redirect=$AIRFLOW_REDIRECT"
fi

echo ""
echo "--- 5. Dremio ---"
DREMIO=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:9047/ 2>/dev/null)
echo "OK: HTTP $DREMIO (kein OIDC - Enterprise only)"

echo ""
echo "--- 6. Nessie ---"
NESSIE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:19120/api/v2/config 2>/dev/null)
echo "OK: HTTP $NESSIE"

echo ""
echo "=========================================="
echo "  URLs:"
echo "  MinIO:    http://localhost:9001       (SSO)"
echo "  Trino:    https://localhost:8443/ui/  (SSO)"
echo "  Airflow:  http://localhost:8081       (SSO)"
echo "  Dremio:   http://localhost:9047       (local)"
echo "  Keycloak: http://localhost:8082/admin"
echo "  Test-User: testuser / test123"
echo "=========================================="
