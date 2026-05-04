# 📝 Changelog - Lakehouse KI

Alle Änderungen und Versionshistorie des Lakehouse KI Projekts.

## [Unreleased]

### OpenMetadata Elasticsearch cgroupv2 Bug Fix (04.05.2026)

- **`docker-compose.yml`**: `-XX:-UseCGroupMemoryMetricForLimits` zu `ES_JAVA_OPTS` des
  `openmetadata-es` Services hinzugefügt
- **Ursache**: Elasticsearch 7.16.3 (Java 11) stürzt beim Start ab mit
  `NullPointerException: Cannot invoke jdk.internal.platform.CgroupInfo.getMountPoint()`
  unter Linux mit cgroupv2 – die JVM kann die cgroup-Speicherlimits nicht korrekt lesen
- **Betroffener Service**: `openmetadata-es` (Elasticsearch)
- **Fix**: JVM-Flag `-XX:-UseCGroupMemoryMetricForLimits` deaktiviert das Lesen von
  cgroup-Speichermetriken → Elasticsearch startet korrekt

### PyIceberg Bulk-Write DAG + Backfill-Bug-Fixes (04.05.2026)

- **Neuer DAG `energy_backfill_pyarrow.py`**: Schreibt Energy-Preisdaten via PyArrow/PyIceberg direkt
  in den Nessie REST Catalog – kein Trino INSERT, kein Token-Limit. Trino wird nur noch für den
  Skip-Check (SELECT DISTINCT) verwendet.
- **`weather_backfill_landing_to_raw.py`**: Skip-Check von Monats- auf Tages-Ebene korrigiert
  (`CAST(date_key AS VARCHAR)` statt `date_trunc('month', ...)`); DELETE und INSERT
  auf Tag-Granularität umgestellt; batch_size=5000 je INSERT.
- **`energy_backfill_landing_to_raw.py`**: Identische Fixes wie weather-Backfill (`bidding_zone`
  statt `location_key` als Partition-Filter).
- **`airflow/Dockerfile`**: `pyiceberg` → `pyiceberg[pyarrow,s3fs]` (PyArrowFileIO + S3-Support für
  direktes Iceberg-Schreiben ohne Trino).
- **`.env` / `.env.example` / `docker-compose.yml`**: Airflow Variable `NESSIE_URI` ergänzt
  (`http://nessie:19120/iceberg` – Iceberg REST API Endpunkt für PyIceberg).

### Iceberg Snapshot Expiration Policy Update (30.03.2026)

- **DAG `iceberg_expire_snapshots.py` angepasst**:
  - **`raw` Schema**: Snapshots nach 5 Minuten löschen, maximal 2 Snapshots vorhalten
  - **Andere Schemas** (`data_vault`, `business_vault`, `marts`): Default-Werte (7 Tage Retention)
  - Reason: `raw` enthält Rohdaten mit häufigen Ladevorgängen → aggressivere Bereinigung sinnvoll

### Kaggle API Integration (30.03.2026)

- **`.env.example` ergänzt**: Kaggle-Variablen als Dummy-Werte hinzugefügt
  - `KAGGLE_USERNAME=YOUR_KAGGLE_USERNAME`
  - `KAGGLE_API_KEY=KGAT_YOUR_API_TOKEN_HERE`
  - Mit Anleitung: README.md → "🎵 Kaggle API Setup"
- **docker-compose.yml**: `AIRFLOW_VAR_KAGGLE_*` nutzen `.env` Variablen (kein Hardcoding)
- **start.sh**: Kaggle-Code entfernt (Sicherheit: keine hardcodierten Secrets im Script)
- **Sicherheit**: `.env` ist in `.gitignore`, wird nicht committed → API-Keys bleiben lokal

### Stack-Konfiguration: Automatisierung & Remote-Zugriff (30.03.2026)

- **Startskript neu geschrieben**: `start.sh`
  - Automatische Netzwerk-IP-Erkennung (Linux `hostname -I`, macOS `ipconfig getifaddr`)
  - Flexible Parameter-Verarbeitung: `./start.sh [IP] [--build]` (Reihenfolge egal)
  - Optionales `--build` Flag (Default: schneller Restart ohne Image-Rebuild)
  - Automatische Berechtigungen: `find ./airflow/dags -not -path '*/__pycache__/*' -exec chmod 777 {} +` + Logs (schließt `__pycache__` aus, keine Permission-Fehler)
  - **Neue Funktion**: Überprüft `AIRFLOW_FERNET_KEY` + generiert neuen wenn fehlend/ungültig (via `cryptography.Fernet.generate_key()`)
  - Setzt dynamische Umgebungsvariablen:
    - `KEYCLOAK_HOSTNAME`: bei localhost → "keycloak" (Docker-intern), sonst `${EXTERNAL_HOST}`
    - `KEYCLOAK_URL`: bei localhost → "http://keycloak:8082", sonst `http://${EXTERNAL_HOST}:8082`
  - Ruft `init-scripts/update-trino-config.sh` auf vor `docker compose up` (aktualisiert Keycloak-URLs in Trino-Config)
  - Wartet auf Keycloak Health-Check (Port 9000) mit 5s-Intervallen (max. 120s)
  - Triggert `init-scripts/setup-keycloak-secrets.sh` nach Keycloak-Start
  - Aktualisiert Keycloak Redirect URIs via `init-scripts/update-keycloak-redirects.sh` (nur bei nicht-localhost)

