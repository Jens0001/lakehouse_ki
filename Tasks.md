# Tasks - Lakehouse KI

## Offen

### Datenquelle 1: Open-Meteo – nächste Schritte
- [x] **Airflow Variables setzen** (Airflow UI → Admin → Variables) *(erledigt 23.03.2026)*
- [x] **DAG `open_meteo_to_raw` triggern** – Backfill ab 2020-01-01 *(erledigt 23.03.2026, 12.552 stündliche Messwerte geladen)*
- ℹ️ **dbt-Pakete** – werden automatisch vom DAG `dbt_run_lakehouse_ki` via `dbt deps` installiert (kein manueller Schritt nötig)
- [x] **dbt-Modelle ausführen** – `dbt run --full-refresh` *(erledigt 23.03.2026, 9 Models PASS)*
- [x] **dbt-Tests bestehen** – `dbt test` 81/81 PASS *(erledigt 23.03.2026)*

### Datenquellen (geplant, noch nicht begonnen)
- [ ] **OpenAQ Luftqualität** (`https://api.openaq.org`) – PM2.5, NO2, CO für Messstationen weltweit
- [ ] **CoinGecko Krypto-Kurse** (`https://api.coingecko.com`) – stündliche/tägliche Preise

### Datenquelle 3: Spotify Charts & Artists – nächste Schritte

#### Voraussetzungen
- [ ] **Kaggle-Datasets herunterladen** und als CSV nach MinIO hochladen:
  - `mc cp spotify_tracks.csv lakehouse/lakehouse/landing/csv/spotify/tracks/`
  - `mc cp spotify_charts.csv lakehouse/lakehouse/landing/csv/spotify/charts/`
  - Empfohlene Datasets:
    - Tracks: https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset
    - Charts: https://www.kaggle.com/datasets/dhruvildave/spotify-charts
- [x] **Spotify Developer App erstellen** → Client ID + Client Secret (27.03.2026)
  - https://developer.spotify.com/dashboard → App erstellen → Client Credentials notieren
  - ✅ API-Verbindung getestet: Token-Generierung + Artist-Suche funktioniert
- [x] **Airflow Variables setzen** (27.03.2026):
  - `SPOTIFY_CLIENT_ID` = ✅ gespeichert
  - `SPOTIFY_CLIENT_SECRET` = ✅ verschlüsselt gespeichert

#### Bulk-Load (einmalig)
- [ ] **DAG `spotify_initial_load` triggern** – Lädt Tracks + Charts CSVs → iceberg.raw
  - Voraussetzung: CSVs liegen in MinIO unter `landing/csv/spotify/`
  - Zwei parallele Tasks: `load_tracks_to_raw`, `load_charts_to_raw`
- [ ] **Verifikation in Trino**:
  ```sql
  SELECT count(*) FROM iceberg.raw.spotify_tracks;
  SELECT count(*) FROM iceberg.raw.spotify_charts;
  SELECT count(distinct region) FROM iceberg.raw.spotify_charts;
  ```

#### Artist Enrichment (wöchentlich)
- [ ] **DAG `spotify_artist_update` triggern** – Erster manuelle Run für Baseline
  - Holt alle distinct artist_name → Spotify Search API → Artist-Snapshots
  - Schreibt nach `iceberg.raw.spotify_artist_snapshots`
- [ ] **Verifikation**:
  ```sql
  SELECT count(*) FROM iceberg.raw.spotify_artist_snapshots;
  SELECT artist_name, popularity, followers FROM iceberg.raw.spotify_artist_snapshots LIMIT 10;
  ```

#### dbt-Modelle ausführen
- [ ] **dbt run** – `dbt run --select stg_spotify_track+` (oder `dbt run --full-refresh` für alle)
  - Neue Modelle: h_track, h_artist, h_country, l_track_artist, s_track_details,
    s_track_audio_features, s_artist_profile, s_chart_entry, dim_artist (SCD2!),
    dim_track, dim_country, fact_chart_entry, artist_chart_performance
- [ ] **dbt test** – `dbt test --select stg_spotify_track+` → alle Tests grün prüfen
- [ ] **SCD2-Validierung** (nach zweitem `spotify_artist_update`-Run):
  ```sql
  -- Muss historische Rows zeigen (is_current = false)
  SELECT * FROM iceberg.business_vault.dim_artist WHERE NOT is_current ORDER BY artist_name LIMIT 20;
  -- Versionsverlauf eines populären Artists
  SELECT * FROM iceberg.business_vault.dim_artist WHERE artist_name LIKE '%Drake%' ORDER BY valid_from;
  ```


