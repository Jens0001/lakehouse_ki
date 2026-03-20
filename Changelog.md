# 📝 Changelog - Lakehouse KI

Alle Änderungen und Versionshistorie des Lakehouse KI Projekts.

## [Unreleased]

### Dokumentation bereinigt (20.03.2026)

- **`instructions.md` auf Kernanweisungen reduziert**: Technische Inhalte (Service-Tabelle, OIDC-Details, Portabilität) aus instructions.md entfernt – waren Duplikate von Memory.md / KEYCLOAK_SETUP.md / README.md
- **`ARCHITECTURE.md` Kapitel 0 ergänzt**: Service-Tabelle (Image-Namen, Ports, Zweck) und Startup-Reihenfolge (depends_on) dorthin übertragen

### Dokumentationskorrekturen (20.03.2026)

- **`dim_date` – Materialisierung**: Widerspruch zwischen Code und Dokumentation aufgelöst.
  - Code war bereits korrekt: `config(materialized='table')` in `dim_date.sql`
  - Korrigiert: Kommentare in `dim_date.sql` (Zeile 6 + 32), `schema.yml` Description, `Memory.md` ADR-006
  - Alle Stellen sagen nun einheitlich: TABLE mit täglichem Full Refresh um 01:00 Uhr
  - Grund für TABLE statt VIEW: Dremio OSS kann Iceberg Views nicht lesen

- **Cognos Analytics – Scope**: Klarstellung in `ARCHITECTURE.md` Kapitel 6 ergänzt.
  - Cognos ist nicht Teil dieses Stacks und nicht containerisiert
  - Wird auf der finalen Zielplattform verfügbar sein und sich extern anbinden

### Geplant
- Integration mit dbt (Data Build Tool)
- Monitoring-Stack (Prometheus, Grafana)
- MLOps-Integration (z.B. MLflow)
- Backup & Disaster Recovery Setup
- Production-ready Deployment Guide

---

## [0.1.0-alpha] - 2026-03-18

### 🐛 Debugging & Fixes - Session 2 (2026-03-18, ab 21:30 Uhr)

#### Docker-Compose Optimierungen
- **`version: '3.8'` entfernt**: Deprecated Attribute (warning bei docker-compose)
- **Airflow Build → Pre-Built Image**: `build: ./airflow` → `image: apache/airflow:2.8.0`
  - **Grund**: Apache Registry Connection-Fehler auf macOS Docker Desktop (IPv6 DNS Issue)
  - **Vorteil**: Schnellerer Start, keine Registry-Probleme im Development
- **PostgreSQL InitDB Args entfernt**: `POSTGRES_INITDB_ARGS: -c max_connections=200` gelöscht
  - **Grund**: Invalid Syntax beim PostgreSQL 13 Image
  - **Fehler war**: `initdb: invalid option -- 'c'`
- **Trino Volume-Mount-Konflikt behoben**:
  - **Problem**: Doppeltes Mounting (Verzeichnis + einzelne Datei)
  - **Fehler**: `OCI runtime create failed: mountpoint outside of rootfs`
  - **Lösung**: `config.properties` zu `trino/etc/` verschoben, doppeltes Mount entfernt

#### Trino Konfigurationsdateien (neu erstellt)
- **`trino/etc/jvm.config`**: JVM Startup-Parameter
  - G1GC Garbage Collector
  - Heap: 2GB (für macOS Development)
  - GC-Logging aktiviert
- **`trino/etc/log.properties`**: Logging-Konfiguration
- **`trino/etc/node.properties`**: Node-Identifier
- **`trino/etc/config.properties`** (vereinfacht):
  - Entfernt: OIDC/OAuth2 (komplexe Env-Var-Substitution)
  - Behalten: Coordinator, WebUI, Query-Settings
  - OIDC kann später per Entrypoint-Script konfiguriert werden

#### Dremio Konfiguration
- **`dremio-etc/dremio.conf`**: YAML → HOCON Format konvertiert
  - **Problem**: Config-Parse-Fehler bei YAML-Syntax
  - **Fehler**: `Expecting end of input or a comma, got ':'`
  - **Lösung**: Vollständige HOCON-Migration (= statt :, {} statt Indentation)
  - **Entfernt**: OIDC-Integration (wird später hinzugefügt)