- **Keycloak Secrets Management**: `init-scripts/setup-keycloak-secrets.sh` erweitert
  - Liest Secrets aus `.env`
  - Erkennt Placeholder-Werte (CHANGE_ME_*, TODO_*, Länge < 20 Zeichen) mittels `is_valid_secret()`
  - Generiert neue sichere Secrets mit `openssl rand -base64 48` wenn nötig
  - Speichert Secrets in `.env` (für Wiederverwendung und Dokumentation)
  - Injiziert Secrets in Keycloak Admin API (PUT /realms/{realm}/clients/{id})
  - Clients: `minio`, `airflow`, `trino` (getrennte Secrets für jede App)

- **Neues Skript**: `init-scripts/update-trino-config.sh`
  - Ersetzt Docker-interne Keycloak-URLs mit externen IPs in `trino/etc/config.properties`
  - Wird von `start.sh` aufgerufen BEVOR Container starten
  - Verhindert Trino "The value of the 'issuer' claim different than Issuer URL"-Fehler

- **Neues Skript**: `init-scripts/update-keycloak-redirects.sh`
  - Aktualisiert Keycloak Client-Konfiguration via Admin API
  - Setzt Redirect URIs, Web Origins, Root URLs, Admin URLs für externe Host-Namen
  - Unterstützt Clients: `minio`, `airflow`, `trino`
  - Verwendet Python für JSON-Manipulation
  - Nur aktiv wenn `EXTERNAL_HOST != localhost`

- **Airflow Connections Initialization**: `scripts/airflow_init_connections.py` (neu)
  - Erstellt automatisch `trino_default` Connection bei Airflow-Start
  - Config: Host=`trino`, Port=8080, User=`trino_user` (kein Passwort), Schema=`default`, Catalog=`iceberg`
  - Wird in `docker-compose.yml` nach `airflow_init_users.py` aufgerufen
  - Idempotent: prüft ob Connection bereits existiert
  - **Authentifizierung**: Trino akzeptiert Username ohne Passwort für lokale/Development-Zugriffe

- **Airflow Webserver-Konfiguration**: `airflow/webserver_config.py` angepasst
  - OAuth2 Client-Konfiguration für Keycloak:
    - Ergänzt: `'token_endpoint_auth_method': 'client_secret_post'` in `client_kwargs`
    - Ergänzt: `'access_token_method': 'POST'`
    - Ergänzt: `'request_token_url': None`
  - Role Mapping erweitert: `'default-roles-lakehouse': ['User']` hinzugefügt
  - Behebt "unauthorized_client" und "invalid_client_credentials"-Fehler

- **docker-compose.yml Anpassungen**:
  - Airflow Volumes: `./scripts/airflow_init_connections.py` gemountet (war vorher vergessen)
  - Airflow Command: `airflow_init_users.py` → `airflow_init_users.py` + `airflow_init_connections.py`
  - Airflow Umgebungsvariablen:
    - `KEYCLOAK_URL=${KEYCLOAK_URL:-http://keycloak:8082}` (dynamisch, mit Fallback)
    - `AIRFLOW__CORE__DAGS_FOLDER=/opt/airflow/dags` (explizit gesetzt)
  - Keycloak `KC_HOSTNAME=${KEYCLOAK_HOSTNAME:-keycloak}` (dynamisch)
  - Dremio Volume geändert: `./dremio/data:/opt/dremio/data` → `dremio_data:/opt/dremio/data` (benannte Volume, behebt Permission-Fehler)
  - OpenMetadata `openmetadata-db-init` Service: führt DB-Migration vor Hauptserver aus

- **Dokumentation & Debugging-Hinweise**:
  - `/etc/hosts`-Eintrag für Remote-Zugriff: `<IP> keycloak` auf allen Client-Rechnern
  - Service-URLs nach Start: MinIO (9001), Airflow (8081), Trino (8443), Keycloak (8082), OpenMetadata (8585), Dremio (9047)
  - Keycloak Health-Check: prüft Port 9000 (Management-Port), nicht 8082

- **Gelöste Fehler**:
  - ✅ Airflow "DAGs not found": `AIRFLOW__CORE__DAGS_FOLDER` gesetzt + Berechtigungen fixiert
  - ✅ Airflow DAG Processor Permission Error: `chmod -R 777 ./airflow/logs` in `start.sh`
  - ✅ Dremio "path /opt/dremio/data is not writable": benannte Volume statt bind-mount
  - ✅ Trino "issuer claim different" OIDC-Fehler: `update-trino-config.sh` ersetzt URLs
  - ✅ Keycloak OIDC Discovery falsche Issuer-URL: `KC_HOSTNAME` dynamisch + Redirect URIs aktualisiert
  - ✅ Airflow OAuth2 unauthorized_client: Client Secrets, Redirect URIs, Token-Methoden konfiguriert
  - ✅ MinIO OAuth2 Redirect-Fehler: Root URL + Admin URL in Keycloak gesetzt
  - ✅ Airflow Trino Connection undefined: `airflow_init_connections.py` erstellt Connection automatisch

### Cognos Analytics → OpenMetadata Ingestion (25.03.2026)