- [ ] **Airflow Variable setzen** (Airflow UI → Admin → Variables): `ENERGY_CHARTS_BIDDING_ZONE = DE-LU`
- [ ] **DAG `energy_charts_to_raw` triggern** – Backfill ab 2018-10-01 (ältestes verfügbares Datum für DE-LU)
  - Hinweis: `max_active_runs=3` begrenzt parallele Runs; Backfill läuft ca. 2.700 Tage durch
  - Idempotent: kann jederzeit erneut getriggert werden
- [ ] **dbt-Modelle ausführen** – `dbt run --select stg_energy_price+` (oder full-refresh)
  - Neue Modelle: `h_price_zone`, `s_energy_price_hourly`, `dim_price_zone`,
    `fact_energy_price_hourly`, `fact_energy_price_daily`, `fact_energy_price_monthly`, `energy_price_trends`
- [ ] **dbt-Tests** – `dbt test --select stg_energy_price+` → alle Tests grün prüfen
- [ ] **Verifikation in Trino**:
  ```sql
  SELECT count(*) FROM iceberg.raw.energy_price_hourly WHERE date_key = DATE '2024-01-01';
  -- erwartet: 24
  SELECT * FROM iceberg.marts.energy_price_trends LIMIT 10;
  ```

  - [x] **Neues Modell `fact_energy_price_monthly` implementiert** (24.03.2026)
    - Erstellt auf Basis von `fact_energy_price_daily`
    - Aggregiert stündliche Daten monatsweise
    - Enthält Min, Max, Durchschnitt und Standardabweichung pro Monat und Gebotszone
    - Wird in `business_vault` abgelegt
    - Dokumentation in `schema.yml` aktualisiert
    - In Tasks.md als erledigt markiert
### Use Cases (niedrige Priorität)
- [ ] **Hypothetischer Stromtarif-Vergleich** *(Prio: niedrig, Abhängigkeit: Smarthome-Verbrauchsdaten in Iceberg)*
  - Preisdaten jetzt verfügbar: `iceberg.business_vault.fact_energy_price_hourly` (ab 2018-10-01)
  - Noch fehlend: stündliche Verbrauchsdaten aus `airflow_smarthome` in Iceberg laden
  - Join mit eigenem Smarthome-Stromverbrauch (stündlich)
  - Star-Schema: `dim_time`, `dim_tariff` (fix vs. dynamisch), `fact_consumption_cost`
  - Mart: `tariff_comparison` – hypothetische Kosten fix vs. dynamisch im Vergleich

---

### Metadatenmanagement
- [x] **OpenMetadata aufsetzen** (3 Services: DB, ES, Server)
- [x] **Trino-Connector**: 12 Records, 6 Tabellen, 0 Fehler (20.03.2026)
- [x] **Airflow-Connector**: 27 Records, 5 Pipelines, 0 Fehler (21.03.2026)
  - Blocker war: Airflow 3.x DAG-Processor braucht eigenen Container → `airflow-dag-processor` Service ergänzt
- [x] **dbt-Connector**: 83 Records, 10 Modelle, 0 Fehler (21.03.2026)
  - `catalog.json` via `dbt docs generate` generiert
  - Beschreibungen, Tags, Lineage und Test-Ergebnisse in OM sichtbar
- [x] **OM-Ingestion Vollautomatik** (23.03.2026): Alle drei Connectors laufen täglich per Schedule
  - Trino 03:00, Airflow 02:00, dbt 04:00 UTC
  - `scripts/om_setup_schedules.py` setzt Schedules nach Stack-Neuanlage
  - DAG `dbt_run_lakehouse_ki` generiert täglich `catalog.json` via `dbt_docs_generate`-Task
- [ ] **dbt-Dokumentation generieren und hosten**
  - `dbt docs generate` erzeugt `catalog.json` + `manifest.json`
  - `dbt docs serve` startet lokalen Webserver mit durchsuchbarem Datenkatalog
  - Prüfen: ob `dbt docs serve` im Airflow-Container sinnvoll oder eigener Service besser
  - Mittelfristig: `catalog.json` in MinIO ablegen und per Static-Hosting erreichbar machen

- [ ] **Iceberg Table Properties mit Beschreibungen anreichern**
  - Trino unterstützt `COMMENT ON TABLE` und `COMMENT ON COLUMN`
  - Alternativ: Nessie Catalog-Metadaten (falls REST-API das unterstützt)
  - Ziel: Beschreibungen auch außerhalb von dbt (z.B. in Dremio) sichtbar