#### PostgreSQL Init-Script
- **`init-scripts/postgres-init.sql`** (Syntax-Fixes):
  - Entfernt: `CREATE DATABASE IF NOT EXISTS` (MySQL-Syntax)
  - Behoben zu: Standard PostgreSQL Syntax
  - Behoben: `CREATE USER IF NOT EXISTS` → `CREATE USER`
  - Kept: Grant- und Schema-Privileges

### ⚠️ Debugging Session Learnings
- **Registry-Probleme auf macOS**: Pre-Built Images sind Best Practice
- **Volume-Mount-Konflikte**: Entweder Verzeichnis ODER einzelne Dateien, nicht beides!
- **Config-Format-Kompatibilität**: YAML ≠ HOCON, SQL-Dialekte unterscheiden sich
- **Service-Dependencies**: Health Checks wichtiger als nur Container-Start
- **Multi-Service-Debugging**: Jeder Service hat unterschiedliche Anforderungen

#### Trino Final Fixes (Session 2 - Part 2)
- **`trino/etc/jvm.config` modernisiert**:
  - Entfernt: Deprecated JVM-Optionen (`PrintGCDateStamps`, `PrintGCApplicationStoppedTime`, etc.)
  - Grund: Java 11+ unterstützt diese Optionen nicht mehr
  - Fehler war: `Unrecognized VM option 'PrintGCDateStamps'`
  - Behalten: G1GC, Heap-Size, Core-Options
- **`trino/etc/config.properties` korrigiert**:
  - Hinzugefügt: `node.environment=production` (erforderlich!)
  - Gefixt: `exchange.http-client.max-connections` → `exchange.http-client.max-connections-per-server`
  - Entfernt: `discovery-server.enabled=true` (wird nicht verwendet)
  - **Result**: Trino startet erfolgreich ✅

#### Dremio Final Fix (Session 2 - Part 2)
- **Config-Approach geändert**:
  - Tried: Complex HOCON-Konfiguration → Config-Validation-Fehler
  - **Solution**: Config-File-Mount entfernt, nutze Dremio-Defaults
  - Entfernt aus docker-compose.yml: `./dremio-etc/dremio.conf:/opt/dremio/conf/dremio.conf`
  - Entfernt aus docker-compose.yml: KEYCLOAK_* Environment-Variablen (nicht nötig ohne Custom-Config)
  - Behalten: Volume für `/opt/dremio/data` (Persistenz)
  - **Result**: Dremio startet erfolgreich mit Defaults ✅
  - **Lesson**: Manchmal sind weniger Config-Files besser!

#### PostgreSQL & Airflow Authentifizierung (Session 2 - Part 3)
- **PostgreSQL Init-Script Fehler behoben**:
  - Problem: `CREATE DATABASE IF NOT EXISTS` nicht unterstützt in PostgreSQL 13
  - Lösung: Simplifies Script ohne `IF NOT EXISTS`
  - Problem: `airflow` Database/User werden automatisch erstellt
  - Lösung: Nur Keycloak User + Database im Script erstellen
- **Airflow DB Connection Fehler**:
  - Problem: Connection String hatte Passwort `airflow` statt `airflow123`
  - Lösung: `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow:airflow123@postgres:5432/airflow`
- **Airflow Fernet Key Fehler**:
  - Problem: Ungültiger Key in `.env`
  - Lösung: Generiert mit `openssl rand -base64 32`
  - Key: `ejjw6bC6g2FrOWQxK9k2KzfgQFVum23EqzsP/PiqyBA=`
- **Airflow WebServer Command**:
  - Hinzugefügt: `command: bash -c "airflow db init && airflow webserver"`
  - Improved: `depends_on: postgres: condition: service_healthy`
  - **Result**: Airflow startet erfolgreich ✅

### 📊 Status nach Fixes (Ende Session 2 - Part 3)
- ✅ docker-compose.yml: Vollständig konfiguriert
- ✅ Trino: Läuft ✅
- ✅ Dremio: Läuft ✅
- ✅ PostgreSQL: Läuft ✅
- ✅ Airflow: Läuft ✅
- ✅ MinIO: Läuft ✅
- ✅ Nessie: Läuft ✅
- 🟡 Keycloak: Läuft, aber OIDC nicht konfiguriert

---

## [0.4.0-alpha] - Session 5: Metadaten, Tests & Dokumentation (2026-03-19/20)