- **Neues Skript**: `scripts/cognos_to_openmetadata.py`
  - Liest Cognos Data Module JSON und erstellt Dashboard Data Models in OpenMetadata (Spalten, Datentypen, Usage-Tags, Beziehungen, Drill-Hierarchien)
  - Liest Cognos Dashboard JSON und erstellt Dashboard- + Chart-Entitäten in OpenMetadata (Tabs, Widgets, Chart-Typ-Mapping, referenzierte Spalten)
  - Erzeugt Lineage: Trino-Tabellen → Data Models → Dashboard → Charts (Ende-zu-Ende)
  - Classification `CognosAnalytics` mit Tags: Identifier, Attribute, Measure
  - Reine Python-Stdlib, keine externen Abhängigkeiten, idempotent (PUT-Upserts)
  - CLI: `--dry-run` zum Validieren, `-v` für Debug, `--dashboard` für Dashboard-Modus, Env-Vars `OM_URL`, `OM_TOKEN`, `OM_TRINO_SVC`
  - Dashboard-Parser (`CognosDashboard`): Extrahiert Tabs, Widgets, Chart-Typen, referenzierte Spalten mit Slot-Mapping
  - Dashboard-Ingestion: Erstellt OM Dashboard + Charts, verknüpft mit Data Models über Datenmodul-Name-Matching
  - Dry-Run getestet: 9 Query Subjects / 48 Spalten (Datenmodul), 6 Tabs / 13 Widgets / 21 Spalten (Dashboard)
- **Neuer Airflow-DAG**: `airflow/dags/cognos_to_openmetadata_dag.py`
  - Täglicher Lauf um 06:00 UTC
  - Liest JSON-Exporte aus `/opt/airflow/cognos_exports/`
  - Task-Reihenfolge: Datenmodule zuerst, dann Dashboards (Abhängigkeit)
- **Dokumentation**: `ARCHITECTURE.md` Abschnitt "Cognos Data Module → Katalog Bridge" vollständig überarbeitet mit Datenmodul- und Dashboard-Ingestion, Mapping-Tabellen und Lineage-Diagramm
- **Dokumentation**: `Memory.md` technische Referenz ergänzt

### Datenquelle 3: Spotify Charts & Artists – Vollständige Pipeline (24.03.2026)

- **Neue Airflow-DAGs**:
  - `airflow/dags/spotify_initial_load.py` – Einmaliger Bulk-Load der Kaggle-CSVs (Tracks + Charts) → `iceberg.raw.spotify_tracks` und `iceberg.raw.spotify_charts`. Zwei parallele Tasks, idempotent via DELETE+INSERT auf `_source_file`. CSV-Spalten werden auf das Raw-Schema gemappt.
  - `airflow/dags/spotify_artist_update.py` – Wöchentlicher Artist-Enrichment (Montag 05:00 UTC). Liest distinct artist_name aus Raw → Spotify Search API → JSON-Snapshot in MinIO Landing → `iceberg.raw.spotify_artist_snapshots`. Rate-Limit-Handling mit Retry-After. Basis für SCD2 in dim_artist.

- **Neue dbt-Modelle** (vollständige Strecke Staging → Data Vault → Business Vault → Mart):
  - `staging/stg_spotify_track.sql` – ephemeral, Hash-Keys (track_hk, artist_hk, track_hashdiff, audio_hashdiff), Type-Casting, NULL-Filter
  - `staging/stg_spotify_chart.sql` – ephemeral, Hash-Keys (chart_hashdiff, country_hk), Type-Casting
  - `staging/stg_spotify_artist_snapshot.sql` – ephemeral, Hash-Keys (artist_hk, artist_profile_hashdiff), SCD2-Basis
  - `data_vault/hubs/h_track.sql` – Hub für Tracks (BK: track_id)
  - `data_vault/hubs/h_artist.sql` – Hub für Artists, UNION aus Kaggle (artist_name) + API (artist_id), API-BK bevorzugt
  - `data_vault/hubs/h_country.sql` – Hub für Länder/Regionen (BK: region)
  - `data_vault/links/l_track_artist.sql` – **Erster Link im Projekt!** Verknüpft Track ↔ Artist (N:M)
  - `data_vault/satellites/s_track_details.sql` – Track-Stammdaten (Name, Album, Dauer, Genre, Popularity)
  - `data_vault/satellites/s_track_audio_features.sql` – Separater Satellite für Audio Features (statisch)
  - `data_vault/satellites/s_artist_profile.sql` – **SCD2-Basis**: artist_name, genres, popularity, followers; Hashdiff über alle Attribute
  - `data_vault/satellites/s_chart_entry.sql` – Transaktionaler Satellite für Chart-Einträge
  - `business_vault/dim_artist.sql` – **SCD Type 2 Dimension!** LEAD() für valid_to, is_current Flag, version_number
  - `business_vault/dim_track.sql` – Track-Dimension mit Audio Features (aktuellste Version)
  - `business_vault/dim_country.sql` – Länder-Dimension mit CASE-Mapping (region → Ländername)
  - `business_vault/fact_chart_entry.sql` – Faktentabelle mit FKs auf dim_country, dim_date; track_name/artist_name als degenerierte Dimensionen
  - `marts/artist_chart_performance.sql` – Multi-granularer Mart (weekly/monthly): Chart-Entries, Best Position, Total Streams, Distinct Tracks/Regions

- **Source-Definitionen erweitert** (`staging/_sources.yml`):
  - 3 neue Tabellen: `spotify_tracks`, `spotify_charts`, `spotify_artist_snapshots` mit vollständiger Spaltendokumentation

- **Architekturentscheidungen**:
  - SCD2 auf dim_artist (popularity + followers ändern sich wöchentlich → idealer SCD2-Kandidat)
  - Audio Features als eigener Satellite (Split by rate of change: statisch vs. dynamisch)
  - Erster Data Vault Link (l_track_artist) für N:M Track-Artist-Beziehungen
  - Chart-Daten mit degenerierten Dimensionen (track_name/artist_name als Text, kein FK auf dim_track)
  - Wöchentlicher API-Schedule (Spotify Popularity ändert sich nicht täglich signifikant)

