# 💾 Memory - Lakehouse KI

Notizen, Erkenntnisse und wichtige Informationen, die während der Arbeit am Lakehouse KI Projekt gesammelt wurden.

---

## 🏗️ Architektur-Entscheidungen (ADRs)

### ADR-001: OIDC/OAuth2 statt API Keys

**Entscheidung**: Keycloak mit OIDC/OAuth2 für alle Services

**Gründe**:
- ✅ Zentrale Benutzerverwaltung
- ✅ Single Sign-On (SSO) möglich
- ✅ Modern & sicher (Token-basiert)
- ✅ Industry Standard
- ✅ Einfaches User Management

**Alternative verworfen**: API Keys → zu komplex für mehrere Benutzer

**Implikationen**:
- Jeder Service braucht OIDC-Integration
- Zusätzliche PostgreSQL DB für Keycloak
- Redirect URIs müssen für Web-Apps konfiguriert werden

**Status**: ✅ Implementiert (Basis)

---

### ADR-002: Docker Compose für Orchestration

**Entscheidung**: Docker Compose statt Kubernetes

**Gründe**:
- ✅ Simplifiziert Development
- ✅ Vollständige Infrastruktur lokal
- ✅ Schnellerer Startup
- ✅ Debugging einfacher

**Alternative verworfen**: Kubernetes → zu komplex für MVP

**Migration Path**: Kubernetes (Helm Charts) geplant für v0.3.0

**Status**: ✅ Implementiert

---

### ADR-003: PostgreSQL als primäre Datenbank

**Entscheidung**: PostgreSQL für Airflow, Keycloak, Nessie-Support

**Gründe**:
- ✅ ACID-Garantien
- ✅ Kostenlos & Open-Source
- ✅ Hohe Zuverlässigkeit
- ✅ Einfache Backups

**Alternative verworfen**: MySQL/MariaDB → bestehende PG-Daten

**Status**: ✅ Implementiert

---

### ADR-005: Data Vault 2.0 + Star Schema (Hybrid-Ansatz)

**Entscheidung**: Raw → Staging (ephemeral) → Data Vault 2.0 → Business Vault (Star Schema) → Marts

**Gründe**:
- ✅ DV2.0 für Auditierbarkeit und Historisierung der Rohdaten
- ✅ Star Schema (Dims + Facts) für einfache Dashboard-Abfragen
- ✅ Keine Redundanz: Satellites speichern Details, Facts aggregieren für Analyse
- ✅ Erweiterbar: neue Datenquellen erweitern Hubs/Satellites, ohne Facts anzufassen

**Schichten**:
1. `raw` – 1:1 aus Quelle (Parquet, Iceberg)
2. `staging` – ephemeral, Hash-Keys + Typen
3. `data_vault` – Hubs, Links, Satellites
4. `business_vault` – Dims + Facts (Star Schema)
5. `marts` – aggregierte, fertige Analysemodelle

**Status**: ✅ Für Open-Meteo vollständig implementiert

---

### ADR-006: dim_date als TABLE (täglicher Full Refresh)

**Entscheidung**: `dim_date` wird als `table` materialisiert (business_vault-Default), kein explizites Override nötig.

**Gründe**:
- ✅ Dremio OSS kann Iceberg Views (erstellt via Trino) **nicht lesen** → View-Ansatz schließt Dremio aus
- ✅ Täglicher Full Refresh um 01:00 Uhr (vor Geschäftsbeginn) hält relative Felder aktuell
- ✅ Staleness-Fenster 00:00–01:00 Uhr (max. 1h) ist akzeptabel
- ✅ Konsistente Definition für alle Konsumenten (Trino, Dremio, direkte SQL-Abfragen)
- ❌ Verworfen: `view` – würde Dremio OSS von der Nutzung ausschließen

**Umsetzung**: `config(materialized='table')` in `dim_date.sql`, täglicher Full Refresh via Airflow DAG

**Status**: ✅ Implementiert

---

### ADR-004: MinIO statt AWS S3

**Entscheidung**: MinIO (S3-kompatibel) für lokale Entwicklung

**Gründe**:
- ✅ Lokal deploybar
- ✅ AWS S3 kompatible API
- ✅ Kostenlos
- ✅ Keine AWS Account nötig

**Produktions-Upgrade**: Kann zu echter AWS S3 gewechselt werden

**Status**: ✅ Implementiert

---

## 🔧 Technische Erkenntnisse