### 🗂️ dbt Metadaten – schema.yml für alle Schichten

- **`dbt/models/staging/schema.yml`**: Dokumentation `stg_weather` mit allen Spalten, not_null/unique/accepted_values-Tests
- **`dbt/models/data_vault/hubs/schema.yml`**: Dokumentation `h_location` inkl. Business-Key-Beschreibung
- **`dbt/models/data_vault/satellites/schema.yml`**: Dokumentation `s_location_details` + `s_weather_hourly` mit relationships-Tests zu `h_location`
- **`dbt/models/business_vault/schema.yml`**: Vollständige Dokumentation aller 5 Modelle (`dim_location`, `dim_date`, `dim_time`, `fact_weather_hourly`, `fact_weather_daily`) inkl. FK-Tests (relationships) auf alle Dimensionen
- **`dbt/models/marts/schema.yml`**: Dokumentation `weather_trends` mit accepted_values für `granularity`-Spalte

### 🧪 Custom dbt-Tests

- **Verzeichnis `dbt/tests/`** angelegt
- **Ordnerstruktur spiegelt `models/`**: Tests unter `tests/business_vault/fact_weather_hourly/`
- **4 Singular-Tests** erstellt:
  - `assert_hourly_completeness.sql`: Prüft exakt 24 Stunden je Tag und Standort
  - `assert_temperature_plausible.sql`: Temperatur zwischen −40 und +50 °C
  - `assert_apparent_temperature_deviation.sql`: Gefühlte Temperatur max. 20 °C Abweichung
  - `assert_humidity_in_range.sql`: Luftfeuchtigkeit zwischen 0 und 100 %
- Tests ausführbar mit: `dbt test` (alle) oder `dbt test --select test_type:singular` (nur custom)

### 📋 Tasks.md – Neue Themenblöcke

- **Metadatenmanagement**: dbt docs generieren/hosten, Iceberg Table Comments, dbt Exposures
- **Data Lineage**: Lücke Airflow→Raw schließen, OpenLineage/Marquez evaluieren
- **Testing der Verarbeitungsstrecke**: dbt test in Airflow integrieren, Idempotenz-Test, weitere custom Tests

---

## [0.3.0-alpha] - Session 4: Data Pipeline & dbt Modelle (2026-03-19)

### 🔧 trino-init Fix

- **Problem**: `setup_namespaces.sh` schlug fehl mit `exit 127` (python3 nicht im `curlimages/curl` Image)
- **Lösung**: JSON-Parsing von `python3` auf `grep -o | sed` umgestellt
- **Ergebnis**: Alle 4 Namespaces (`raw`, `data_vault`, `business_vault`, `marts`) werden korrekt angelegt

### 🏗️ dbt Projektstruktur

- **Dummy-Modelle entfernt**: Alle Platzhalter-SQL-Dateien gelöscht, Ordnerstruktur mit `.gitkeep` erhalten
- **`dbt_project.yml`**: `dim_date` explizit als `materialized: view` konfiguriert (überschreibt Business-Vault-Default `table`)
- **`packages.yml`**: `dbt-utils` + `automate-dv` eingetragen (noch nicht mit `dbt deps` installiert)
- **`profiles.yml`**: `method: none`, `host: trino`, `port: 8080`, `catalog: iceberg`, `schema: raw`

### ✈️ Airflow DAG: open_meteo_to_raw

