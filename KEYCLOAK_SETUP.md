# Keycloak Authentication Integration Guide

Dies ist ein umfassendes Setup für Keycloak-basierte Authentifizierung und Single Sign-On (SSO) für alle Container im Lakehouse KI-Stack.

## 🚀 Quick Start

### 1. Umgebungsvariablen anpassen

Die `.env`-Datei wurde automatisch mit Standard-Werten erstellt. Überprüfen Sie und passen Sie diese Werte an:

```bash
# .env File:
# Keycloak Admin Credentials
KEYCLOAK_ADMIN_USER=admin
KEYCLOAK_ADMIN_PASSWORD=admin123  # Ändern Sie dies in Production!

# Keycloak Realm URL
KEYCLOAK_URL=http://localhost:8082
KEYCLOAK_REALM=lakehouse

# Service Credentials
KEYCLOAK_CLIENT_ID_AIRFLOW=airflow
KEYCLOAK_CLIENT_SECRET_AIRFLOW=airflow-secret-key-12345  # Ändern Sie dies!
# (und ähnlich für MINIO, TRINO, DREMIO)
```

### 2. Container starten

```bash
docker-compose up -d
```

Das wird folgende Services starten:
- **MinIO** (Port 9000, 9001)
- **Nessie** (Port 19120)
- **Trino** (Port 8080)
- **Dremio** (Port 9047)
- **Airflow** (Port 8081)
- **Postgres** (für Airflow, Nessie, und Keycloak)
- **Keycloak** (Port 8082) ← Authentifizierung

### 3. Keycloak Realm und OIDC Clients setup

Nach dem Start müssen die Keycloak Realm und OIDC Clients manuell oder automatisch (via Setup-Script) erstellt werden.

```bash
# Automatisches Setup (aus der lokalen Maschine):
bash init-scripts/setup-keycloak.sh
```

Oder manuell über die Keycloak Admin Console:

1. Öffnen Sie http://localhost:8082
2. Melden Sie sich mit `admin` / `admin123` an
3. Gehen Sie zu der Admin-Konsole
4. Erstellen Sie einen neuen Realm: `lakehouse`
5. Für jeden Service einen OIDC-Client erstellen (MinIO, Trino, Dremio, Airflow)

## 📋 Konfigurierte OIDC-Clients

### MinIO (Port 9001)
- **Client ID**: `minio`
- **Client Secret**: `minio-secret-key-12345`
- **Redirect URI**: `http://localhost:9001/oauth_callback`
- **Zugriff**: http://localhost:9001

### Airflow (Port 8081)
- **Client ID**: `airflow`
- **Client Secret**: `airflow-secret-key-12345`
- **Redirect URI**: `http://localhost:8081/oauth-authorized/keycloak`
- **Zugriff**: http://localhost:8081
- **Konfiguration**: `airflow/webserver_config.py`

### Trino (Port 8080)
- **Client ID**: `trino`
- **Client Secret**: `trino-secret-key-12345`
- **Redirect URI**: `http://localhost:8080/oauth2/callback`
- **Zugriff**: http://localhost:8080
- **Konfiguration**: `trino/config.properties`

### Dremio (Port 9047)
- **Client ID**: `dremio`
- **Client Secret**: `dremio-secret-key-12345`
- **Redirect URI**: `http://localhost:9047/dremio/oauth_callback`
- **Zugriff**: http://localhost:9047
- **Konfiguration**: `dremio-etc/dremio.conf`

## 🔧 Service URLs

Die Services können nach erfolgreichem Setup unter folgenden URLs erreicht werden:

| Service | URL | Port | Auth |
|---------|-----|------|------|
| Keycloak Admin | http://localhost:8082 | 8082 | admin/admin123 |
| MinIO Console | http://localhost:9001 | 9001 | OAuth2 (Keycloak) |
| Airflow | http://localhost:8081 | 8081 | OAuth2 (Keycloak) |
| Trino UI | http://localhost:8080/ui | 8080 | OAuth2 (Keycloak) |
| Dremio | http://localhost:9047 | 9047 | OAuth2 (Keycloak) |
| Nessie API | http://localhost:19120 | 19120 | - |