### Keycloak OIDC Redirect URIs

**Issue**: OAuth2 Callbacks schlagen fehl, wenn Redirect URIs nicht genau stimmen

**Lernpunkt**:
```
Keycloak Client Config:
- Exact URI Matching aktivieren
- Protokoll: http (dev) / https (prod)
- Host: localhost (dev) / domain.com (prod)
- Port exakt wie in URL
- Path: /oauth_callback oder /oauth-authorized/keycloak

Beispiel (MinIO):
  Falsch: http://localhost:9001/oauth
  Richtig: http://localhost:9001/oauth_callback
```

**Workaround**: Bei URI-Mismatch: Keycloak Admin → Clients → Redirect URIs genau prüfen

---

### Docker Network Kommunikation

**Issue**: Container können sich nicht untereinander erreichen

**Lernpunkt**:
```
- Container-Namen sind DNS-Namen im Docker Network
- airflow kann postgres erreichen via: postgres:5432
- NOT: localhost:5432 (würde auf der selben Maschine sein)
- Docker inspect zeigen die IP-Adressen
```

**Debugging**:
```bash
docker-compose exec airflow ping postgres
docker network inspect lakehouse_network
```

---

### Umgebungsvariablen in Docker

**Issue**: Umgebungsvariablen von .env werden nicht in alle Services gepropagiert

**Lernpunkt**:
```
- .env wird von docker-compose automatisch geladen
- Aber nicht in entrypoint scripts!
- Explizit in docker-compose.yml angeben:
  environment:
    - VAR_NAME=${VAR_NAME}
```

**Anwendungsbeispiel**:
```yaml
environment:
  - KEYCLOAK_URL=${KEYCLOAK_URL}
  - KEYCLOAK_REALM=${KEYCLOAK_REALM}
```

---

### Health Checks bei Docker-Compose

**Wichtig**: Health Checks für Services definieren

**Lernpunkt**:
```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
  interval: 10s
  timeout: 5s
  retries: 5
```

**Vorteil**: `depends_on` kann auf `service_healthy` warten statt nur auf Container-Start

---

## 📚 Service-spezifische Notizen

### MinIO

- **Ports**: 9000 (S3 API), 9001 (Web Console)
- **Credentials**: minioadmin / minioadmin123
- **OAuth2**: Redirect muss exakt: `http://localhost:9001/oauth_callback`
- **Buckets**: Manuell anlegen oder via `mc` CLI
- **S3 CLI**: `aws s3 --endpoint-url http://localhost:9000 ls`

---

### Nessie Catalog

- **API Port**: 19120
- **Format**: REST API (keine WebUI!)
- **Docker Image**: `projectnessie/nessie:latest`
- **Speicher**: RocksDB (lokal) oder PostgreSQL (extern)
- **Hauptzweck**: Iceberg Table Versioning & Branching

---

### Trino

- **Port**: 8080
- **WebUI**: http://localhost:8080/ui
- **OIDC Config**: In `trino/config.properties`
- **Catalogs**: In `trino/catalog/*.properties`
- **Performance**: Distributed Query Engine → mehrere Worker nötig für Prod

---

### Dremio

- **Port**: 9047
- **Config**: `dremio-etc/dremio.conf` (YAML)
- **OIDC**: ❌ **NICHT MÖGLICH** – nur Dremio Enterprise/Cloud hat OIDC-Support
- **Data Sources**: Web-UI für Konfiguration
- **Features**: Self-Service Analytics, Semantic Layer, Arrow Flight

---

### Airflow

- **Port**: 8081 (WebUI)
- **Config**: `airflow/webserver_config.py` für OIDC (FAB OAuth)
- **Custom Image**: `build: ./airflow` – `authlib` Package erforderlich
- **DAGs**: Im `airflow/dags/` Verzeichnis
- **Initdb**: `airflow db init` (beim ersten Start, in Command integriert)
- **OIDC**: Flask-AppBuilder OAuth mit `AUTH_TYPE = AUTH_OAUTH`

---

### Keycloak