- **Datei**: `airflow/dags/open_meteo_to_raw.py`
- **Quelle**: Open-Meteo Archive API (`https://archive-api.open-meteo.com/v1/archive`) – kostenlos, kein API-Key
- **Zeitplan**: `0 6 * * *`, `start_date=2020-01-01`, `catchup=True`, `max_active_runs=3`
- **Task 1 `fetch_to_landing`**: GET → JSON → MinIO `landing/json/weather/YYYY-MM-DD.json`
- **Task 2 `landing_to_raw`**: MinIO JSON → Trino INSERT in `iceberg.raw.weather_hourly`
- **Idempotenz**: DELETE + INSERT pro Tag + Standort (kein Duplikat-Risiko)
- **Tabellen-Auto-Create**: PARQUET-Format, partitioniert nach `date_key`
- **Airflow Variables** (manuell zu setzen): `WEATHER_LATITUDE`, `WEATHER_LONGITUDE`, `WEATHER_LOCATION_KEY`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`
- **`airflow/Dockerfile`**: `boto3` hinzugefügt (für MinIO S3-Zugriff)

### 🗄️ dbt Modelle – vollständige Open-Meteo Pipeline

#### Staging
- **`stg_weather`** (ephemeral): Hash-Keys berechnen (`dbt_utils.generate_surrogate_key`), Typen casten, NULL-Filter
- **`_sources.yml`**: Source-Definition für `iceberg.raw.weather_hourly`

#### Data Vault
- **`h_location`** (incremental): Hub mit Business Key `location_key`, idempotent via `location_hk not in ...`
- **`s_location_details`** (incremental): Koordinaten historisiert per `location_hashdiff`
- **`s_weather_hourly`** (incremental): Alle Messwerte historisiert per `weather_hashdiff`, unique_key

#### Business Vault
- **`dim_location`** (table): Aktuellster Stand via `qualify row_number()`
- **`dim_date`** (view): Kalender 2020–2030 mit relativen Feldern (is_yesterday, days_ago etc.)
- **`dim_time`** (table): 24 Zeilen, Tagesabschnitte, `is_peak` für Stromtarif-Vergleich vorbereitet
- **`fact_weather_hourly`** (incremental): FKs auf alle 3 Dims, unique_key `[location_hk, measured_at]`
- **`fact_weather_daily`** (incremental): Tagesaggregation (min/max/avg Temp, Niederschlag-Summe, Wind)

#### Marts
- **`weather_trends`** (table): UNION ALL aus daily (letzte 90 Tage), weekly, monthly

### 📖 README.md Erweiterungen

- Layer-Architektur mit ASCII-Diagramm für alle 6 Schichten dokumentiert
- Ephemeral-Staging-Konzept mit CTE-Beispiel erklärt
- Alle Dummy-Modell-Referenzen entfernt

---

## [0.2.0-alpha] - Session 3: OIDC / SSO Integration

### 🔐 Keycloak OIDC für MinIO – komplett funktionsfähig

#### Keycloak Konfiguration
- **Realm "lakehouse"** erstellt via Admin Console
- **Volume `keycloak_data`** hinzugefügt für Realm-Persistenz (Realm verschwand ohne Volume bei Restart)
- **Port auf 8082 intern** umgestellt (`KC_HTTP_PORT=8082`, Mapping `8082:8082`)
  - **Grund**: Browser-Redirect muss intern + extern denselben Port nutzen
- **Hostname-Config**: `KC_HOSTNAME=keycloak`, `KC_HOSTNAME_PORT=8082`, `KC_HOSTNAME_STRICT=false`
  - `KC_HOSTNAME_STRICT=false` → Keycloak gibt dynamische Hostnames basierend auf Request-Origin zurück

#### MinIO OIDC Konfiguration
- **Image gepinnt** auf `minio/minio:RELEASE.2024-11-07T00-52-20Z`
  - **Bug in `latest`** (RELEASE.2025-09): Console zeigt keinen SSO-Button, `loginStrategy` bleibt auf `form` obwohl OIDC korrekt konfiguriert ist
- **14 OIDC Env Vars** aktiviert, inkl. `MINIO_IDENTITY_OPENID_ENABLE=on` (Default ist `off`!)
- **`MINIO_BROWSER_REDIRECT_URL=http://localhost:9001`** gesetzt
- **`depends_on: keycloak: condition: service_started`** hinzugefügt (DNS-Fehler "no such host" ohne)
- **OIDC Client** `minio` in Keycloak erstellt, Secret in `.env` hinterlegt

#### Browser / Host-Konfiguration
- **`/etc/hosts`**: `127.0.0.1 keycloak` Eintrag erforderlich auf dem Host-Rechner
  - Keycloak gibt `http://keycloak:8082/...` in Redirects zurück → Browser kann das nur mit /etc/hosts auflösen

#### Debugging-Erkenntnisse (Session 3)
- MinIO speichert Config in `./storage/data/.minio.sys/config/` – diese kann Env Vars überschreiben
- `mc admin config set` / `mc idp openid add` funktionieren, aber latest Image ignoriert sie für Console
- Löschen von `.minio.sys` allein reicht bei latest nicht (Console-Bug bleibt)
- **Lösung**: Image-Pin auf funktionierende Version