### Surrogate Key Strategie für Dimensionen (24.03.2026)

- **Neues dbt-Macro `generate_dimension_sk`** (`dbt/macros/generate_dimension_sk.sql`):
  - Erzeugt deterministischen Hash-Surrogate-Key: `md5(col1 || '|' || col2)`
  - Wiederverwendbar für alle zukünftigen Dimensionen
  - Pipe-Separator verhindert Hash-Kollisionen bei Wert-Konkatenation

- **Dimensionen angepasst** (neuer Surrogate Key als PK):
  - `dim_location.sql` → `location_sk = md5(location_hk || '|' || valid_from)`
  - `dim_price_zone.sql` → `price_zone_sk = md5(price_zone_hk || '|' || valid_from)`
  - Hub Hash Key (`location_hk`, `price_zone_hk`) bleibt als Referenzspalte erhalten

- **Faktentabellen angepasst** (FK auf Surrogate Key statt Hub Hash Key):
  - `fact_weather_hourly.sql` → Join auf `dim_location`, `location_sk` als FK
  - `fact_energy_price_hourly.sql` → Join auf `dim_price_zone`, `price_zone_sk` als FK
  - `fact_weather_daily.sql` → `location_sk` in SELECT + GROUP BY
  - `fact_energy_price_daily.sql` → `price_zone_sk` in SELECT + GROUP BY

- **Marts angepasst** (Joins über Surrogate Key):
  - `weather_trends.sql` → Join über `location_sk`
  - `energy_price_trends.sql` → Join über `price_zone_sk`

- **Schema-Tests angepasst** (`business_vault/schema.yml`):
  - SK-Spalten mit `unique` + `not_null` Tests
  - `relationships`-Tests in Fakten zeigen auf SK statt HK
  - HK-Spalten behalten `not_null`, verlieren `unique`

- **Dokumentation**: ADR in `ARCHITECTURE.md` (Abschnitt 3), Konvention in `Memory.md` (ADR-007)

### Datenquelle 2: Energy-Charts Day-Ahead-Spotpreise (23.03.2026)

- **Neuer Airflow-DAG `energy_charts_to_raw`** (`airflow/dags/energy_charts_to_raw.py`):
  - Holt stündliche Day-Ahead-Spotpreise von `https://api.energy-charts.info/price?bzn=DE-LU`
  - Kein API-Key erforderlich, Lizenz: CC BY 4.0 (Bundesnetzagentur | SMARD.de)
  - Task 1 `fetch_to_landing`: Rohdaten als JSON in MinIO `landing/json/energy_prices/YYYY-MM-DD.json`
  - Task 2 `landing_to_raw`: JSON → `iceberg.raw.energy_price_hourly` (Trino), idempotent via DELETE+INSERT
  - `start_date=2018-10-01` (ältestes Datum für DE-LU; vorher DE-AT-LU, liefert HTTP 404)
  - Airflow Variable: `ENERGY_CHARTS_BIDDING_ZONE` (default: `DE-LU`)

- **Neue dbt-Modelle** (vollständige Strecke Staging → Data Vault → Business Vault → Mart):
  - `staging/stg_energy_price.sql` – ephemeral, Hash-Keys, Typ-Casting, NULL-Filter
  - `data_vault/hubs/h_price_zone.sql` – Hub für Gebotszonen (BK: `bidding_zone`)
  - `data_vault/satellites/s_energy_price_hourly.sql` – Satellit, stündliche Preise, append-only
  - `business_vault/dim_price_zone.sql` – Dimension, TABLE-materialisiert
  - `business_vault/fact_energy_price_hourly.sql` – stündliche Faktentabelle mit FKs auf dim_date, dim_time
  - `business_vault/fact_energy_price_daily.sql` – Tagesaggregation (min, max, avg, stddev, negative_price_hours)
  - `marts/energy_price_trends.sql` – daily/weekly/monthly UNION ALL, direkt für Grafana/Dremio

- **Neue dbt Custom Tests**:
  - `assert_price_plausible.sql`: Preise zwischen -500 und 3000 EUR/MWh (negative Preise erlaubt)
  - `assert_daily_price_completeness.sql`: genau 24 Rows pro Tag und Gebotszone

- **YAML-Ergänzungen** in `_sources.yml`, `staging/schema.yml`, `hubs/schema.yml`,
  `satellites/schema.yml`, `business_vault/schema.yml`, `marts/schema.yml`

### EXTERNAL_HOST – Remote-Zugriff auf den Stack (23.03.2026)

- **Feature**: Neue Variable `EXTERNAL_HOST` in `.env` ermöglicht Zugriff von externen Rechnern
  - Default: `localhost` (keine Verhaltensänderung für lokale Entwicklung)
  - Bei Setzung auf eine IP/Hostname (z.B. `192.168.1.50`):
    - MinIO `MINIO_BROWSER_REDIRECT_URL` zeigt auf `http://<EXTERNAL_HOST>:9001`
    - Keycloak Redirect URIs werden um `EXTERNAL_HOST`-Einträge erweitert (MinIO, Airflow, Trino)