- **Port**: 8082 intern UND extern (`KC_HTTP_PORT=8082`, Mapping `8082:8082`)
- **Admin URL**: http://localhost:8082/admin
- **Mode**: `start-dev` (Development)
- **Mode**: `start` (Production)
- **Realm**: Logisch separierte Tenant/Umgebung
- **Clients**: Applikationen, die SSO nutzen (minio, trino, airflow)
- **Users**: In Realm definiert (testuser/test123)
- **Hostname-Config**: `KC_HOSTNAME=keycloak`, `KC_HOSTNAME_PORT=8082`, `KC_HOSTNAME_STRICT=false`
- **Persistenz**: Volume `keycloak_data:/opt/keycloak/data` (ohne geht Realm bei Restart verloren!)
- **OIDC Discovery**: `http://keycloak:8082/realms/lakehouse/.well-known/openid-configuration`
- **Healthcheck**: Port **9000** (Management-Port in Keycloak 26.x), NICHT Port 8082!
- **Auto-Import**: `--import-realm` + Volume `./init-scripts/keycloak:/opt/keycloak/data/import`
- **Realm-Export**: `init-scripts/keycloak/lakehouse-realm.json` (3 Clients + testuser)

---

## 🔑 OIDC-Integration Erkenntnisse (Session 3)

### MinIO OIDC – Gelöste Probleme

1. **MinIO `latest` SSO-Bug**: Image `minio/minio:latest` (Stand Sept. 2025, RELEASE.2025-09-07) zeigt keinen SSO-Button in der Console – `loginStrategy` bleibt auf `form`. Fix: Image pinnen auf `RELEASE.2024-11-07T00-52-20Z`.

2. **MinIO Stored Config**: MinIO speichert Konfiguration in `./storage/data/.minio.sys/config/config.json`. Diese überschreibt Env Vars! Bei Problemen: `.minio.sys/` löschen und Container neu starten.

3. **`MINIO_IDENTITY_OPENID_ENABLE`**: Default ist `off`! Muss explizit auf `on` gesetzt werden.

4. **DNS-Auflösung**: MinIO braucht `depends_on: keycloak: condition: service_started`, sonst schlägt OIDC-Discovery beim Start fehl ("no such host").

### Keycloak Hostname / Port – Gelöste Probleme

5. **Gleicher Port intern + extern**: Keycloak muss intern und extern denselben Port verwenden (8082:8082 mit `KC_HTTP_PORT=8082`), damit Browser-Redirects funktionieren.

6. **`KC_HOSTNAME_STRICT=false`**: Erlaubt Keycloak, dynamische Hostnames basierend auf dem Request-Origin zurückzugeben. Ohne läuft nur der konfigurierte Hostname.

7. **`/etc/hosts` Eintrag**: `127.0.0.1 keycloak` auf dem Host-Rechner erforderlich, weil Keycloak `http://keycloak:8082/...` in OIDC-Redirects schreibt und der Browser diesen Hostname auflösen muss.

### Generelle OIDC-Lessons

- Docker OIDC erfordert, dass interne (Container-zu-Container) URLs und externe (Browser) URLs konsistent sind
- `KC_HOSTNAME_STRICT=false` + `/etc/hosts` ist die sauberste Lösung für lokale Dev-Setups
- Bei OIDC-Problemen: Immer zuerst den Discovery-Endpoint prüfen (`/.well-known/openid-configuration`)

### Trino OIDC – Gelöste Probleme

8. **HTTPS Pflicht für Web UI OAuth2**: Trino Web UI ist bei OAuth2 über HTTP deaktiviert (`/ui/disabled.html`). Lösung: Self-Signed JKS Keystore erstellen, HTTPS auf Port 8443.

9. **Keystore-Generierung ohne Java auf macOS**: `docker run --rm eclipse-temurin:21-jdk keytool -genkeypair` – Keystore im Container generieren, per Volume nach Host kopieren.

10. **`internal-communication.shared-secret`**: Pflicht wenn `authentication.type` gesetzt ist. Generieren mit `openssl rand -base64 32`.

11. **`${ENV:VAR}` Syntax**: Trino (ab v389+) unterstützt diese Syntax für Secrets in Properties-Files.

12. **Unterstrich in DNS SAN**: `lakehouse_trino` hat invaliden Unterstrich für DNS-Namen → muss aus SAN entfernt werden.

### Airflow OIDC – Gelöste Probleme

13. **authlib fehlt im Standard-Image**: Airflow Standard-Image hat kein `authlib` → Custom Dockerfile mit `pip install authlib` nötig.

14. **FAB OAuth Config-Format**: Flask-AppBuilder erwartet direkte Variable-Assignments (`AUTH_TYPE = AUTH_OAUTH`), KEINE funktions-basierte Config.