### 📊 Status nach Session 3
- ✅ Alle 7 Services laufen
- ✅ MinIO SSO via Keycloak funktioniert (`loginStrategy: "redirect"`)
- ✅ Trino SSO via Keycloak funktioniert (HTTPS, OAuth2 → Keycloak Redirect)
- ✅ Airflow SSO via Keycloak funktioniert (FAB OAuth, "Sign in with Keycloak")
- ❌ Dremio OIDC: Enterprise-only, nicht möglich mit OSS
- ✅ Keycloak Realm Auto-Import konfiguriert (Portabilität)
- ✅ Clean Restart Test bestanden – alle Checks grün

### 🔐 Trino OIDC Integration (Session 3, Part 2)

#### Keycloak Client
- **Client `trino`** erstellt im Realm `lakehouse` via REST API
- Redirect URIs: `https://localhost:8443/oauth2/callback`, `https://localhost:8443/ui/api/login/oauth2/callback`
- Web Origins: `https://localhost:8443`, `http://localhost:8080`
- Client Secret in `.env` als `KEYCLOAK_CLIENT_SECRET_TRINO` gespeichert

#### Trino Konfiguration
- **HTTPS Keystore** erstellt: `trino/etc/trino-keystore.jks` (Self-Signed, SAN: localhost, trino)
- **`config.properties`** erweitert:
  - `http-server.authentication.type=OAUTH2`
  - `http-server.https.enabled=true`, Port 8443
  - `http-server.authentication.oauth2.issuer=http://keycloak:8082/realms/lakehouse`
  - `http-server.authentication.allow-insecure-over-http=true` (Dev)
  - `internal-communication.shared-secret` (Pflicht bei aktivierter Auth)
  - `web-ui.authentication.type=OAUTH2`
- **Docker-Compose**: Port `8443:8443` hinzugefügt, `depends_on: keycloak`

#### Debugging-Erkenntnisse
- Trino Web UI ist bei OAuth2 über HTTP deaktiviert (`/ui/disabled.html`) → HTTPS erforderlich
- `internal-communication.shared-secret` ist Pflicht wenn `authentication.type` gesetzt ist
- `docker-compose restart` erstellt Container nicht neu → Port-Änderungen brauchen `up -d`
- Trino unterstützt `${ENV:VAR}` Syntax für Secrets in Properties-Files (ab Version 389+)

#### Test-User
- **`testuser`** / `test123` im Realm `lakehouse` erstellt (für alle OIDC-Services nutzbar)

### 🔐 Airflow OIDC Integration (Session 3, Part 3)

#### Keycloak Client
- **Client `airflow`** erstellt im Realm `lakehouse`
- Redirect URI: `http://localhost:8081/oauth-authorized/keycloak`
- Client Secret in `.env` als `KEYCLOAK_CLIENT_SECRET_AIRFLOW` gespeichert

#### Airflow Konfiguration
- **Custom Dockerfile** (`airflow/Dockerfile`): `authlib` Package hinzugefügt (Pflicht für FAB OAuth)
- **`webserver_config.py`** komplett neu geschrieben für Flask-AppBuilder OAuth:
  - `AUTH_TYPE = AUTH_OAUTH` statt `AUTH_DB`
  - `OAUTH_PROVIDERS` mit `remote_app` Dict für Keycloak
  - Token/Authorize/API URLs über Container-Netzwerk (`http://keycloak:8082`)
  - `AUTH_USER_REGISTRATION = True`, Default-Rolle `Viewer`
  - `AUTH_ROLES_MAPPING` für admin/viewer/user/op
- **Docker-Compose**: `build: ./airflow` statt Pre-Built Image, Volume-Mount für `webserver_config.py`

#### Debugging-Erkenntnisse
- Airflow FAB erwartet direkte Variable-Assignments, keine Funktions-basierte Config
- `authlib` muss im Image vorhanden sein, fehlt im Standard Apache Airflow Image
- Keycloak-URLs müssen im Container-Netzwerk erreichbar sein (`http://keycloak:8082`), NICHT `localhost`

### ❌ Dremio OIDC (Session 3, Part 3)

- **Ergebnis**: Dremio OSS hat **keinen OIDC-Support** – nur Enterprise/Cloud
- **Verifizierung**: `grep -ri "oidc|oauth" /opt/dremio/conf/` → keine Treffer
- **Konsequenz**: Dremio nutzt lokale Accounts (Setup Wizard)