## 🛠️ OIDC-Konfigurationsdateien

Die folgenden Dateien wurden für die automatische OIDC-Integration erstellt:

1. **`.env`** - Umgebungsvariablen für alle Services
2. **`airflow/webserver_config.py`** - Airflow OIDC-Konfiguration
3. **`trino/config.properties`** - Trino OAuth2-Konfiguration
4. **`dremio-etc/dremio.conf`** - Dremio OIDC-Konfiguration
5. **`init-scripts/postgres-init.sql`** - PostgreSQL DB-Setup für Keycloak
6. **`init-scripts/setup-keycloak.sh`** - Automatisches Setup-Script

## 🔐 Sicherheitshinweise für Production

Die folgenden Punkte sollten vor der Produktionsreife berücksichtigt werden:

1. **Ändern Sie alle Passwörter**:
   - Keycloak Admin: `admin123` → starkes Passwort
   - Alle Client Secrets: `xxx-secret-key-12345` → starke Secrets (min. 32 Zeichen)
   - Alle Datenbank-Passwörter

2. **HTTPS aktivieren**:
   - Trino: `http-server.https.enabled=true`
   - Dremio: `https.enabled: true`
   - Keycloak: Mit SSL-Zertifikat konfigurieren

3. **Keycloak Production Mode**:
   - Ersetzen Sie `start-dev` durch `start` in docker-compose.yml
   - Verwenden Sie ein H2/PostgreSQL (externe Instanz) für die Datenbankpersistenz

4. **Firewall & Netzwerk**:
   - Begrenzen Sie Zugriff auf Port 8082 (Keycloak Admin)
   - Verwenden Sie nur interne Netzwerke für Service-Kommunikation

5. **Token Lifespan**:
   - Überprüfen Sie und passen Sie Token-Gültigkeitsdauer an

6. **Backup und Recovery**:
   - Regelmäßige Backups der PostgreSQL-Datenbank
   - Sichern Sie Keycloak-Realm-Konfiguration

## 📝 Beispiel-Workflow

### User-Login über Keycloak

1. Benutzer besucht http://localhost:8081 (Airflow)
2. System leitet zu Keycloak weiter: http://localhost:8082/realms/lakehouse/protocol/openid-connect/auth
3. Benutzer meldet sich an
4. Keycloak leitet zurück zu Airflow mit Authorization-Code
5. Airflow tauscht Code gegen Access Token aus
6. Benutzer ist angemeldet

### User-Management

Benutzer können in der Keycloak Admin-Konsole verwaltet werden:

1. http://localhost:8082 → Admin Console
2. Wählen Sie Realm `lakehouse`
3. Gehen Sie zu Users
4. Erstellen Sie neue Benutzer oder verwalten Sie bestehende
5. Rollen und Gruppen zuordnen

## 🐛 Troubleshooting

### Keycloak startet nicht
- Prüfen Sie PostgreSQL-Logs: `docker logs lakehouse_postgres`
- Überprüfen Sie die Postgres-Init-SQL: `init-scripts/postgres-init.sql`

### OAuth2 Callbacks schlagen fehl
- Stellen Sie sicher, dass Redirect URIs in Keycloak korrekt konfiguriert sind
- Prüfen Sie Netzwerk-Konnektivität zwischen Containern
- Überprüfen Sie Docker Network: `docker network inspect lakehouse_network`

### Clients können sich nicht authentifizieren
- Prüfen Sie Client IDs und Secrets in `.env`
- Stellen Sie sicher, dass Clients in Keycloak-Realm existieren
- Prüfen Sie Firewall/Port-Konfiguration

## 📚 Weitere Ressourcen

- [Keycloak Dokumentation](https://www.keycloak.org/documentation)
- [Redis-Kurve für Dremio OIDC](https://dremio.readme.io/docs/authentication)
- [Trino OAuth2 Documentation](https://trino.io/docs/current/security/oauth2.html)
- [Airflow Flask-AppBuilder Security](https://airflow.apache.org/docs/apache-airflow/stable/security/webserver.html)

---

**Erstellt**: März 2026
**Version**: 1.0