- [ ] **dbt-Exposures definieren** (`exposures:` in schema.yml)
  - Dashboards und nachgelagerte Systeme als Consumers dokumentieren
  - Beispiel: Grafana-Dashboard referenziert `weather_trends` und `fact_weather_daily`
  - Macht Abhängigkeiten in `dbt docs` sichtbar (Lineage bis zum Dashboard)

---

### Data Lineage

> **Ziel**: Lückenlose End-to-End Lineage vom Datenproduzenten bis zum BI-Tool – vollständig im Governance Katalog sichtbar. OpenLineage ist das Transportprotokoll; der Katalog (DataHub / OpenMetadata) ist der Empfänger und Visualisierer.

- [ ] **OpenLineage-Emitter konfigurieren** *(nach Katalog-Auswahl)*
  - **Airflow**: `apache-airflow-providers-openlineage` installieren, Umgebungsvariable `OPENLINEAGE_URL` auf Katalog-Endpunkt setzen
    - Emittiert: DAG-Run-Lineage (Input-/Output-Datasets pro Task)
    - Abdeckung: `open_meteo_to_raw` → `iceberg.raw.weather_hourly` als Dataset-Event
  - **dbt**: `openlineage-integration-common` + Katalog-Adapter installieren
    - Emittiert: Modell-Lineage aus `manifest.json` und `run_results.json`
    - Abdeckung: stg → data_vault → business_vault → marts komplett
  - Ergebnis: Alle Lineage-Events landen automatisch im Katalog – kein manuelles Mapping

- [ ] **Lineage-Lücken identifizieren und schließen**
  - Lücke 1: Externe Quelle (Open-Meteo API) → Airflow *(manuell als Upstream-Dataset im Katalog einpflegen)*
  - Lücke 2: dbt marts → Dremio VDS *(Dremio-Connector im Katalog konfigurieren)*
  - Lücke 3: Dremio → Cognos *(als dokumentarische Exposure in dbt schema.yml erfassen)*
  - Ziel: kein "weißer Fleck" in der Kette API → Landing → Raw → DV → BV → Marts → BI

---

### Testing der Verarbeitungsstrecke
- [x] **dbt-Tests nach erstem `dbt run` ausführen** *(erledigt 23.03.2026 – 81/81 PASS)*
  - `dbt test` führt alle schema.yml-Tests (not_null, unique, relationships, accepted_values) aus
  - `dbt test --select test_type:singular` führt nur die custom Tests in `tests/` aus
  - Custom Test `assert_hourly_completeness` prüft 24h pro Tag/Standort

- [x] **dbt-Tests in Airflow-DAG integrieren** *(bereits im DAG `dbt_run_lakehouse_ki` als Task `dbt_test` enthalten)*
  - Reihenfolge: `dbt_deps` → `dbt_run` → `dbt_test` → `dbt_docs_generate`
  - Bei Testfehler: DAG-Task schlägt fehl (BashOperator exit code != 0)

- [ ] **Airflow-DAG-Tests: Idempotenz prüfen**
  - DAG `open_meteo_to_raw` manuell zweimal für denselben Tag triggern
  - Erwartung: kein doppelter Eintrag in `iceberg.raw.weather_hourly` (DELETE + INSERT ist idempotent)
  - Prüfabfrage: `SELECT date_key, count(*) FROM iceberg.raw.weather_hourly GROUP BY date_key HAVING count(*) > 24`

- [ ] **Weitere custom dbt-Tests ergänzen** *(nach erstem erfolgreichen Lauf)*
  - `fact_weather_daily`: precipitation_sum >= 0, temperature_min <= temperature_max
  - `dim_date`: date_id eindeutig und lückenlos zwischen 20200101 und 20301231
  - `s_weather_hourly`: keine Dopplungen (location_hk + measured_at eindeutig)