15. **Keycloak-URLs im Container-Netzwerk**: In `webserver_config.py` müssen URLs über Docker-DNS gehen (`http://keycloak:8082`), nicht `localhost` – der Airflow-Container kann `localhost:8082` nicht erreichen.

### Dremio OIDC

16. **Enterprise-only**: Dremio OSS hat keinen OIDC/OAuth2-Support. Nur Dremio Enterprise und Dremio Cloud unterstützen SSO/OIDC.

### Keycloak 26.x Healthcheck

17. **Management-Port 9000**: Keycloak 26.x nutzt Port 9000 für Management-Endpoints (inkl. Health). HTTP-Port 8082 hat KEINEN Health-Endpoint. Aktivierung: `--health-enabled=true`.

18. **Kein curl/wget im Container**: Keycloak-Container hat weder curl noch wget. Lösung: Bash TCP-Check `exec 3<>/dev/tcp/localhost/9000`.

### Portabilität

19. **Realm Auto-Import**: `--import-realm` importiert JSON-Dateien aus `/opt/keycloak/data/import/` nur bei frischer DB.

20. **`docker compose restart` vs `up -d`**: `restart` erstellt Container nicht neu → Port-Änderungen, Image-Änderungen etc. brauchen `up -d`.

---

## ⚙️ Konfiguration & Umgebung

### .env Struktur

```
# Produktionsreif müssen alle diese Werte angepasst werden:
1. KEYCLOAK_ADMIN_PASSWORD → Starkes Passwort
2. KEYCLOAK_CLIENT_SECRET_* → 32+ Zeichen Random
3. POSTGRES_PASSWORD → Starkes Passwort
4. KEYCLOAK_DB_PASSWORD → Starkes Passwort
```

### Docker Volumes

```
postgres_data: Persistiert PostgreSQL-Datenbank
keycloak_data: Persistiert Keycloak Realm-Daten (WICHTIG!)
./storage/data: MinIO Buckets (inkl. .minio.sys Config)
./airflow/: DAGs, Logs, Plugins
./dremio/data: Dremio Konfiguration
```

**Sichererung**: Diese sollten regelmäßig gebackuped werden!

---

## 🔐 Security Erkenntnisse

### Passwort-Management

**Best Practice für Development**:
```env
# Kurz, aber dokumentiert
KEYCLOAK_ADMIN_PASSWORD=admin123
```

**Best Practice für Production**:
```bash
# Random 32+ Zeichen
openssl rand -base64 32
# Ergebnis in .env.production speichern
# In Secret Manager (Vault, AWS Secrets) speichern
```

---

### API-Security

**Faustregel**: Token-basiert statt hardcoded API Keys

- Tokens haben Ablaufzeit (TTL)
- Tokens können revoziert werden
- Token refreshing möglich
- Audit Logging einfacher

---

### Network Isolation

**Development** (aktuell):
- Alle Container im gleichen Docker Network
- Ports zu lokal gebunden
- OK für Entwickler-Laptop

**Production**:
- Private VPC/Network
- Firewall Rules pro Service
- Keycloak Admin nur über VPN
- Database (PostgreSQL) nicht exposed

---

## 🧪 Testing & Validierung

### OIDC/OAuth2 Flow Validierung

Checklist für manuelles Testen:

```
1. Browser: http://localhost:8081 (Airflow)
2. Sollte zu Keycloak weitergeleitet werden
3. Login-Form anzeigen
4. Mit Test-User anmelden
5. Zurück zu Airflow mit Access Token
6. Airflow sollte Benutzer anzeigen

Falls failed:
- Keycloak Logs: docker logs lakehouse_keycloak
- Browser Dev Tools: Console & Network Tab
- Redirect URI überprüfen
```

---

### Health & Connectivity Check

```bash
# Alle Container running?
docker-compose ps

# Netzwerk funktioniert?
docker-compose exec trino nslookup keycloak

# Services antwortet?
curl http://localhost:8082/admin  # Keycloak
curl http://localhost:9001        # MinIO
curl http://localhost:8081        # Airflow
```

---

## 📖 Dokumente & Referenzen

### Kurz-Befehle

```bash
# Stack starten
docker-compose up -d

# Logs all
docker-compose logs -f

# Logs spezifisch
docker-compose logs -f keycloak

# Shell in Container
docker-compose exec airflow bash

# Postgres SQL
docker-compose exec postgres psql -U airflow -d airflow

# Stack stoppen
docker-compose down

# Stack + Volumes löschen
docker-compose down -v
```

