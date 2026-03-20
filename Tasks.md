# Tasks - Lakehouse KI

## Offen

### Datenquelle 1: Open-Meteo – nächste Schritte
- [ ] **Stack neu bauen** – `docker compose up -d --build` (wegen `boto3` im Dockerfile)
- [ ] **Airflow Variables setzen** (Airflow UI → Admin → Variables):
  - `WEATHER_LATITUDE` (z.B. `52.59`)
  - `WEATHER_LONGITUDE` (z.B. `13.35`)
  - `WEATHER_LOCATION_KEY` (z.B. `berlin-reinickendorf`)
  - `MINIO_ENDPOINT` → `http://minio:9000`
  - `MINIO_ACCESS_KEY` → `minioadmin`
  - `MINIO_SECRET_KEY` → `minioadmin123`
- [ ] **DAG `open_meteo_to_raw` triggern** – Backfill ab 2020-01-01 (catchup=True)
- [ ] **dbt-Pakete installieren** – `docker compose exec airflow bash -c "cd /opt/dbt && dbt deps --profiles-dir /opt/dbt"`
- [ ] **dbt-Modelle ausführen** – `dbt run` nach erfolgreichem DAG-Lauf

### Datenquellen (geplant, noch nicht begonnen)
- [ ] **OpenAQ Luftqualität** (`https://api.openaq.org`) – PM2.5, NO2, CO für Messstationen weltweit
- [ ] **CoinGecko Krypto-Kurse** (`https://api.coingecko.com`) – stündliche/tägliche Preise

### Use Cases (niedrige Priorität)
- [ ] **Hypothetischer Stromtarif-Vergleich** *(Prio: niedrig)*
  - Quelle: Energy-Charts API (`https://api.energy-charts.info/price?bzn=DE-LU`) – kein Key, historisch bis ~2015
  - Stündliche Day-Ahead-Spotpreise (€/MWh) für DE-LU rückwirkend laden (Backfill)
  - Join mit eigenem Smarthome-Stromverbrauch (stündlich)
  - Star-Schema: `dim_time`, `dim_tariff` (fix vs. dynamisch), `fact_consumption_cost`
  - Mart: `tariff_comparison` – hypothetische Kosten fix vs. dynamisch im Vergleich

---

### Metadatenmanagement
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
- [ ] **dbt-Tests nach erstem `dbt run` ausführen**
  - `dbt test` führt alle schema.yml-Tests (not_null, unique, relationships, accepted_values) aus
  - `dbt test --select test_type:singular` führt nur die custom Tests in `tests/` aus
  - Erwartung beim ersten Lauf: completeness-Test schlägt für den aktuellen Tag fehl (< 24h)

- [ ] **dbt-Tests in Airflow-DAG integrieren**
  - Nach `dbt run` automatisch `dbt test` ausführen
  - Bei Testfehler: DAG-Task auf `failed` setzen (nicht stillschweigend ignorieren)
  - Bestehender DAG `dbt_run_lakehouse_ki.py` anpassen

- [ ] **Airflow-DAG-Tests: Idempotenz prüfen**
  - DAG `open_meteo_to_raw` manuell zweimal für denselben Tag triggern
  - Erwartung: kein doppelter Eintrag in `iceberg.raw.weather_hourly` (DELETE + INSERT ist idempotent)
  - Prüfabfrage: `SELECT date_key, count(*) FROM iceberg.raw.weather_hourly GROUP BY date_key HAVING count(*) > 24`

- [ ] **Weitere custom dbt-Tests ergänzen** *(nach erstem erfolgreichen Lauf)*
  - `fact_weather_daily`: precipitation_sum >= 0, temperature_min <= temperature_max
  - `dim_date`: date_id eindeutig und lückenlos zwischen 20200101 und 20301231
  - `s_weather_hourly`: keine Dopplungen (location_hk + measured_at eindeutig)

---

### Data Governance Katalog
- [ ] **Tool-Evaluation: DataHub vs. OpenMetadata** *(Prio: mittel)*
  - DataHub: LinkedIn-Herkunft, große Community, ausgereifte Lineage-Graph-UI, aktives Ökosystem
  - OpenMetadata: Jüngeres Projekt, modernere UI, einfacheres Deployment, integrierte Data Quality-UI
  - **Must-have Kriterien** (K.O.-Ausschluss wenn nicht erfüllt):
    - [ ] OpenLineage-Receiver: nimmt Events von Airflow + dbt entgegen (REST-Endpunkt)
    - [ ] Trino-Connector: crawlt Iceberg-Tabellen inkl. Spaltenmetadaten
    - [ ] dbt-Integration: importiert `manifest.json` + `catalog.json` (Modellbeschreibungen + Lineage)
    - [ ] Airflow-Connector: Job-Lineage (DAG → Dataset-Abhängigkeiten)
  - **Nice-to-have Kriterien**:
    - Dremio-Connector (VDS-Metadaten)
    - Integrierte Data Quality Checks
    - OIDC-Authentifizierung (Keycloak-kompatibel)
  - Empfehlung erst nach Demo-Setup beider Tools

- [ ] **Gewählten Katalog in Docker Compose integrieren**
  - Als eigener Service im Stack (Port separat reservieren, `.env` ergänzen)
  - OpenLineage-Receiver-Endpunkt aktivieren (Airflow + dbt senden Events hierhin)
  - Connector zu Trino: Schema-Crawling `iceberg.*` (Tabellen + Spalten + Datentypen)
  - Connector zu dbt: `manifest.json` + `catalog.json` aus `dbt/target/` importieren
  - Connector zu Airflow: DAG-Metadaten + Job-Lineage
  - Connector zu Dremio: VDS-Metadaten *(nachgelagert, wenn VDS genutzt werden)*

- [ ] **End-to-End Lineage validieren** *(lückenlose Kette sicherstellen)*
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

### Infrastruktur
- [ ] **Alle Secrets in `.env` für Production absichern**
  - `KEYCLOAK_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, alle `CLIENT_SECRET_*`
  - `.env.example` mit `CHANGE_ME_*` Platzhaltern liegt als Vorlage bereit

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

---

## Entfällt

- **Dremio OIDC**: OSS-Version unterstützt kein OIDC/OAuth2 (nur Dremio Enterprise/Cloud)