### 🚀 Portabilität & Restart-Persistenz (Session 3, Part 3)

#### Keycloak Realm Auto-Import
- **Realm-Export**: `init-scripts/keycloak/lakehouse-realm.json` (82KB, 2212 Zeilen)
- **Auto-Import**: `--import-realm` Command + Volume-Mount `./init-scripts/keycloak:/opt/keycloak/data/import`
- **Inhalt**: 3 Clients (minio, trino, airflow) mit Secrets + Redirect URIs, Test-User `testuser/test123`
- **Verhalten**: Import nur bei frischer DB, bestehendes Volume wird respektiert

#### Keycloak Healthcheck (Port 9000)
- **Discovery**: Keycloak 26.x nutzt Port **9000** für Management/Health, NICHT den HTTP-Port 8082
- **Problem**: Kein `curl`/`wget` im Keycloak-Container verfügbar
- **Lösung**: Bash TCP Check: `exec 3<>/dev/tcp/localhost/9000`
- **Wirkung**: MinIO + Trino nutzen `depends_on: keycloak: condition: service_healthy` → keine Race Conditions

#### Weitere Portabilitäts-Maßnahmen
- **`.env.example`** erstellt: Template mit `CHANGE_ME_*` Platzhaltern und Dokumentation
- **`scripts/health_check.sh`** erstellt: Verifiziert alle Services inkl. OIDC-Funktionalität
- **Clean Restart** verifiziert: `docker compose down && docker compose up -d` → alle Checks grün

---

## [0.1.0-alpha] - 2026-03-18

### 🆕 Neu hinzugefügt

#### Docker Compose Infrastruktur
- **Keycloak Integration**: OIDC/OAuth2-basierte Authentifizierung für alle Services
  - Keycloak auf Port 8082
  - Automatische Realm-Erstellung (`lakehouse`)
  - OIDC-Clients für MinIO, Airflow, Trino, Dremio

- **Service-Konfiguration mit Umgebungsvariablen**:
  - MinIO (Port 9000, 9001)
  - Nessie Catalog (Port 19120)
  - Trino Query Engine (Port 8080)
  - Dremio (Port 9047)
  - Apache Airflow (Port 8081)
  - PostgreSQL 13 (multi-DB für Airflow und Keycloak)

- **Netzwerk-Konfiguration**:
  - Docker Bridge Network: `lakehouse_network`
  - Health Checks für PostgreSQL
  - Service Dependencies definiert

#### Authentifizierung & Autorisation
- **Keycloak OIDC Clients**:
  - `minio`: OAuth2 für MinIO Console
  - `airflow`: OAuth2 für Airflow WebUI
  - `trino`: OAuth2 für Trino UI
  - `dremio`: OAuth2 für Dremio UI

- **Security Features**:
  - Separate Client Secrets pro Service
  - Redirect URI Konfiguration
  - Token Lifespan Management

#### Konfigurationsdateien

**1. `.env` - Umgebungsvariablen**
   - Keycloak Admin Credentials
   - Service Client ID & Secrets
   - Datenbank-Credentials
   - Airflow FERNET_KEY für Passwort-Verschlüsselung

**2. `docker-compose.yml` - Container-Orchestration**
   - 7 Services mit vollständiger Konfiguration
   - Volumes für Datenpersistenz
   - Health Checks
   - Abhängigkeiten

**3. `airflow/webserver_config.py` - Airflow OIDC**
   - Flask-AppBuilder Security Manager
   - Keycloak Provider Konfiguration
   - OAuth2 Token-Handling
   - User Info Mapping

**4. `trino/config.properties` - Trino OAuth2**
   - OIDC Auth URL, Token URL, Userinfo URL
   - Client ID & Secret
   - Redirect URI für Web-UI

**5. `dremio-etc/dremio.conf` - Dremio OIDC**
   - OIDC Provider URL
   - Realm und Client Configuration
   - Logout URI

**6. `init-scripts/postgres-init.sql` - Database Init**
   - Keycloak Datenbank-Erstellung
   - Benutzer und Berechtigungen
   - Schema-Grants

**7. `init-scripts/setup-keycloak.sh` - Realm Setup**
   - Automatische `lakehouse` Realm-Erstellung
   - 4 OIDC-Clients erstellen (MinIO, Airflow, Trino, Dremio)
   - Redirect URIs automatisch konfigurieren