---

### Wichtige URLs (Bookmark!)

| Service | URL | Login |
|---------|-----|-------|
| Keycloak Admin | http://localhost:8082/admin | admin / $PW |
| MinIO | http://localhost:9001 | OAuth |
| Airflow | http://localhost:8081 | OAuth |
| Trino | http://localhost:8080/ui | OAuth |
| Dremio | http://localhost:9047 | OAuth |

---

## 🚀 Optimization Hints

### Für schnelleres Development

1. **PreBuilt Images verwenden** statt `build: ./airflow`
2. **Volumes für häufig ändernde Dateien** (DAGs, Config)
3. **Health Checks verkürzen** während Entwicklung
4. **Multi-stage Builds** für Airflow Dockerfile

### Für bessere Performance (Production)

1. **Resource Limits** setzen in docker-compose
2. **Read Replicas** für PostgreSQL
3. **Caching** für Query Results (Trino, Dremio)
4. **Horizontal Scaling** mit mehreren Workers

---

## 📋 Checklisten

### Pre-Production Checklist

- [ ] Alle Passwörter geändert
- [ ] HTTPS/SSL aktiviert
- [ ] Firewall konfiguriert
- [ ] Backups automatisiert
- [ ] Monitoring aktiv
- [ ] Alerting konfiguriert
- [ ] Security Audit gemacht
- [ ] Load Testing gemacht

---

## 🎯 Nächste Fokusthemen

1. **OIDC Integration Validierung** → Integration Tests schreiben
2. **Performance Baseline** → Benchmark-Skripte erstellen
3. **Disaster Recovery** → Backup/Restore testen
4. **Monitoring Setup** → Prometheus + Grafana
5. **Documentation** → Update nach Learnings

---

## 🌦️ Data Pipeline: Open-Meteo

### API-Details

- **Archive API**: `https://archive-api.open-meteo.com/v1/archive` – kein API-Key, historisch bis ~2015
- **Forecast API**: `https://api.open-meteo.com/v1/forecast` – für aktuelle Tage
- **Wichtig**: Aktueller Tag ist über die Archive API noch **nicht** verfügbar → DAG muss Vortag abrufen (Schedule `0 6 * * *`)
- **Zeitzonenproblem**: API gibt Timestamps in der angefragten Timezone zurück. Immer `timezone=Europe%2FBerlin` mitgeben für konsistente Stunden.
- **Partieller Tag**: Bei Backfill: Falls letzter Tag im Batch unvollständig (< 24 Stunden), schlägt `assert_hourly_completeness`-Test fehl → ist erwartet für den jeweils aktuellsten Tag

### Airflow Variables

| Variable | Beispielwert |
|---|---|
| `WEATHER_LATITUDE` | `52.59` |
| `WEATHER_LONGITUDE` | `13.35` |
| `WEATHER_LOCATION_KEY` | `berlin-reinickendorf` |
| `MINIO_ENDPOINT` | `http://minio:9000` |
| `MINIO_ACCESS_KEY` | `minioadmin` |
| `MINIO_SECRET_KEY` | `minioadmin123` |

### Trino Airflow Connection

- Connection ID: `trino_default`
- Type: `Trino`, Host: `trino`, Port: `8080`, Schema: `iceberg`
- HTTP (nicht HTTPS) – intern im Docker-Netzwerk

---

## 🧱 dbt Konventionen (dieses Projekt)

### schema.yml Struktur

- Jeder Ordner unter `models/` hat eine eigene `schema.yml`
- Jede Spalte hat eine `description`
- Standard-Tests direkt unter der Spalte: `not_null`, `unique`, `relationships`, `accepted_values`
- FK-Tests via `relationships: to: ref('model'), field: col`

### Test-Ordner Konvention

- Custom Tests in `tests/` spiegeln die Ordnerstruktur von `models/`
- Schema: `tests/<schicht>/<modellname>/assert_<was_wird_geprüft>.sql`
- Beispiel: `tests/business_vault/fact_weather_hourly/assert_hourly_completeness.sql`
- Ausführung: `dbt test --select <modellname>` führt alle Tests für dieses Modell aus

### Hash-Keys in dbt

- Surrogatschlüssel via `{{ dbt_utils.generate_surrogate_key(['col1', 'col2']) }}`
- Hub-Key: nur Business Key → `generate_surrogate_key(['location_key'])`
- Hashdiff (Satellit): alle Attributspalten → `generate_surrogate_key(['col1', 'col2', ...])`
- `dbt_utils` muss in `packages.yml` eingetragen und mit `dbt deps` installiert sein