- [ ] **Erweiterte Data Quality Checks: dbt-expectations oder Great Expectations**
  - **Problem**: Aktuelle dbt-Tests prüfen nur Schema-Ebene (not_null, unique, accepted_values, relationships). Inhaltliche Anomalien werden nicht erkannt – z.B. plötzlicher Einbruch der Zeilenanzahl, statistische Ausreißer, fehlende Tage in Zeitreihen.
  - **Option A – dbt-expectations** (bevorzugt, kein neuer Service):
    - Package `calogica/dbt_expectations` in `packages.yml` ergänzen
    - Beispiel-Tests für bestehende Modelle:
      - `expect_row_count_to_be_between` auf `iceberg.raw.weather_hourly` (min 24 Rows pro Tag×Standort)
      - `expect_column_values_to_be_between` auf `temperature_2m` (z.B. -50°C bis +60°C)
      - `expect_column_mean_to_be_between` auf `price_eur_mwh` (historischer Mittelwert ±3σ)
      - `expect_table_row_count_to_equal_other_table` → Raw vs. Staging Zeilenzahl-Abgleich
      - `expect_multicolumn_sum_to_be_between` für Konsistenz-Checks (z.B. precipitation_sum ≈ Σ hourly)
    - Tests in `schema.yml` der jeweiligen Modelle ergänzen, laufen im bestehenden `dbt test`-Task
  - **Option B – Great Expectations** (eigenständig, mehr Aufwand):
    - GE als Python-Package im Airflow-Container installieren
    - Eigener Airflow-Task nach `dbt_run` und vor `dbt_test`
    - Vorteil: Profiling-Reports (HTML), Data Docs als statische Seite hostbar
  - **Ziel**: Anomalien in Datenlieferungen automatisch erkennen, bevor sie in Marts landen

- [ ] **dbt-Modelle auf inkrementelle Materialisierung umstellen**
  - **Problem**: Alle dbt-Modelle nutzen aktuell `materialized='table'` (Full Refresh bei jedem Run). Bei wachsenden Datenmengen (Weather: 12k+ Rows, Energy: potentiell Millionen stündliche Preise) wird das zunehmend langsam und ressourcenintensiv.
  - **Kandidaten für `materialized='incremental'`** (nach Priorität):
    1. **`s_weather_hourly`** – Satellite, append-only Natur, ideal für incremental
       - Strategie: `incremental_strategy='append'`, Filter `WHERE _loaded_at > (SELECT MAX(_loaded_at) FROM {{ this }})`
    2. **`s_energy_price_hourly`** – gleiche Logik wie Weather-Satellite
    3. **`fact_weather_hourly`** / **`fact_energy_price_hourly`** – Business Vault Facts
       - Strategie: `merge` auf `unique_key` (z.B. `location_hk || '_' || measured_at`)
    4. **`fact_weather_daily`** / **`fact_energy_price_daily`** – Aggregate, nur neue Tage nachrechnen
       - Filter: `WHERE date_key >= (SELECT MAX(date_key) - INTERVAL '2' DAY FROM {{ this }})`
  - **Nicht umstellen** (Full Refresh sinnvoll):
    - Hubs (`h_location`, `h_price_zone`) – klein, Deduplizierung erfordert Gesamtbild
    - Dims mit SCD2 (`dim_artist`) – LEAD() Window Function braucht alle Rows
    - Marts (`weather_trends`, `energy_price_trends`) – aggregiert, klein
  - **Voraussetzung**: Trino + Iceberg muss `MERGE INTO` unterstützen (ab Trino 400+ und Iceberg v2 gegeben)
  - **Testplan**: Erst ein Modell umstellen (z.B. `s_weather_hourly`), mit `dbt run --select s_weather_hourly` testen, Zeilenanzahl vor/nach vergleichen, dann weitere Modelle schrittweise

- [ ] **OpenLineage in Airflow aktivieren**
  - **Problem**: OM-native Ingestion liefert Lineage aus dbt-Artefakten (manifest.json), aber keine **Runtime-Lineage** – d.h. es fehlt die Info, welcher konkrete DAG-Run welche Partition/Tabelle geschrieben hat, mit welchen Input-Datasets und wann.
  - **Umsetzung** (minimaler Aufwand – eine Env-Variable + ein Package):
    1. `apache-airflow-providers-openlineage` in `airflow/Dockerfile` installieren (pip install)
    2. Env-Variable in `docker-compose.yml` unter `airflow.environment` ergänzen:
       - `OPENLINEAGE_URL=http://openmetadata-server:8585/api/v1/lineage`
       - `OPENLINEAGE_NAMESPACE=lakehouse_airflow`
    3. OM empfängt dann automatisch OpenLineage-Events bei jedem Task-Run
  - **Was danach sichtbar wird**:
    - DAG `open_meteo_to_raw`: Input `open-meteo.com API` → Output `iceberg.raw.weather_hourly` (pro Run)
    - DAG `energy_charts_to_raw`: Input `api.energy-charts.info` → Output `iceberg.raw.energy_price_hourly`
    - DAG `dbt_run_lakehouse_ki`: Input `iceberg.raw.*` → Output `iceberg.marts.*` (gesamte dbt-Lineage als Runtime-Events)
  - **Validierung**: Nach einem DAG-Run in OM UI → Lineage-Tab der Tabelle prüfen → Runtime-Kanten müssen sichtbar sein
  - **Hinweis**: OM-native Airflow-Ingestion (täglich 02:00 UTC) liefert DAG-Struktur, OpenLineage ergänzt die Runtime-Ebene – beides komplementär, kein Entweder-Oder