- **`start.sh`** (NEU): Wrapper-Script für VM-Deployments
  - Erkennt Netzwerk-IP automatisch (`hostname -I` auf Linux, `ipconfig getifaddr en0` auf macOS)
  - Setzt `EXTERNAL_HOST` in `.env`
  - Startet `docker compose up -d --build`
  - Wartet auf Keycloak-Healthcheck, dann Update der Redirect URIs
- **`init-scripts/update-keycloak-redirects.sh`** (NEU): Idempotentes Script das Keycloak-Clients via Admin API aktualisiert
  - Fügt EXTERNAL_HOST-basierte Redirect URIs zu bestehenden Clients hinzu (MinIO, Airflow, Trino)
  - Bestehende localhost-Einträge bleiben erhalten
- **Bekannte Einschränkung**: `/etc/hosts`-Eintrag `<VM-IP> keycloak` auf Client-Rechnern weiterhin erforderlich (Split-DNS-Problem mit `KC_HOSTNAME=keycloak`)
  - Langfristige Lösung: Traefik Reverse Proxy + dnsmasq (dokumentiert in Tasks.md)

### dbt Schema-Naming & Deduplizierung (23.03.2026)

- **dbt-Metadaten in OpenMetadata**: dbt-Ingestion-Pipeline manuell getriggert → Beschreibungen, Tags (`dbtTags.hub`, `dbtTags.satellite`), Test-Ergebnisse und **Lineage** sind jetzt im OM-Katalog sichtbar.
  - Lineage-Graph zeigt vollständige Kette: `raw.weather_hourly` → Data Vault → Business Vault → Marts
  - Trino-Ingestion ebenfalls neu getriggert um veraltete `raw_*`-Schemas aus dem Katalog zu entfernen

- **Bug**: dbt-Schemas hießen `raw_data_vault`, `raw_business_vault`, `raw_marts` statt `data_vault`, `business_vault`, `marts`
  - **Root Cause**: dbt konkateniert den Default-Schema aus profiles.yml (`raw`) mit dem Custom-Schema aus dbt_project.yml (`data_vault`) → `raw_data_vault`
  - **Fix**: Custom-Macro `dbt/macros/generate_schema_name.sql` erstellt – verwendet bei gesetztem `+schema` nur den Custom-Namen, ohne Prefix
  - Alte `raw_*`-Schemas + Tabellen in Trino gedroppt

- **Bug**: 4 dbt-Tests schlugen fehl (unique_h_location_location_hk, unique_h_location_location_bk, unique_s_location_details_location_hashdiff, assert_hourly_completeness)
  - **Root Cause**: `h_location.sql` und `s_location_details.sql` nutzten `SELECT DISTINCT`, aber `_loaded_at` variierte pro Ladevorgang → 112 Duplikate pro Standort. Der JOIN in `fact_weather_hourly` multiplizierte dann jede Stunde ×112 (2.688 statt 24 pro Tag).
  - **Fix**: `SELECT DISTINCT` durch `GROUP BY` + `MIN(_loaded_at)` ersetzt in:
    - `dbt/models/data_vault/hubs/h_location.sql`
    - `dbt/models/data_vault/satellites/s_location_details.sql`
  - `dbt run --full-refresh` → 81/81 Tests PASS

### Elasticsearch 7.x Downgrade & CSRF-Fix (23.03.2026)

- **Bug**: OM-Suche lieferte `media_type_header_exception: Invalid media-type value on headers [Content-Type, Accept]`
  - **Root Cause**: OM 1.12.3 bündelt `elasticsearch-java 9.2.4` (shaded). Dieser Client sendet `Content-Type: application/vnd.elasticsearch+json;compatible-with=8`, aber vergisst den passenden `Accept`-Header. ES 8.x lehnt das ab.
  - **Fix**: ES-Image von `8.10.2` auf `7.16.3` downgraded. ES 7.x prüft Vendor-Header nicht.
  - ES-Volume gelöscht und neu erstellt (alle Indizes waren leer, kein Datenverlust)
  - Nach ES-Neustart: `SearchIndexingApplication` getriggert (`POST /v1/apps/trigger/SearchIndexingApplication`)
- **Bug**: OM konnte keine Ingestion-Pipelines deployen → `400: The CSRF token is missing`
  - **Root Cause**: Airflow 3.x FAB Flask-App hat `CSRFProtect` global aktiviert. Die `openmetadata-managed-apis` Plugin hat einen No-Op `@csrf.exempt` Decorator (funktioniert nicht in Airflow 3.x). `AIRFLOW__WEBSERVER__WTF_CSRF_ENABLED=false` wirkt nicht auf die FAB Flask-App.
  - **Fix**: Neue Datei `airflow/webserver_config_ingestion.py` mit `WTF_CSRF_ENABLED = False`, gemountet als `/opt/airflow/webserver_config.py` im Ingestion-Container.
- **Bug**: Ingestion-Pipeline-DAGs nutzten `localhost:8585` als OM-Server-URL → im Container nicht erreichbar
  - **Fix**: `SERVER_HOST_API_URL=http://openmetadata-server:8585/api` im OM-Server gesetzt
- **Ergebnis**: Alle 3 Pipelines (Trino, Airflow, dbt) deployen + triggern erfolgreich, 3 Tabellen + 54 Assets im Katalog

### Airflow Container-Konsolidierung (23.03.2026)

- **3 separate Airflow-Container** (`airflow`, `airflow-scheduler`, `airflow-dag-processor`) zu **1 Container** konsolidiert
  - Einzelner `airflow`-Service startet `api-server`, `scheduler` und `dag-processor` als Background-Jobs
  - Healthcheck prüft `/api/v2/monitor/health` (alle 3 Komponenten)