### Incremental-Pattern

- Hubs: `where location_hk not in (select location_hk from {{ this }})` – nie doppelt
- Satellites: `where hashdiff not in (select hashdiff from {{ this }})` – nur neue Versionen
- Facts: `where load_date > (select max(load_date) from {{ this }})` – neue Loads

### Ephemeral Staging

- Keine physische Tabelle → direkt als CTE eingefügt
- Ideal für Transformationen (Hash-Keys, Typ-Casts), die überall gebraucht werden
- Kein separater dbt-Run nötig

---

## 🔧 Trino + Iceberg + Nessie: Technische Details

### Korrekte Catalog-Properties (Trino 479)

```properties
# Kein hive-metastore – native s3
fs.native-s3.enabled=true
s3.endpoint=http://minio:9000
s3.aws-access-key=minioadmin
s3.aws-secret-key=minioadmin123
s3.path-style-access=true

# Nessie als Iceberg Catalog
iceberg.catalog.type=rest
iceberg.rest-catalog.uri=http://nessie:19120/iceberg
```

- **`fs.native-s3.enabled=true`** erfordert `s3.*` Properties, NICHT `hive.s3.*`
- **Namespace-Anlage**: Via Trino REST API (nicht SQL!): `POST /v1/catalog/namespaces`
- **Parquet-Default**: Iceberg-Tabellen in Trino nutzen Parquet by default – explizit via `with (format = 'PARQUET')` bei `CREATE TABLE`

### Namespace-Init ohne Python

- Image `curlimages/curl` hat kein python3 → JSON-Parsing mit `grep -o '"nextUri":"[^"]*"' | sed`
- `trino-init` Container exitiert mit Code 0 nach erfolgreichem Namespace-Setup

### Iceberg Views

- Trino + Nessie: Iceberg Views funktionieren (`CREATE VIEW`)
- Dremio OSS: **kann keine Iceberg Views lesen** (nur Dremio Enterprise/Cloud)
- → `dim_date` als Trino-View ist für Dashboards per Trino nutzbar, nicht per Dremio

---

## 🐛 Session 2 - Debugging Erkenntnisse (18. März 2026, ab 21:30)

### Issue #1: Docker Registry Connection auf macOS

**Fehler**:
```
failed to resolve source metadata for docker.io/apache/airflow:2.8.0: 
failed to do request: dialing registry-1.docker.io:443: 
dial tcp [2600:1f18:2148:bc00...]:443: connect: no route to host
```

**Ursache**: IPv6 DNS-Problem bei macOS Docker Desktop

**Lösungen**:
1. Docker Desktop neustarten → Manchmal hilfreich
2. DNS zu 8.8.8.8 ändern → Funktioniert, aber kompliziert
3. **Pre-Built Images nutzen** ✅ → BEST FÜR DEVELOPMENT

**Learning**: Bei Build-Problemen auf macOS lieber vorgefertigte Images verwenden statt zu bauen.

---

### Issue #2: Trino Volume Mount Konflikt

**Fehler**:
```
OCI runtime create failed: error mounting 
"/host_mnt/Users/jens/git/lakehouse_ki/trino/config.properties" to rootfs at 
"/etc/trino/config.properties": create mountpoint outside of rootfs
```

**Root Cause**:
```yaml
volumes:
  - ./trino/etc:/etc/trino                              # Mounts VERZEICHNIS
  - ./trino/config.properties:/etc/trino/config.properties  # Versucht DATEI darin zu mounten
```

Docker kann nicht gleichzeitig ein Verzeichnis und eine Datei darin mounten!

**Lösung**:
1. Doppeltes Mount entfernen
2. Datei ins Verzeichnis verschieben: `./trino/config.properties` → `./trino/etc/config.properties`
3. Nur noch Volume-Mount vom Verzeichnis:
```yaml
volumes:
  - ./trino/etc:/etc/trino
  - ./trino/catalog:/etc/trino/catalog
```

**Best Practice**: Immer komplette Verzeichnisse mounten, nicht einzelne Dateien!

---

### Issue #3: Dremio YAML vs HOCON Config

**Fehler**:
```
dremio.conf @ line 4: Expecting end of input or a comma, got ':' 
(if you intended ':' to be part of a key or string value, try enclosing the key or value in double quotes)
```