---

### Data Governance Katalog
- [x] **Tool-Evaluation: DataHub vs. OpenMetadata** *(erledigt 20.03.2026 – Entscheidung: OpenMetadata)*
  - Begründung: geringerer RAM-Bedarf (3 vs. ~7 Container), nativer DQ-Test-Runner, modernere UI
  - Details: ARCHITECTURE.md Abschnitt 7.2

- [x] **OpenMetadata in Docker Compose integrieren** *(erledigt 20.03.2026)*
  - ✅ 3 Services (`openmetadata-db`, `openmetadata-es`, `openmetadata-server`) im Stack
  - ✅ Port 8585 reserviert, `.env.example` ergänzt
  - ✅ OpenLineage-Receiver aktiv (`POST /api/v1/lineage`)
  - ✅ `OPENMETADATA_DB_PASSWORD=openmetadata123` in `.env` eingetragen (20.03.2026)
  - **Erster Login**: http://localhost:8585 → admin@open-metadata.org / admin (getestet 20.03.2026)
  - **Healthcheck-Reihenfolge**: OM-Server startet erst wenn `openmetadata-db` + `openmetadata-es` healthy sind – ES braucht ~30-60s

- [x] **OpenMetadata Ingestion-Container** *(erledigt 20.03.2026)*
  - ✅ Service `openmetadata-ingestion` (Image `openmetadata/ingestion:1.12.3`) in docker-compose.yml
  - ✅ OM Pipeline-Client zeigt auf Ingestion-Container (Port 8080 intern, 8090 extern)
  - ✅ Plugin `openmetadata-managed-apis` wird beim Start installiert (Airflow 3.x kompatibel)

- [x] **OpenMetadata: Connectors konfigurieren** *(erledigt 23.03.2026)*
  - [x] **Trino-Connector**: Service `lakehouse_trino` angelegt, Ingestion erfolgreich (Tabellen, Schemas, Spalten)
  - [x] **Trino-Connector triggern**: Läuft automatisch täglich 03:00 UTC + manuell per API
  - [x] **dbt-Connector**: `manifest.json` + `catalog.json` + `run_results.json` importiert – Beschreibungen, Tags (`dbtTags.hub`, `dbtTags.satellite`), Test-Ergebnisse und Lineage sichtbar
  - [x] **Airflow-Connector**: DAGs, Tasks, Run-History im Katalog
  - [ ] **Airflow OpenLineage**: `OPENLINEAGE_URL=http://openmetadata-server:8585` in Airflow-Env setzen *(optional – OM-native Ingestion liefert bereits Lineage)*
  - [ ] **Dremio-Connector**: VDS-Metadaten crawlen *(nachgelagert, wenn VDS genutzt werden)*
  - [ ] **Keycloak-OIDC** für OM aktivieren *(optional)*: Anleitung in Memory.md

- [ ] **End-to-End Lineage validieren** *(lückenlose Kette sicherstellen)*

---

### Infrastruktur & Konfiguration

- [ ] **Nessie Catalog – OIDC Konfiguration**
  - **Problem**: Nessie versucht sich mit OIDC zu verbinden, aber ist noch nicht konfiguriert
  - **Log-Fehler**: `WARN OIDC Server is not available:: Connection refused: /127.255.0.0:0`
  - **Umsetzung**: OIDC mit Keycloak in `docker-compose.yml` Nessie-Service aktivieren
    - `QUARKUS_OIDC_PROVIDER_NAME` setzen
    - `QUARKUS_OIDC_CLIENT_ID` und `QUARKUS_OIDC_CLIENT_SECRET` aus Keycloak
    - `KEYCLOAK_URL` referenzieren
  - **Validierung**: Nessie-Logs sollten keine OIDC-Warnings mehr enthalten
  - [ ] API → Airflow DAG: Open-Meteo als Upstream-Quelle im Katalog sichtbar
  - [ ] Airflow DAG → `iceberg.raw.weather_hourly`: OpenLineage-Event empfangen + Dataset verlinkt
  - [ ] `iceberg.raw` → `stg_weather`: dbt-Lineage aus manifest.json importiert
  - [ ] `stg_weather` → `h_location` / `s_weather_hourly`: Intermediate-Modelle in Lineage-Graph
  - [ ] Data Vault → `fact_weather_hourly`: business_vault-Modelle durchgehend verknüpft
  - [ ] `fact_weather_hourly` → `weather_trends`: Mart als Endpunkt im Lineage-Graph
  - [ ] Mart → Dremio: Dremio-Connector liest Mart-Tabelle, erscheint als Downstream
  - [ ] Dremio → Cognos Data Module: Bridge-Skript läuft, Data Module erscheint als letztes Glied im Lineage-Graph
  - [ ] Testfall: Spalte in `iceberg.raw` umbenennen → Impact-Analyse im Katalog zeigt alle betroffenen Downstream-Modelle inkl. Cognos Data Module