- `dag-processor` zum OM-Ingestion-Container hinzugefügt (fehlte vorher → keine DAGs sichtbar)
- **Ergebnis**: 3 Container weniger, gleiche Funktionalität, alle 11 Container `healthy`

### OpenMetadata Integration (20.03.2026)

- **3 neue Docker-Compose-Services**: `openmetadata-db`, `openmetadata-es`, `openmetadata-server`
  - UI erreichbar unter: http://localhost:8585 (**Login: admin@open-metadata.org / admin**)
  - REST API / Swagger: http://localhost:8585/swagger-ui
  - OpenLineage-Empfänger aktiv: `POST /api/v1/lineage` (Airflow + dbt senden Events hierhin)

### OpenMetadata Ingestion-Container Bugfix (23.03.2026)

- **Bug**: `openmetadata-ingestion` Container startete nach wenigen Sekunden mit Exit 0 → OM-Katalog blieb leer:
  1. `(cmd &)` Subshell-Syntax: Hintergrundprozesse waren nicht als Jobs des Parent sichtbar, `wait` hatte nichts zu warten → sofortiger Exit
  2. Airflow 3.x `SimpleAuthManager` generiert beim ersten Start ein **zufälliges Passwort** – OM-Server konnte sich mit `admin/admin` nicht verbinden
  3. `airflow users create` existiert in Airflow 3.x nicht mehr (nur für FabAuthManager 2.x)
- **Fix `docker-compose.yml`**:
  - `(airflow api-server &)` → `airflow api-server &` (direkte Job-Hintergrundausführung)
  - `(sleep 5 && airflow scheduler &)` → `sleep 5 && airflow scheduler &`
  - `airflow users create ...` entfernt
  - `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS=admin:admin` hinzugefügt
  - `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=true` hinzugefügt
  - Health-Check von `/health` (Airflow 2.x) auf `/api/v2/monitor/health` (Airflow 3.x) korrigiert
- **Ergebnis**: Container läuft dauerhaft als `healthy`, OM kann Ingestion-Pipelines triggern

### OpenMetadata Vollautomatische Ingestion-Schedules (23.03.2026)

- **Alle drei OM-Ingestion-Pipelines haben nun Cron-Schedules** – Stack läuft nach `docker compose up` vollautomatisch:
  - **Airflow** `lakehouse_airflow_metadata_ingestion`: täglich **02:00 UTC** (DAGs, Tasks, Run-History)
  - **Trino** `lakehouse_trino_metadata_ingestion`: täglich **03:00 UTC** (Tabellen, Schemas, Spalten)
  - **dbt** `lakehouse_dbt_metadata_ingestion`: täglich **04:00 UTC** (Beschreibungen, Tests, Lineage)
- **`scripts/om_setup_schedules.py`** (NEU): Idempotentes Python-Skript das alle drei Schedules per OM-API konfiguriert. Läuft lokal gegen `localhost:8585`. Setzt Schedules zurück nach Stack-Neuanlage.
- **`scripts/om_dbt_ingestion.yaml`** (NEU): Persistente YAML-Konfiguration für manuelle dbt-Ingestion via `docker run`.
- **`airflow/dags/dbt_run_lakehouse_ki.py`**: Neuer Task `dbt_docs_generate` am Ende der Pipeline (`dbt_test → dbt_docs_generate`). Generiert täglich frische `catalog.json` im `dbt/target/` Verzeichnis.
- **`docker-compose.yml`**: Volume-Mount `./dbt/target:/opt/dbt/target:ro` im `openmetadata-ingestion`-Service, damit der OM-Scheduler die dbt-Artefakte lesen kann.

### OpenMetadata dbt-Connector (21.03.2026)

- **dbt-Metadaten erfolgreich nach OpenMetadata ingested (21.03.2026)**:
  - **83 dbt-Records, 164 OM-Records, 0 Fehler, 100% Success in 2.3s**
  - Angereichert: 10 Modelle (staging → data_vault → business_vault → marts)
  - Beschreibungen, Tags und Spalten-Typen aus dbt schema.yml + catalog.json übernommen
  - Lineage aus manifest.json: dbt-Modell-DAG sichtbar in OM
  - `run_results.json`: Test-Status (81 Tests) in OM sichtbar
- **`catalog.json` generiert**: `dbt docs generate` im Airflow-Scheduler-Container ausgeführt → `/opt/dbt/target/catalog.json`
- **Wichtig: dbt ist kein eigener Service in OM** – es reichert den bestehenden `lakehouse_trino`-Service an (`serviceName: lakehouse_trino`)
- **Korrektes YAML-Format** (OM 1.12.3):
  - `sourceConfig.config.type: DBT` (nicht `DBTLocalConfig`)
  - Dateipfade unter `dbtConfigSource.dbtConfigType: local` verschachteln
  - `dbtCatalogFilePath`, `dbtManifestFilePath`, `dbtRunResultsFilePath` als Kinder von `dbtConfigSource`
- **Volume-Mount**: `dbt/target/` als `:ro` in den Ingestion-Container mounten

### OpenMetadata Airflow-Connector + Airflow 3.x DAG-Processor (21.03.2026)