**Root Cause**: Dremio benötigt HOCON-Format, nicht YAML!

**Unterschied**:
```yaml
# FALSCH - YAML Format
services:
  coordinator:
    enabled: true
  
# RICHTIG - HOCON Format
services {
  coordinator.enabled = true
}
```

**Key Differences**:
- YAML: `:` (Colon) für Key-Value
- HOCON: `=` (Equals) für Key-Value
- YAML: Indentation für Nesting
- HOCON: `{}` oder `.` (dot notation) für Nesting

**Learning**: Immer die offizielle Config-Doku konsultieren! Nicht annahmen via Format-Namen machen.

---

### Issue #4: PostgreSQL MySQL-Syntax in Init-Script

**Fehler**:
```
initdb: invalid option -- 'c'
Try "initdb --help" for more information.
```

**Root Cause**: Init-Script hatte MySQL-Syntax statt PostgreSQL-Syntax

```sql
-- FALSCH - MySQL
CREATE DATABASE IF NOT EXISTS keycloak;

-- RICHTIG - PostgreSQL
CREATE DATABASE keycloak;
```

**Unterschiede**:
- MySQL: `IF NOT EXISTS` ist Standard
- PostgreSQL: Neuere Versionen unterstützen es, aber `IF NOT EXISTS` ist nicht zwingend
- PostgreSQL User-Syntax ist anders: `CREATE USER username WITH PASSWORD 'pass'`

**Learning**: SQL-Dialekte sind unterschiedlich! Immer DB-spezifische Doku nutzen.

---

### Issue #5: Trino fehlende JVM Config

**Fehler**:
```
ERROR: JVM config file is missing: could not find file: /etc/trino/jvm.config
```

**Root Cause**: Trino braucht mehrere Config-Dateien:
1. `jvm.config` - JVM Startup-Parameter
2. `log.properties` - Logging
3. `config.properties` - Server-Config
4. `node.properties` - Node-Identifier (optional)

**Gelöst durch**: Alle fehlenden Config-Dateien erstellen mit sinnvollen Defaults

**Learning**: Nicht alle Trino Config-Attribute sind in `config.properties`. JVM/Logging separate!

---

### Pattern: Docker Mount-Protokoll

**Merksatz**: Docker Volume Mounts sind **exklusiv**:
- ✅ `./dir:/container/path` - Ganzes Verzeichnis mounten
- ✅ `./file:/container/file.txt` - Einzelne Datei mounten
- ❌ `./dir:/container/path` + `./dir/file:/container/path/file` - KONFLIKT!

**Workaround**: Wenn beides nötig:
- Option 1: Alles in ein Verzeichnis, dann komplettes Verzeichnis mounten
- Option 2: Datei in Verzeichnis verschieben

---

### Pattern: Config-Format-Sicherheit

Immer beim Erstellen von Config-Dateien beachten:
1. **Format validieren**: Welche Syntax erwartet die Anwendung?
   - YAML: `:` für KV, Indentation für Struktur
   - HOCON: `=` für KV, `{}` oder Dot-Notation für Struktur
   - Properties: `key=value`, keine Nesting
2. **Doku konsultieren**: Offizielle Docs der Anwendung
3. **Syntax-Validation**: Config-File direkt validieren
   ```bash
   # Beispiel für YAML
   python -m yaml /path/to/config.yaml
   ```

---

### Pattern: Service Dependencies in Docker-Compose

**Problem**: Services können schneller starten als ihr State ready ist

**Lösung**: Health Checks + depends_on conditions

```yaml
depends_on:
  postgres:
    condition: service_healthy  # Warte bis gesund
    
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U airflow"]
  interval: 10s
  timeout: 5s
  retries: 5
```

**Wichtig**: `service_healthy` ist zuverlässiger als `service_started`

---

### macOS Docker Desktop Spezifika

1. **IPv6 DNS Issues**: Registry-Connection kann fehlschlagen → Restart oder Pre-Built Images
2. **Volume Mounting**: Funktioniert anders als auf Linux (virtiofs vs native)
3. **Resource Defaults**: Standard-Limits können zu Low sein → Settings überprüfen
4. **Performance**: Nicht alles hochfahren, Resource-Limits beachten

**Workarounds**:
- Pre-Built Images statt Building
- Health Checks Delays erhöhen (etwas langsamer auf macOS)
- Größere Timeouts für Service-Start

---

---