- [ ] **Cognos Data Module → Katalog Bridge** *(letztes Lineage-Stück, kein nativer Connector)*
  - Cognos emittiert keine OpenLineage-Events → Bridge-Skript nötig (~150 Zeilen Python)
  - Skript liest Data Module JSON per Cognos REST API: `GET /api/v1/datamodules/{id}`
  - Extrahiert und mapped:
    - `tableSet` + `columnList` → Dataset + Schema im Katalog (inkl. Labels, Datentypen, `usage`-Rolle)
    - `relationshipSet` → Join-Beziehungen als Katalog-Metadaten
    - `hierarchySet` → Zeitdimension, Geografie etc. als Glossar-Terme oder Tags
    - `query.sourceList` → Lineage-Kante `Dremio-Tabelle → Cognos Data Module`
  - Ergebnis im Katalog: semantische Schicht (Labels, Rollen) + letzte Lineage-Kante sichtbar
  - Ausführung als Airflow DAG täglich (kein Event-Trigger möglich, da Cognos kein OpenLineage kennt)
  - **Wichtig**: Skript vor Katalog-Auswahl entwickeln – DataHub Python SDK und OpenMetadata REST API haben unterschiedliche Payload-Formate

- [ ] **dbt-Exposures definieren** *(unabhängig vom Katalog sinnvoll)*
  - Dashboards und nachgelagerte Systeme als `exposures:` in schema.yml dokumentieren
  - Macht Abhängigkeiten in `dbt docs` + Katalog sichtbar (Lineage bis zum Dashboard)

---

### Infrastruktur – Stack-Härtung & Observability

- [ ] **PostgreSQL 13 → 15+ upgraden**
  - Shared Postgres (`postgres:13`) für Airflow + Keycloak ist EOL (Nov 2025)
  - OM-DB nutzt bereits `postgres:15-alpine` → einheitlich auf 15 oder 16 heben
  - Änderung in `docker-compose.yml`: Image `postgres:13` → `postgres:16-alpine`
  - Nach Upgrade: `docker compose down -v` (Airflow DB wird per `airflow db migrate` neu erstellt)
  - Keycloak-DB prüfen: Flyway-Migration läuft beim Start automatisch
  - **Risiko**: Volume `postgres_data` ist nicht kompatibel zwischen Major-Versionen → `pg_dump` vorher!

- [ ] **Healthchecks für MinIO und Nessie ergänzen**
  - **MinIO** hat keinen Healthcheck → `minio-init` startet per `service_started` (Race Condition möglich)
    - MinIO bietet `/minio/health/live` Endpoint auf Port 9000
    - Healthcheck: `curl -sf http://localhost:9000/minio/health/live || exit 1`
    - Danach `minio-init` auf `condition: service_healthy` umstellen
  - **Nessie** hat keinen Healthcheck → `trino` startet ohne Garantie dass Catalog bereit ist
    - Nessie REST API: `GET /api/v2/config` liefert 200 wenn bereit
    - Healthcheck: `curl -sf http://localhost:19120/api/v2/config || exit 1`
    - Trino `depends_on.nessie` auf `condition: service_healthy` setzen