- **Root Cause Airflow 3.x**: In Airflow 3.x ist der DAG-Processor kein integrierter Teil des Schedulers mehr, sondern ein **eigener Prozess** (`airflow dag-processor`). Ohne diesen Prozess werden DAGs nie in die Datenbank serialisiert → OM-Ingestion findet keine Pipelines.
- **Neuer Service `airflow-dag-processor`** (`container_name: lakehouse_airflow_dag_processor`):
  - Command: `airflow dag-processor`
  - Gleiche Volumes wie Scheduler (dags, plugins, logs, dbt, jdbc_drivers)
  - Gleiche Umgebungsvariablen wie Scheduler
  - Abhängig von: `postgres` (healthy)
- **Airflow-Connector in OM erstellt** (Service: `lakehouse_airflow`, ID: `49ee80c2-8f73-42aa-850e-4deddd9fa0e8`):
  - Ingestion-Pipeline ID: `97bed354-3eb3-406d-9c50-7e193024257b`
  - Connection: Postgres-Direct `postgresql+psycopg2://airflow:airflow123@postgres:5432/airflow`
  - Connector liest Airflow-DB **direkt via SQLAlchemy** (kein REST-API-Aufruf)
  - `authType: {password: "..."}` Format – nicht einfaches `password`-Feld
- **Erste Airflow-Ingestion erfolgreich (21.03.2026)**:
  - **27 Records, 0 Fehler, 100% Success in 1.6s**
  - 5 Pipelines ingested: `db2_jdbc_query`, `dbt_run_lakehouse_ki`, `open_meteo_to_raw`, `oracle_jdbc_query`, `postgres_public_query`
  - Ausgeführt via: `docker run --rm --network lakehouse_ki_lakehouse_network openmetadata/ingestion:1.12.3`

**Airflow 3.x User Creation Fix (21.03.2026)**:
- `airflow users create` und `airflow fab` CLI-Commands existieren in Airflow 3.x **nicht mehr**
- Lösung: `scripts/airflow_init_users.py` – nutzt `FabAirflowSecurityManagerOverride(app.appbuilder).add_user(...)` via `airflow.providers.fab.www.app.create_app(enable_plugins=False)`
- docker-compose.yml: Init-Command auf `python /opt/airflow/scripts/airflow_init_users.py` umgestellt
- Volume-Mount: `./scripts/airflow_init_users.py:/opt/airflow/scripts/airflow_init_users.py:ro`
- Erstellt `admin` / `admin` (Admin-Rolle) bei erstem Start

### OpenMetadata Ingestion-Container + Trino-Connector (20.03.2026)

- **Neuer Service `openmetadata-ingestion`** (`openmetadata/ingestion:1.12.3`): Dedizierter Ingestion-Pipeline-Runner
  - Eigener Airflow 3.x Mini-Stack mit Scheduler + API-Server
  - Plugin `openmetadata-managed-apis` wird beim Start automatisch installiert
  - OM-Server Pipeline-Client zeigt auf diesen Container (statt auf Hauptairflow)
  - Port: 8090 (extern) → 8080 (intern), Volume: `openmetadata_ingestion_data`
- **Trino-Connector erstellt und getestet**:
  - Service `lakehouse_trino` in OM angelegt (Service-ID: `462416d7-a94f-4861-839c-bab23b302bfd`)
  - Erste Metadata-Ingestion erfolgreich: **12 Records, 6 Tabellen, 0 Fehler, 100% Success**
  - Crawlt `iceberg.*` Schemas (excl. `information_schema`), inkl. Views und Tags
  - Ausgeführt via: `docker run --rm --entrypoint /bin/bash --network lakehouse_ki_lakehouse_network openmetadata/ingestion:1.12.3 -c "metadata ingest -c ..."`
- **YAML-Konfiguration**: `hostPort` muss `/api` Suffix enthalten (`http://openmetadata-server:8585/api`), da OM-Ingestion-Client den `api_version` (`v1`) selbst anhängt
- **Bot-Token-API**: `GET /api/v1/users/token/{userId}` – nicht `generateToken` (404 in 1.12.3)
- **collate-data-diff ≥0.11.9**: Nicht auf PyPI für Python 3.9 → lokale pip-Installation scheitert; Docker-Image enthält alle Packages
- **`openmetadata-db`** (`postgres:15-alpine`): Dedizierte Postgres-Instanz für OM
  - Bewusst getrennt von shared postgres – OM verwaltet Schema eigenständig via Flyway
  - Volume: `openmetadata_db_data`
- **`openmetadata-es`** (`elasticsearch:8.10.2`): Such-Backend für OM
  - single-node, xpack.security=false, 512 MB Heap (RAM-schonend)
  - Volume: `openmetadata_es_data`
- **`openmetadata-server`** (`openmetadata/server:latest`): OM Server
  - Auth: OM-native (`basic`), Keycloak-OIDC konfigurierbar (Anleitung in Memory.md)
  - Airflow-Integration: OM kann Ingestion-Workflows als DAGs triggern
- **`.env.example`**: `OPENMETADATA_DB_USER`, `OPENMETADATA_DB_PASSWORD`, `KEYCLOAK_CLIENT_ID_OPENMETADATA` ergänzt
- **Hintergrund**: OpenMetadata wurde gegenüber DataHub ausgewählt – weniger RAM-Bedarf (3 vs. 7 Container), nativer DQ-Test-Runner, modernere UI. Abwägung in ARCHITECTURE.md Abschnitt 7.2.
- **`daemon.json`**: `"ipv6": false` + explizite DNS-Server ergänzt – behebt Docker-Desktop-Bug auf macOS bei Cloudflare R2 Layer-Pulls (`no route to host` über IPv6).

