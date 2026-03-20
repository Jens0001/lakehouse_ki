#!/bin/bash
# Keycloak realm and client setup

# Wait for Keycloak to be ready
echo "Waiting for Keycloak to be ready..."
sleep 10

# Get the admin token
TOKEN=$(curl -s -X POST http://keycloak:8080/realms/master/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=admin-cli" \
  -d "username=admin" \
  -d "password=admin123" \
  -d "grant_type=password" | jq -r '.access_token')

echo "Keycloak admin token obtained: $TOKEN"

# Create the lakehouse realm
echo "Creating lakehouse realm..."
curl -s -X POST http://keycloak:8080/admin/realms \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "realm": "lakehouse",
    "enabled": true,
    "displayName": "Lakehouse",
    "accessTokenLifespan": 3600,
    "refreshTokenLifespan": 86400
  }' || echo "Realm might already exist"

# Create MinIO client
echo "Creating MinIO OIDC client..."
curl -s -X POST http://keycloak:8080/admin/realms/lakehouse/clients \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "minio",
    "name": "MinIO",
    "enabled": true,
    "publicClient": false,
    "clientAuthenticatorType": "client-secret",
    "secret": "minio-secret-key-12345",
    "redirectUris": ["http://localhost:9001/oauth_callback"],
    "webOrigins": ["http://localhost:9001"],
    "standardFlowEnabled": true,
    "implicitFlowEnabled": false,
    "directAccessGrantsEnabled": false
  }' || echo "MinIO client might already exist"

# Create Airflow client
echo "Creating Airflow OIDC client..."
curl -s -X POST http://keycloak:8080/admin/realms/lakehouse/clients \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "airflow",
    "name": "Airflow",
    "enabled": true,
    "publicClient": false,
    "clientAuthenticatorType": "client-secret",
    "secret": "airflow-secret-key-12345",
    "redirectUris": ["http://localhost:8081/oauth-authorized/keycloak"],
    "webOrigins": ["http://localhost:8081"],
    "standardFlowEnabled": true,
    "implicitFlowEnabled": false,
    "directAccessGrantsEnabled": false
  }' || echo "Airflow client might already exist"

# Create Trino client
echo "Creating Trino OIDC client..."
curl -s -X POST http://keycloak:8080/admin/realms/lakehouse/clients \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "trino",
    "name": "Trino",
    "enabled": true,
    "publicClient": false,
    "clientAuthenticatorType": "client-secret",
    "secret": "trino-secret-key-12345",
    "redirectUris": ["http://localhost:8080/oauth2/callback"],
    "webOrigins": ["http://localhost:8080"],
    "standardFlowEnabled": true,
    "implicitFlowEnabled": false,
    "directAccessGrantsEnabled": false
  }' || echo "Trino client might already exist"

# Create Dremio client
echo "Creating Dremio OIDC client..."
curl -s -X POST http://keycloak:8080/admin/realms/lakehouse/clients \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "dremio",
    "name": "Dremio",
    "enabled": true,
    "publicClient": false,
    "clientAuthenticatorType": "client-secret",
    "secret": "dremio-secret-key-12345",
    "redirectUris": ["http://localhost:9047/oauth_callback"],
    "webOrigins": ["http://localhost:9047"],
    "standardFlowEnabled": true,
    "implicitFlowEnabled": false,
    "directAccessGrantsEnabled": false
  }' || echo "Dremio client might already exist"

echo "Keycloak realm and clients setup completed!"