- [ ] **Monitoring: Prometheus + Grafana integrieren**
  - Ziel: Container-Metriken, Query-Laufzeiten, Airflow Task Duration, DAG-Erfolgsraten zentral sichtbar
  - **Prometheus** als Scraper (neuer Service in docker-compose.yml, Port 9090)
    - Scrape-Targets mit nativen Endpoints:
      - Trino: `/v1/info` und JMX-Exporter (Port 9090 intern, `jmx_exporter.yml` in `trino/etc/`)
      - Airflow: `AIRFLOW__METRICS__STATSD_ON=true` + StatsD-Exporter → Prometheus
      - OpenMetadata: `/api/v1/system/version` (Healthcheck), Dropwizard-Metriken auf Port 8586
      - MinIO: `/minio/v2/metrics/cluster` (Prometheus-Format nativ)
      - PostgreSQL: `postgres-exporter` Sidecar (Connections, Locks, Replication Lag)
    - cAdvisor oder Docker-Daemon Metrics für Container-Ressourcen (CPU, RAM, Network I/O)
  - **Grafana** als Dashboard-UI (neuer Service, Port 3000)
    - Provisioning: `grafana/provisioning/datasources/prometheus.yml` → automatische Prometheus-Anbindung
    - Community-Dashboards: Trino (#12345), Airflow (#11276), PostgreSQL (#9628)
  - Config-Dateien: `monitoring/prometheus.yml`, `monitoring/grafana/provisioning/`
  - Optionale Alerting-Rules: DAG-Failure, Container-Restart, Disk-Nutzung >80%

- [ ] **Log-Aggregation: Loki + Promtail (oder Docker Log Driver)**
  - **Problem**: Airflow-Logs liegen auf Disk (`./airflow/logs/`), alle anderen Services nur in Docker stdout – kein zentrales Debugging möglich
  - **Option A – Grafana Loki Stack** (empfohlen, passt zu Prometheus+Grafana):
    - `promtail` als Sidecar: liest Docker-Container-Logs via `/var/lib/docker/containers/`
    - `loki` als Log-Backend (neuer Service, Port 3100)
    - Grafana-Datasource `loki` hinzufügen → Logs + Metriken in einer UI
    - Airflow Task-Logs: Remote Logging auf S3 (MinIO) umstellen (`AIRFLOW__LOGGING__REMOTE_LOGGING=true`, `AIRFLOW__LOGGING__REMOTE_BASE_LOG_FOLDER=s3://airflow-logs/`)
      - Bucket `airflow-logs` wird bereits von `minio-init` angelegt
  - **Option B – Docker Compose Logging** (minimal):
    - `logging:` Block in docker-compose.yml mit `json-file` Driver + `max-size: 10m` + `max-file: 3`
    - Verhindert zumindest unkontrolliertes Log-Wachstum
  - Ziel: Alle Container-Logs zentral durchsuchbar, korrelierbar mit Metriken (gleicher Zeitraum in Grafana)

- [ ] **Alle Secrets in `.env` für Production absichern**
  - `KEYCLOAK_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, alle `CLIENT_SECRET_*`
  - `.env.example` mit `CHANGE_ME_*` Platzhaltern liegt als Vorlage bereit

- [ ] **Reverse Proxy + DNS für produktionsnahen Betrieb** *(Prio: niedrig)*
  - **Traefik** als Reverse Proxy: ein Einstiegspunkt (Port 443), automatisches TLS, Host-basiertes Routing
    - `minio.lakehouse.internal:443` → MinIO :9001
    - `airflow.lakehouse.internal:443` → Airflow :8081
    - `trino.lakehouse.internal:443` → Trino :8443
    - `keycloak.lakehouse.internal:443` → Keycloak :8082
  - **dnsmasq** (lokaler DNS): Wildcard `*.lakehouse.internal` → VM-IP (löst `/etc/hosts`-Requirement ab)
  - **Eigene CA** (cfssl/mkcert): Wildcard-Zertifikat `*.lakehouse.internal` → alle Services HTTPS
  - Eliminiert: manuelle `/etc/hosts`-Einträge, Split-DNS-Problem, Port-Nummern in URLs
  - Voraussetzung: Air-gapped Netzwerk oder eigener DNS-Server

- [ ] **Dremio Data Sources konfigurieren** *(Voraussetzung für alle Dremio-Tests)*
  - MinIO als S3-Source hinzufügen (Endpoint: `http://minio:9000`, Access/Secret Key aus `.env`)
  - Nessie Catalog als Quelle einbinden (Endpoint: `http://nessie:19120/api/v1`)

- [ ] **Dremio Funktionstests** *(nach Data Sources Konfiguration)*
  - [ ] Basis-Konnektivität: `SELECT 1` auf Nessie-Source erfolgreich
  - [ ] Iceberg-Tabellen lesbar: `SELECT * FROM nessie.raw.weather_hourly LIMIT 10`
  - [ ] Namespaces vollständig: `raw`, `data_vault`, `business_vault`, `marts` alle sichtbar
  - [ ] Dremio Reflection anlegen auf `iceberg.marts.weather_trends` (Aggregations-Reflection)
  - [ ] Reflection-Status prüfen: `REFRESH REFLECTION ...` + Status `DONE` in Reflections-UI
  - [ ] Abfrage mit Reflection-Nutzung verifizieren: Query Plan zeigt Reflection-Hit (kein Full-Scan)
  - [ ] Arrow Flight SQL testen: Verbindung per `flight-sql-client` oder DBeaver mit Arrow Flight Protokoll
  - [ ] Cross-Source-Abfrage: Join Iceberg-Tabelle (Nessie) × PostgreSQL-Tabelle (falls Source konfiguriert)
  - [ ] Performance-Vergleich dokumentieren: dieselbe Abfrage mit/ohne Reflection (Laufzeit notieren)

---

## Erledigt

| Datum | Aufgabe |
|-------|---------|
| 18.03 | Docker Compose Stack (7 Services) lauffähig |
| 18.03 | Keycloak OIDC für MinIO, Trino, Airflow |
| 18.03 | Keycloak Realm Auto-Import (`--import-realm`) |
| 18.03 | Keycloak Healthcheck (Port 9000, Keycloak 26.x) |
| 18.03 | `.env.example`, `scripts/health_check.sh`, README.md |
| 18.03 | Trino HTTPS (Port 8443), Self-Signed Keystore |
| 18.03 | Airflow Custom Dockerfile (`authlib` für OIDC) |
| 19.03 | `minio-init` Container – Bucket `lakehouse` + Landing-Prefixes automatisch |
| 19.03 | `trino-init` Container – Iceberg Namespaces via Trino REST API |
| 19.03 | Iceberg Catalog Properties korrigiert (Trino 479 + Nessie + MinIO native-s3) |
| 19.03 | dbt-Verzeichnis gemountet (`./dbt:/opt/dbt`) |
| 19.03 | `dbt_run_lakehouse_ki` Airflow DAG erstellt |
| 19.03 | `dbt_project.yml`, `profiles.yml`, `packages.yml` (automate-dv) |
| 19.03 | Namespaces `raw`, `data_vault`, `business_vault`, `marts` in Trino erstellt |
| 19.03 | README.md: Layer-Architektur mit Diagramm und Beispielen dokumentiert |
| 19.03 | Dummy-Modelle entfernt (Ordnerstruktur mit `.gitkeep` erhalten) |
| 19.03 | **Airflow 2.8.4 → 3.1.8 Migration** – Base Image, Provider-Upgrades (trino 6.5.0) |
| 19.03 | **DAG Airflow 3.x Migration** – `schedule_interval` → `schedule`, Operator-Imports aktualisiert |
| 19.03 | `dbt_run_lakehouse_ki.py` – BashOperator aus `airflow.providers.standard.operators.bash` |
| 19.03 | `open_meteo_to_raw.py` – PythonOperator-Import, TrinoOperator entfernt (nicht in 6.5.0) |
| 20.03 | **Java JRE + JDBC-Treiber** – OpenJDK 17, ojdbc11.jar (Oracle), db2jcc.jar (IBM DB2) |
| 20.03 | `postgres_public_query.py` – DAG gegen RNAcentral öffentliche PostgreSQL-DB (EBI) |
| 20.03 | `oracle_jdbc_query.py` – DAG-Template für Oracle via JDBC (pausiert bis Connection gesetzt) |
| 20.03 | `db2_jdbc_query.py` – DAG-Template für IBM DB2 via JDBC (pausiert bis Connection gesetzt) |
| 23.03 | **Airflow Keycloak SSO** – FAB Auth Manager aktiviert, redirect_uri fix, Default-Rolle Admin |
| 23.03 | **Airflow `trino_default` Connection** erstellt (trino://airflow@trino:8080, catalog=iceberg, schema=raw) |
| 23.03 | **DAG `open_meteo_to_raw`** erfolgreich – 12.552 stündliche Wetterdaten (2020–2026) geladen |
| 23.03 | **dbt `generate_schema_name` Macro** – Schema-Namen ohne `raw_`-Prefix (data_vault, business_vault, marts) |
| 23.03 | **dbt Deduplizierung** – h_location + s_location_details: SELECT DISTINCT → GROUP BY + MIN(_loaded_at) |
| 23.03 | **dbt run --full-refresh** – 9 Models PASS, 81/81 Tests PASS |
| 23.03 | **dbt-Metadaten in OpenMetadata** – Beschreibungen, Tags, Lineage (6 Nodes, 25 Edges) sichtbar |
| 23.03 | **OM Ingestion-Pipelines** alle 3 getriggert und erfolgreich (Trino, Airflow, dbt) |
| 23.03 | **EXTERNAL_HOST** – Remote-Zugriff via `start.sh` + `update-keycloak-redirects.sh` |

---

## Entfällt

- **Dremio OIDC**: OSS-Version unterstützt kein OIDC/OAuth2 (nur Dremio Enterprise/Cloud)