**Bugfixes beim ersten Start (20.03.2026)**:
- **`DB_SCHEME` korrigiert**: `postgresql+psycopg2` → `postgresql` – OM ist Java, erwartet JDBC-Schema, kein Python/SQLAlchemy-Dialekt.
- **`DB_DRIVER_CLASS` ergänzt**: `org.postgresql.Driver` explizit gesetzt – OM-Image hat MySQL als Default-Treiber.
- **`DB_PARAMS` leer gesetzt**: MySQL-spezifische URL-Parameter (`allowPublicKeyRetrieval`, `serverTimezone`) entfernt – PostgreSQL-Treiber kennt diese nicht.
- **DB-Migration manuell ausgeführt**: `latest`-Tag erzwingt expliziten Migrationsschritt vor erstem Serverstart: `./bootstrap/openmetadata-ops.sh migrate`.
- **Initialpasswort korrigiert**: OM setzt bei PostgreSQL + basic-Auth `admin` als Passwort (nicht `Admin@1234!` wie in der Doku – das gilt nur beim MySQL-Default-Setup).

### Demo-DAGs für Datenbankanbindungen (20.03.2026)

- **`postgres_public_query.py`**: Voll funktionaler DAG gegen RNAcentral (EMBL-EBI, öffentlich)
  - PostgresHook mit Connection `postgres_rnacentral`
  - Abfrage: Top-10 Quell-Datenbanken nach RNA-Sequenzanzahl aus `xref`, `database`, `rna`
  - Pipeline: PostgreSQL → MinIO Landing (`landing/json/rnacentral/`) → `iceberg.raw.rnacentral_stats`

- **`oracle_jdbc_query.py`**: Template-DAG für Oracle über JDBC
  - JayDeBeApi + ojdbc11.jar (Oracle 19c/21c/23ai), Treiber in `/opt/jdbc_drivers/`
  - Abfrage: ALL_TABLES Metadaten (Schema, Zeilenanzahl, Blockanzahl)
  - Pipeline: Oracle → MinIO Landing → `iceberg.raw.oracle_tables`
  - `is_paused_upon_creation=True` – aktiv sobald Connection `oracle_jdbc` hinterlegt

- **`db2_jdbc_query.py`**: Template-DAG für IBM DB2 über JDBC
  - JayDeBeApi + db2jcc.jar (DB2 11.5 / Db2 on Cloud), Treiber in `/opt/jdbc_drivers/`
  - Abfrage: SYSIBM.SYSTABLES Metadaten (Schema, Tabellen, Zeilenanzahl)
  - Pipeline: DB2 → MinIO Landing → `iceberg.raw.db2_tables`
  - `is_paused_upon_creation=True` – aktiv sobald Connection `db2_jdbc` hinterlegt

- **Alle DAGs**: Idempotent (DELETE + INSERT per Tag), partitioniert nach `query_date`

### Airflow 3.1.8 Migration (19.03.2026)

- **Base Image Upgrade**: `apache/airflow:2.8.4-python3.11` → `apache/airflow:3.1.8-python3.11`
  - **Grund**: Neueste LTS-Version mit erweiterten Features, bessere Python-Unterstützung
  - **Airflow Version im Container**: 3.1.8 (vorher unreleased Tag 2.11.2)

- **Breaking Changes bewältigt**:
  - `airflow webserver` → `airflow api-server` (docker-compose.yml command)
  - `airflow db init` → `airflow db migrate` (database initialization)
  - Alle Provider-Versionen aktualisiert

- **Provider-Upgrades**:
  - `apache-airflow-providers-trino:6.5.0` (von 4.x)
    - ⚠️ **Breaking**: TrinoOperator entfernt—nur TrinoHook verfügbar
    - Impact: `open_meteo_to_raw.py` nutzt bereits TrinoHook (kein Code-Change nötig)
  - `apache-airflow-providers-http:6.0.0`

- **DAG Fixes** (`airflow/dags/`):
  - `dbt_run_lakehouse_ki.py`:
    - Import: `from airflow.operators.bash` → `from airflow.providers.standard.operators.bash`
    - Schedule: `schedule_interval="@daily"` bereits korrekt
    - ✅ Tests bestanden (3 Tasks ausgelesen: dbt_deps, dbt_run, dbt_test)
  
  - `open_meteo_to_raw.py`:
    - Import: `from airflow.operators.python` → `from airflow.providers.standard.operators.python`
    - Schedule: `schedule_interval="0 6 * * *"` → `schedule="0 6 * * *"`
    - Removed: `from airflow.providers.trino.operators.trino import TrinoOperator` (nicht in 6.5.0)
    - TrinoHook-Nutzung in `landing_to_raw()` unverändert (nutzt bereits `TrinoHook.run()`)
    - ✅ Tests bestanden (2 Tasks ausgelesen: fetch_to_landing, landing_to_raw)

- **dbt-trino Kompatibilität**:
  - dbt-trino 1.10.1 bleibt stabil (1 Patch in connections.py für behavior_flags null-guard)
  - Automatische Auflösung: `dbt-adapters>=1.22.9` fixed behavior_flags-Bug nativ
  - Alle 9 dbt-Modelle PASS (0 Regressions)

- **GitHub Commits**:
  - Commit 1: Database schema + Layer-Struktur
  - Commit 2: `fix: migrate open_meteo_to_raw DAG to Airflow 3.x`

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

**Letzte Aktualisierung**: 25. März 2026  
**Bearbeitet von**: GitHub Copilot  
**Status**: 🟡 In Development