## 🐛 Session 2 - Part 2: Trino & Dremio Final Fixes (18. März 2026, 21:30 - 21:40)

### Issue #6: Trino Deprecated JVM-Optionen

**Fehler**:
```
Unrecognized VM option 'PrintGCDateStamps'
Error: Could not create the Java Virtual Machine.
```

**Root Cause**: `jvm.config` hatte zu viele veraltete GC-Logging-Optionen
- `PrintGCDateStamps` - nicht mehr unterstützt in Java 11+
- `PrintGCApplicationStoppedTime` - veraltet
- `PrintGCDetails` - alt
- `-Xlog:gc:...` - neuerer Syntax wird bevorzugt

**Lesson**: Beim Schreiben von JVM Configs die Java-Version prüfen!

### Issue #7: Trino fehlender `node.environment`

**Fehler**:
```
Invalid configuration property node.environment: must not be null
```

**Root Cause**: Trino braucht `node.environment` - es ist mandatory!

**Lösung**: `node.environment=production` hinzufügen

### Issue #8: Dremio strengere Config-Validation

**Fehler** (mehrere Iterationen):
```
Failure reading configuration file. The following properties were invalid:
  paths.staging, web.port, services.web.enabled
```

**Finale Lösung**: Config-Mount ganz entfernen! ✅

**Learning**: Manchmal ist die beste Konfiguration: **keine Konfiguration**!
   - Dremio hat perfekte Defaults
   - Nur das nötigste (Volume für Persistenz) mounten

---

---

## 🐛 Session 2 - Part 3: PostgreSQL & Airflow Authentication Fixes (18. März 2026, 21:45 - 22:05)

### Issue #9: PostgreSQL Init-Script Syntax Errors

**Fehler**:
```
ERROR:  syntax error at or near "NOT"
LINE 1: CREATE DATABASE IF NOT EXISTS keycloak OWNER keycloak
```

**Root Cause**: PostgreSQL 13 unterstützt nur `CREATE DATABASE IF NOT EXISTS`, nicht `CREATE USER IF NOT EXISTS`

**Lösung**: Simplify Script - nutze dass `airflow` DB/User ohnehin automatisch erstellt werden

**Pattern**: PostgreSQL Init-Scripts laufen nur beim ersten Start → keine Exception Handling nötig

### Issue #10: Airflow Password Authentication Failed

**Fehler**:
```
FATAL:  password authentication failed for user "airflow"
```

**Root Cause**: Connection String in docker-compose.yml hatte Passwort `airflow` statt `airflow123`

**Fix**: 
```yaml
# FALSCH
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow:airflow@postgres:5432/airflow

# RICHTIG
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow:airflow123@postgres:5432/airflow
```

**Learning**: Connection Strings müssen mit `.env` Variablen passen!

### Issue #11: Invalid Fernet Key

**Fehler**:
```
AirflowException: Could not create Fernet object: Fernet key must be 32 url-safe base64-encoded bytes.
```

**Root Cause**: Der Key in `.env` war ein Placeholder-Text, kein gültiger base64 Key

**Lösung**: Generiert mit `openssl rand -base64 32`

**Neuer Key**: `ejjw6bC6g2FrOWQxK9k2KzfgQFVum23EqzsP/PiqyBA=`

### Issue #12: Airflow Missing Startup Command

**Fehler**:
```
airflow command error: the following arguments are required: GROUP_OR_COMMAND
```

**Root Cause**: Docker Image hatte keine `command`, Also fehlte der Startup-Befehl

**Lösung**: Hinzugefügt in docker-compose.yml:
```yaml
command: bash -c "airflow db init && airflow webserver"
```

**Verbesserung**: `depends_on` mit `condition: service_healthy` → wartet auf PostgreSQL

---

## 📋 Session 2 Debugging Summary

**Behobene Issues**: 12 (macOS Docker Registry → Trino Config → Dremio Config → PostgreSQL → Airflow)

**Root Causes gelernt**:
1. Hardcoded Credentials vs Environment-Variablen
2. Config Format (YAML ≠ HOCON ≠ Properties)
3. PostgreSQL vs MySQL SQL-Syntax
4. Base64 Encoding für Secrets
5. Docker depends_on mit health checks
6. Init-Scripts vs Custom Config
7. JVM GC Options evolution

---

**Letzte Aktualisierung**: 18. März 2026, 22:05  
**Sachstandortingredienzen**: 50+ Einträge  
**Repository**: /Users/jens/git/lakehouse_ki