#### Dokumentation
- **README.md**: Überblick, Architektur, Quick Start
- **KEYCLOAK_SETUP.md**: Detailliertes Keycloak Setup-Guide
- **Changelog.md**: Diese Datei - Versionshistorie
- **Tasks.md**: Anstehende Aufgaben und TODO-Items
- **Memory.md**: Notizen und Erkenntnisse

### ✅ Änderungen

- `docker-compose.yml`:
  - MinIO: OAuth2 für Console aktiviert
  - Airflow: Webserver Authentifizierung aktiviert
  - Trino: OIDC Environment Variablen hinzugefügt
  - Dremio: OIDC Environment Variablen hinzugefügt
  - PostgreSQL: Healthcheck und Multi-DB Support

- `.env`:
  - Struktur überarbeitet mit Kommentaren
  - Alle Client Secrets und Credentials befüllt
  - Production Hinweise hinzugefügt

### 📋 Dokumentation & Prozesse

- **Workspace Instructions** in `.instructions.md`:
  - Alle Tools aktiviert
  - Changelog Dokumentation gefordert
  - Tasks.md für Aufgaben-Management
  - Memory.md für Erkenntnisse
  - Code-Kommentare für Nachvollziehbarkeit

### 🔒 Sicherheit

- **Initiale Secrets**:
  - Keycloak Admin: `admin123` (MUSS in Produktion geändert werden)
  - Service Secrets: `*-secret-key-12345` (MUSS in Produktion geändert werden)
  - DB Passwort: `airflow123`, `keycloak123` (MUSS in Produktion geändert werden)

- **Production-Checkliste** in Dokumentation hinzugefügt:
  - HTTPS/SSL Aktivierung
  - Passwort-Rotation
  - Firewall-Konfiguration
  - Backup-Strategie

### 🐛 Bekannte Einschränkungen

- Keycloak läuft im `start-dev` Modus (Development only)
- Keine verschlüsselte Kommunikation zwischen Services (Port 8082+ nicht SSL)
- Standard-Credentials sollten NICHT in Production verwendet werden
- MinIO läuft im Single-Node Modus

---

## [Pre-Release] - Vor diesem Projekt

### 📦 Bestehende Komponenten (aus workspace_info)
- dbt-Projekte für Heizung, PV, Strom-Datenmodelle
- Apache Airflow DAGs für ETL-Prozesse
- dremio-etc Konfigurationsdateien
- Grafana/Prometheus Monitoring-Setup
- Docker-Support für alle Services

---

## 🔑 Hinweise für zukünftige Versionen

### Zu Implementieren
1. **Production-Ready Setup**
   - SSL/HTTPS Zertifikate
   - Keycloak Production Mode
   - Secret Management (z.B. HashiCorp Vault)

2. **Monitoring & Observability**
   - Prometheus Metrics
   - Grafana Dashboards
   - Distributed Tracing (Jaeger)
   - Log Aggregation (ELK Stack)

3. **Backup & Disaster Recovery**
   - PostgreSQL WAL Archiving
   - MinIO Versioning & Replication
   - Automated Backup Scripts

4. **Additional Features**
   - Iceberg Table Optimization
   - DBT Integration & Tests
   - Apache Spark Integration
   - Tableau/Looker Integration

5. **Testing & CI/CD**
   - Integration Tests für Services
   - GitHub Actions CI/CD
   - Load Testing (k6)
   - Security Scanning

---

## 🔄 Versionierung

Das Projekt folgt [Semantic Versioning](https://semver.org/):

- **MAJOR**: Inkompatible API-Änderungen oder größere Umstrukturierungen
- **MINOR**: Neue Features, abwärtskompatibel
- **PATCH**: Bug-Fixes

Aktuelle Version: **0.1.0-alpha** (Frühe Entwicklungsphase)

---

## 👥 Kontributionen

Für Änderungen bitte:
1. Task in `Tasks.md` anlegen
2. Änderungen implementieren
3. Diese Datei (`Changelog.md`) aktualisieren
4. Memory-Einträge in `Memory.md` hinzufügen (falls relevant)

---

**Letzte Aktualisierung**: 18. März 2026  
**Bearbeitet von**: GitHub Copilot  
**Status**: 🟡 In Development
