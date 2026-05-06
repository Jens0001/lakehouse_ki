# 💾 Memory - Lakehouse KI

Notizen, Erkenntnisse und wichtige Informationen, die während der Arbeit am Lakehouse KI Projekt gesammelt wurden.

---

## Cognos REST API – Wichtige Eigenheiten (06.05.2026)

Erkenntnisse aus der direkten API-Analyse für `cognos_api_sync.py`. Relevant für alle
zukünftigen Cognos-API-Integrationen.

### Response-Struktur
- Content-Endpunkte (`GET /api/v1/content`, `/content/{id}/items`) geben Objekte unter
  dem Key **`"content"`** zurück – nicht `"entries"` (wie ältere Dokumentation vermuten lässt)
- 404-Responses sind normal für nicht gefundene Objekte – kein Fehler, gibt `None` zurück

### Dashboard-Spezifikation (exploration-Typ)
- Moderne interaktive Cognos-Dashboards haben den Objekttyp **`"exploration"`**
  (nicht `"dashboard"`)
- Spezifikation abrufen: `GET /content/{id}?fields=specification`
  (nicht `?extensions=specification` und nicht `?details=specification`)
- Das `specification`-Feld in der Antwort ist ein **JSON-String** → muss mit `json.loads()`
  geparst werden, bevor man darauf zugreift
- Die geparste Spec enthält direkt `name`, `layout`, `dataSources` – keine weitere Verschachtelung

### XSRF-Token (Sicherheitsschicht)
- Cognos schützt alle API-Endpunkte (auch lesende wie `/items`) mit einem XSRF-Token
- Ohne Token: **403 Forbidden** mit Header `X-BI-XSRF: Rejected`
- Token wird beim Session-Aufbau als Cookie `XSRF-TOKEN` geliefert
- Muss bei jedem Request als Header **`X-XSRF-Token: <wert>`** mitgesendet werden
- Token aus dem Session-Response-Cookie extrahieren (nicht aus Datei lesen)

### Datenmodul-Struktur (useSpec)
- `useSpec[].dataSourceOverride` existiert in unserem Setup **nicht**
- Datenquellenname steht in `useSpec[].ancestors[0].defaultName`
  (z.B. `"Lakehouse KI Trino"`)
- Catalog und Schema aus `useSpec[].searchPath` per Regex extrahieren:
  `dataSourceSchema[@name='iceberg/marts']` → catalog=`iceberg`, schema=`marts`
- Query Subjects referenzieren ihren useSpec über `ref: ["M1.tabellen_name"]`:
  - `"M1"` = useSpec-Identifier
  - `"tabellen_name"` = physischer Tabellenname in Trino (für Lineage)

### Session-Aufbau
- Anonyme Session: `GET /api/v1/session` – funktioniert für öffentlich freigegebene Inhalte
- Authentifiziert: zuerst `POST /api/v1/session`, dann `GET /api/v1/session/login` mit
  `Authorization: Basic <base64(user:pass)>` Header
- `canCallLogon: false` in der Session-Antwort bedeutet: anonymer Zugriff ausreichend

### Getestete Konfiguration
- Cognos Analytics URL: `http://192.168.178.149:9300/api/v1`
- Anonymer Zugriff auf Teamordner → Lakehouse → Modelle und Dashboards funktioniert
- Gefundene Objekte: 1 Datenmodul (`Lakehouse_KI`, 16 Query Subjects), 2 Dashboards
  (`Temperatur_Durchschniitt`, `Strompreis_Durchschniitt`, beide Typ `exploration`)

---

## Keycloak OIDC Issuer-URL: KEYCLOAK_HOSTNAME muss in .env persistiert werden (04.05.2026)

- **Problem**: Zugriff auf Keycloak über externe IP (z.B. `http://192.168.178.81:8082`) führte
  zu automatischem Redirect auf `keycloak:8082` → Browser-Fehler.
- **Ursache**: `KEYCLOAK_HOSTNAME` stand nicht in `.env`. `start.sh` setzte es per `export`,
  aber Docker Compose liest Environment-Variablen primär aus `.env`. Der Fallback
  `${KEYCLOAK_HOSTNAME:-keycloak}` in `docker-compose.yml:206` wurde aktiv.
- **Folge**: Keycloak `KC_HOSTNAME=keycloak` → OIDC-Discovery-Endpoint gab
  `http://keycloak:8082/realms/lakehouse` zurück.
- **Fix**: `start.sh` schreibt `KEYCLOAK_HOSTNAME` nun automatisch aus `EXTERNAL_HOST` in `.env`
  (analog zu `OM_URL`). `.env.example` enthält nun auch `KEYCLOAK_HOSTNAME=${EXTERNAL_HOST}`.
- **Betroffene Dateien**: `start.sh`, `.env.example`, `docker-compose.yml` (Zeile 206)
- **Wichtig**: Nach diesem Fix muss der Keycloak-Volume gelöscht werden, da der Realm-Import
  nur beim ersten Start erfolgt: `docker volume rm lakehouse_ki_keycloak_data && ./start.sh`

---

## OpenMetadata Elasticsearch cgroupv2 Bug (04.05.2026)

- **Problem**: Elasticsearch 7.16.3 stürzt beim Start mit `NullPointerException` ab unter Linux mit cgroupv2
- **Stack Trace**: `Cannot invoke "jdk.internal.platform.CgroupInfo.getMountPoint()" because "anyController" is null`
- **Ursache**: Der `JvmOptionsParser` im ES-Image (aufgerufen als separater Java-Prozess) liest
  cgroup-Speichermetriken. Unter Ubuntu 25.10 mit Kernel 6.17 und cgroupv2
  (`nsdelegate,memory_recursiveprot`) ist der Controller nicht im erwarteten Format verfügbar.
- **Fix-Versuch 1 (fehlgeschlagen)**: `-XX:-UseCGroupMemoryMetricForLimits` →
  `Unrecognized VM option` – existiert erst ab Java 17, ES 7.16.3 verwendet Java 11
- **Fix-Versuch 2 (fehlgeschlagen)**: `ES_JAVA_OPTS` leer + `jvm.options` gemountet →
  wirkungslos, da der `JvmOptionsParser` crasht BEVOR die jvm.options Datei gelesen wird.
  Der Parser wird als separater Java-Prozess aufgerufen:
  `"$JAVA" -cp "$ES_CLASSPATH" org.elasticsearch.tools.launchers.JvmOptionsParser ...`
- **Endgültiger Fix**: Wrapper-Script `elasticsearch/elasticsearch-wrapper.sh` ersetzt das
  originale `elasticsearch` Binary. Der Wrapper ruft den JvmOptionsParser mit
  `-Des.cgroups.hierarchy.override=/` auf – das setzt die cgroup-Hierarchie manuell
  und verhindert den NullPointerException.
- **Technische Details**:
  - Das System-Property `es.cgroups.hierarchy.override` wird vom ES-cgroup-Reader verwendet
  - Der Wert `/` zeigt auf die root-cgroup, die immer existiert
  - Der Wrapper source't `elasticsearch-env` für JAVA/XSHARE/ES_CLASSPATH
  - docker-compose.yml: `command: ["bash", "/docker-entrypoint.sh", "eswrapper"]`
- **Betroffene Services**: `openmetadata-es` (Elasticsearch 7.16.3)
- **Host-System**: Ubuntu 25.10, Kernel 6.17.0-7-generic, Docker cgroupv2, systemd Driver
- **Dokumentation**: Siehe auch Changelog.md und Tasks.md

---

## PyIceberg / Nessie REST Catalog (04.05.2026)

- **Nessie Core API** (Trino): `http://nessie:19120/api/v2` → konfiguriert in `trino/etc/catalog/iceberg.properties`
- **Nessie Iceberg REST API** (PyIceberg): `http://nessie:19120/iceberg` → Airflow Variable `NESSIE_URI`
  Beide Endpunkte zeigen auf dieselbe Nessie-Instanz, unterscheiden sich nur im Pfad-Präfix.
- PyIceberg-Catalog-Config: `type=rest`, `py-io-impl=pyiceberg.io.pyarrow.PyArrowFileIO`,
  `s3.path-style-access=true`, MinIO-Credentials aus den Standard-Airflow-Variablen
- Idempotentes Schreiben: `table.overwrite(arrow_table, overwrite_filter=EqualTo("date_key", day_date))`
  löscht bestehende Dateien für genau diesen Partition-Wert und schreibt neu.
- `pyiceberg[pyarrow,s3fs]` Extras erforderlich (nicht plain `pyiceberg`); ohne `[pyarrow]` schlägt
  `PyArrowFileIO`-Import fehl.

---

## 🛠️ Gelöste Probleme

### Spotify-Pipeline: Kaggle-Artist-Name vs. Spotify-Artist-ID (24.03.2026)

**Problem**: Das Kaggle-Tracks-Dataset enthält keine `artist_id` – nur `artist_name` als Text. Die Spotify API liefert dagegen eine zuverlässige `artist_id`. Beide Quellen speisen den Hub `h_artist`, aber mit unterschiedlichen Business Keys.

**Lösung**:
- `h_artist` verwendet UNION ALL aus beiden Quellen
- Kaggle-artist_hk basiert auf `lower(trim(artist_name))`, API-artist_hk auf `artist_id`
- Bei gleichem artist_hk wird der API-BK bevorzugt (`COALESCE(api_bk, fallback_bk)`)
- Chart-Daten (aus Kaggle) werden mit degenerierten Dimensionen (track_name, artist_name als Text) statt FK-Joins geführt, da kein zuverlässiges Matching möglich ist

**Merke**: Bei Datenquellen ohne gemeinsamen Business Key lieber degenerierte Dimensionen verwenden als unzuverlässige Fuzzy-Matches.

### Spotify-Pipeline: SCD2 über Satellite-Hashdiff (24.03.2026)

**Konzept**: SCD Type 2 wird NICHT im Satellite selbst implementiert, sondern im Business-Vault-Modell `dim_artist`:
1. `s_artist_profile` speichert append-only Snapshots mit `artist_profile_hashdiff`
2. Gleicher Hashdiff = keine Änderung → Row wird beim inkrementellen Load übersprungen
3. Neuer Hashdiff = Änderung an popularity/followers/genres → neue Satellite-Row
4. `dim_artist` berechnet per `LEAD(load_date) OVER(...)` die `valid_to`-Timestamps
5. `is_current = (valid_to IS NULL)` markiert die aktuelle Version

**Merke**: Data Vault trennt sauber zwischen Historisierung (Satellite, append-only) und SCD2-Logik (Business Vault, Full Refresh). Der Satellite ist nur die "Datenbasis", die Dimension berechnet Gültigkeitszeiträume.

### dbt SELECT DISTINCT vs. GROUP BY bei mehrfach geladenen Rohdaten

**Problem**: Data-Vault-Hubs und -Satellites hatten Duplikate trotz `SELECT DISTINCT`, weil `_loaded_at` pro Ladevorgang unterschiedlich war.

**Symptom**: `unique`-Tests auf `location_hk`, `location_bk`, `location_hashdiff` schlugen fehl. Downstream-Fact-Tabelle hatte 112× multiplizierte Zeilen durch JOIN-Explosion.

**Lösung**: `SELECT DISTINCT ... _loaded_at AS load_date` → `GROUP BY ... MIN(_loaded_at) AS load_date`. So wird pro Business-Key / Hashdiff genau eine Zeile erzeugt.

**Betroffene Dateien**: `dbt/models/data_vault/hubs/h_location.sql`, `dbt/models/data_vault/satellites/s_location_details.sql`

**Merke**: Bei Data-Vault-Loads mit mehrfachen Ladevorgängen immer `GROUP BY` statt `SELECT DISTINCT` verwenden, wenn technische Felder wie `_loaded_at` enthalten sind.

### OIDC/OAuth2 Remote-Zugriff – Split-DNS-Problem & Lösung (30.03.2026)

**Problem**: Bei Zugriff auf den Stack von einem anderen Rechner (z.B. VM im LAN) scheitert OIDC/SSO wegen des klassischen **Split-DNS-Problems**:
1. Browser-Redirects (MinIO, Airflow, Trino) auf `localhost` zeigen → Client-Browser landet auf eigenem Rechner
2. Keycloak OIDC-Discovery gibt Issuer-URL mit `keycloak` Hostname zurück → Browser kann `keycloak` nicht auflösen
3. Trino validiert Token-Issuer: Token-Claims sagen `iss=http://<IP>:8082/...`, aber Trino erwartet `iss=http://keycloak:8082/...` → Token-Validierung scheitert

**Root Cause**: Docker-interne Hostnamen (`keycloak`, `minio`) sind außerhalb des Docker-Netzwerks nicht auflösbar. Keycloak setzt `KC_HOSTNAME` als Issuer im Token. Im Container sieht das Token `keycloak:8082`, draußen sieht der Browser `localhost:8082` → Mismatch.

**Implementierte Lösung (EXTERNAL_HOST-Pattern)**:

1. **`start.sh` Automation**:
   - Erkennt Netzwerk-IP automatisch (Linux: `hostname -I`, macOS: `ipconfig getifaddr`)
   - Setzt `KEYCLOAK_HOSTNAME`:
     - Bei `localhost`: `keycloak` (Docker-intern, auch für Container)
     - Bei IP: `${EXTERNAL_HOST}` (damit Keycloak den Issuer mit IP setzt)
   - Setzt `KEYCLOAK_URL`:
     - Bei `localhost`: `http://keycloak:8082` (Docker-Netzwerk)
     - Bei IP: `http://${EXTERNAL_HOST}:8082` (für Airflow außerhalb Docker, fällt aber in Container trotzdem auf keycloak:8082 ab)

2. **`update-trino-config.sh`** vor Container-Start:
   - Ersetzt in `trino/etc/config.properties`: `http://keycloak:8082` → `http://${EXTERNAL_HOST}:8082`
   - Trino erkennt dann den Issuer mit externer IP → Token-Validierung passt

3. **Keycloak Client-URLs aktualisieren** (nach Keycloak-Start):
   - `update-keycloak-redirects.sh` setzt für alle Clients:
     - **Redirect URIs**: `http://<IP>:<port>/oauth_callback` + localhost-Fallback
     - **Web Origins**: `http://<IP>:<port>` (Browser CORS)
     - **Root URL**: `http://<IP>:<port>/` (für relative Links)
     - **Admin URL**: `http://<IP>:<port>/` (für Token-Validierung)

4. **Client-seitiger `/etc/hosts`-Eintrag** (immer noch nötig):
   - `<VM-IP> keycloak` auf jedem Client-Rechner
   - Browser kann jetzt `keycloak` auflösen (zeigt auf VM-IP)
   - Keycloak ist unter `http://keycloak:8082` (im Browser) erreichbar

**Warum nicht einfach `KC_HOSTNAME=${EXTERNAL_HOST}`?**: Wenn Keycloak den Issuer mit IP setzt (z.B. `iss=http://192.168.1.50:8082/...`), aber im Container noch `http://keycloak:8082` hardcodiert steht, entstehen zwei verschiedene URLs für den gleichen Server → Token-Mismatch. Mit dem aktuellen Ansatz:
- Container sehen Keycloak intern unter `http://keycloak:8082/...`
- Browser sehen Keycloak extern unter `http://<IP>:8082/...` (via `/etc/hosts` Auflösung zu `keycloak`)
- Der Issuer im Token wird mit der externen IP gesetzt
- Alle Services (Trino, Airflow, MinIO) validieren gegen die externe IP

**Langfristige Lösung** (nicht implementiert): Traefik Reverse Proxy + dnsmasq + DNS-Split-View → ein DNS-Name (`keycloak.lakehouse.local`) der sowohl von Docker als auch vom Browser identisch aufgelöst wird. Würde das `/etc/hosts`-Requirement eliminieren.

**Keycloak Client-Secrets**:
- Werden bei jedem Start überprüft (Setup-Skript)
- Falls Placeholder (`CHANGE_ME_*`, `TODO_*`, `<20 Zeichen`), neue Secrets generieren
- Secrets in `.env` speichern (für wiederholte Starts) + in Keycloak Admin API injizieren
- Clients: `minio`, `airflow`, `trino` (separate Secrets)

### dbt generate_schema_name Macro

**Problem**: dbt-Default-Verhalten hängt den Target-Schema als Prefix an Custom-Schemas an (`raw` + `data_vault` → `raw_data_vault`).

**Lösung**: Custom-Macro `dbt/macros/generate_schema_name.sql` – gibt bei gesetztem `+schema` nur den Custom-Schema-Namen zurück, ohne Prefix.

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

### ADR-008: Aggregation von Energie-Preis-Daten auf Monatsebene

**Entscheidung**: Erstellung eines neuen Modells `fact_energy_price_monthly` zur Aggregation stündlicher Energiepreisdaten auf Monatsebene

**Gründe**:
- ✅ Erweiterung der vorhandenen Energie-Preis-Modelle (`fact_energy_price_daily`)
- ✅ Aggregation der stündlichen Daten zu monatlichen Aggregaten (Min, Max, Durchschnitt, Standardabweichung)
- ✅ Konsistente Struktur mit bestehenden Modellen
- ✅ Wiederverwendung bestehender Datenquellen und Transformationslogik
- ✅ Unterstützung für monatliche Analysen und Reporting

**Implementierungsdetails**:
- Modell basiert auf `fact_energy_price_daily` 
- Aggregation auf Monatsebene (per `date_key` und `price_zone_hk`)
- Enthält die gleichen Metriken wie `fact_energy_price_daily` (min_price, max_price, avg_price, stddev_price)
- Wird in `business_vault` abgelegt
- Verwendung der bestehenden `dim_date` Dimension für Datumsinformationen

**Status**: ✅ Implementiert (24.03.2026)

### Änderung: `fact_energy_price_monthly` Modell-Struktur

**Implementierungsdetails**:
- Modell basiert auf `fact_energy_price_hourly` 
- Aggregation auf Monatsebene (per `date_key` und `price_zone_hk`)
- Enthält die gleichen Metriken wie `fact_energy_price_daily` (min_price, max_price, avg_price, stddev_price)
- Wird in `business_vault` abgelegt
- Verwendung der bestehenden `dim_date` Dimension für Datumsinformationen
- Inkrementelle Lade-Logik implementiert mit `is_incremental()`-Condition
- Verwendung von `stddev_pop()` für Population-Standardabweichung statt `stddev_samp()`

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

### ADR-007: Hash-basierte Surrogate Keys für Dimensionen

**Entscheidung**: Jede Dimension im Business Vault erhält einen Hash-basierten Surrogate Key: `SK = md5(hub_hash_key || '|' || valid_from)`

**Gründe**:
- ✅ Deterministisch, idempotent (Re-runs erzeugen identische Keys)
- ✅ Keine Sequenzen nötig (Trino/Iceberg hat keine SERIAL/IDENTITY)
- ✅ SCD2-fähig: neues valid_from → neuer SK pro Dimensionsversion
- ✅ Architektonisch korrekt nach Kimball (Surrogate Key in Fakt zeigt auf eindeutige Dimensionsversion)
- ✅ Pipe-Separator verhindert Hash-Kollisionen

**Hub Hash Key bleibt erhalten**: In Dimensionen und Fakten als Referenz zum Data Vault Hub (Debugging, Lineage).

**Konvention für alle zukünftigen Dimensionen**: Immer `<entity>_sk = generate_dimension_sk(['<entity>_hk', 'valid_from'])` als PK. FK in Fakten = SK. HK bleibt als Referenz. Macro: `dbt/macros/generate_dimension_sk.sql`.

**dim_date / dim_time**: Nicht betroffen (haben bereits eigene Integer-PKs).

**Trino-Besonderheit**: `md5()` erwartet `varbinary`, nicht `varchar`. Korrektes Muster im Macro: `lower(to_hex(md5(to_utf8(expr))))` – `to_utf8()` für Input, `to_hex()` für Output.

**Status**: ✅ Implementiert (24.03.2026) – dbt run + dbt test: PASS=15/15 Modelle, PASS=130/130 Tests

---

## 🔧 Technische Erkenntnisse

### Informationsanlieferung an OpenMetadata (17.04.2026)

Die Anlieferung von Metadaten erfolgt über zwei komplementäre Wege:

1. **Pull-basierte Ingestion (Technische Metadaten)**:
   - Ein dedizierter `openmetadata-ingestion` Container (separater Airflow) führt Pipelines aus.
   - **dbt-Connector**: Liest `manifest.json` und `catalog.json` aus `./dbt/target`. Damit werden Beschreibungen, Tags und die statische Lineage (Modell-Abhängigkeiten) importiert.
   - **Trino/Airflow-Connectors**: Crawlen Schemata, Tabellen und DAG-Strukturen direkt über DB-Connections oder APIs.

2. **Push-basierte Lineage (Operative Runtime-Lineage)**:
    - Über das **OpenLineage-Protokoll** senden die Runtime-Systeme (Airflow, dbt) Events an den OM-Server (`/api/v1/lineage`).
    - **Voraussetzung**: `OPENLINEAGE_URL` muss im Airflow-Environment gesetzt sein.
    - **Problem bei PythonOperators**: Diese können keine Tabellen automatisch erkennen. Hier müssen `inlets` (Inputs) und `outlets` (Outputs) explizit im Operator definiert werden, damit die Kanten in der Lineage-Grafik erscheinen.

3. **Semantische Metadaten (Business Glossary)**:
    - Während technische Metadaten automatisch gecrawlt werden, erfordert das Business-Glossar eine kuratierte Struktur.
    - **Ansatz**: Definition der Begriffe und Hierarchien in einer `glossary_structure.json`. Ein Python-Skript (`om_glossary_ingest.py`) importiert diese Struktur via API in OpenMetadata.
    - **Automatisierung**: Die `OM_URL` wird in `start.sh` automatisch an den `EXTERNAL_HOST` angepasst. Der `OM_TOKEN` muss einmalig manuell aus der OM-UI hinterlegt werden, um die API-Authentifizierung zu ermöglichen.


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

### Keycloak Client-Konfiguration Pattern (30.03.2026)

**Vollständige Client-Setup-Checkliste** (alle 3 Komponenten notwendig!):

1. **Client Secrets** (in `.env` + Keycloak Admin Console):
   ```
   KEYCLOAK_CLIENT_SECRET_<APP>=<secret>
   ```
   - **Länge**: mindestens 24 Zeichen (weniger werden als "placeholder" erkannt)
   - **Generierung**: `openssl rand -base64 48` (48 Base64 = 36 Bytes Raw)
   - **Speicherung**: `.env` ODER `docker-compose.yml` environment, aber nicht beide!
   - **Best Practice**: In `.env` speichern, damit es nicht in `.yml` landet

2. **Redirect URIs** (Keycloak Client → Settings):
   ```
   http://localhost:8081/oauth-authorized/keycloak      # Airflow lokal
   http://<IP>:8081/oauth-authorized/keycloak            # Airflow remote
   http://localhost:9001/oauth_callback                   # MinIO lokal
   http://<IP>:9001/oauth_callback                        # MinIO remote
   ```
   - **Muss exakt** der Browser-URL entsprechen, auf die OAuth2 zurückleitet
   - **Exact Matching aktivieren** in Keycloak (standardmäßig an)
   - **Protokoll und Port** wichtig: `http://` vs `https://`, `8081` vs `443`
   - **Wildcard nicht sicher**: `http://localhost/*` verhindert genaue CSRF-Validierung

3. **Root URL + Admin URL** (nur für Browser/CORS relevant):
   ```
   Root URL: http://<IP>:8082/  (für relative Links in Keycloak UI)
   Admin URL: http://<IP>:8082/admin/  (für Token-Management-Flows)
   ```
   - Wird von einigen Clients ignoriert, aber wichtig für vollständiges Setup
   - **MinIO braucht Web Origins** zusätzlich: `http://localhost:9001`, `http://<IP>:9001`

4. **Token Endpoint Auth Method**:
   - **Server-to-Server** (Backend): `client_secret_basic` (Standard) oder `client_secret_post`
   - **Browser App** (SPA): `public` (kein Secret)
   - **Airflow (PyArrow/PySpark in Container)**: `client_secret_post` (explizit in webserver_config.py)

**Häufige Fehler**:
- ❌ "unauthorized_client": Redirect URI stimmt nicht exakt überein (Typo, falscher Port, fehlender Protokoll)
- ❌ "invalid_client_id": Client nicht im Realm, oder Secret nicht richtig gesetzt
- ❌ Token-Validierung schlägt fehl: Issuer URL passt nicht (Split-DNS-Problem)

---

### Docker Permissions & Named Volumes Pattern (30.03.2026)

**Problem**: Host-User (z.B. `einwagje`) erstellt bind-mount Verzeichnisse mit `755`, Docker-Container (User `airflow`, `dremio`) können nicht schreiben.

**Symptome**:
- Airflow: "Permission denied: '/opt/airflow/logs/dag_processor'"
- Dremio: "The path /opt/dremio/data is not writable by the current user"
- DAG Processor: Kann nicht in `/opt/airflow/logs` schreiben

**Lösungsmuster**:

1. **Für kritische Verzeichnisse: Named Volumes statt Bind-Mounts**
   ```yaml
   volumes:
     dremio_data:  # statt ./dremio/data

   services:
     dremio:
       volumes:
         - dremio_data:/opt/dremio/data  # Docker verwaltet Berechtigungen
   ```
   - ✅ Docker kümmert sich um Ownership (rootless oder daemon-User)
   - ✅ Container-User kann immer schreiben
   - ❌ Auf Host nicht direkt einsehbar (nur via `docker volume inspect`)

2. **Für Development-Verzeichnisse: Automatische Permission-Fixes**
   ```bash
   # In start.sh, BEVOR docker compose up:
   chmod -R 777 ./airflow/dags
   chmod -R 777 ./airflow/logs
   ```
   - ✅ Transparent für Developer
   - ✅ Funktioniert mit bind-mounts
   - ✅ Schneller Restart möglich

3. **Alternative (nicht empfohlen)**: User-Mapping in Container
   ```dockerfile
   # Dockerfile: Container-User mit Host-GID
   RUN groupadd -g 1000 airflow && \
       useradd -u 1000 -g airflow airflow
   ```
   - ❌ Kompliziert bei Entwicklung
   - ❌ Funktioniert nicht über Gast-VMs hinweg
   - ✅ Einzig saubere Lösung für Produktions-Container

**Entscheidung für Lakehouse KI**: Hybrid-Ansatz
- Named Volumes für `dremio_data` (produktiv-ähnlich)
- `chmod 777` für `./airflow/dags` + `./airflow/logs` (Development)
- Automatisiert in `start.sh` (transparent)

---

### Trino Authentication in Airflow (30.03.2026)

**Problem**: Trino ist mit Keycloak OIDC konfiguriert (für Browser/BI-Tools), aber TrinoHook in Airflow kann nicht mit OIDC authentifizieren.

**Lösung**: Username ohne Passwort in der Airflow Trino-Connection.
- Trino akzeptiert `login="trino_user"` (kein password-Feld) für Basic Auth
- Trino kennt `trino_user` als Default-User und akzeptiert die Authentifizierung
- Funktioniert sowohl lokal als auch in der VM

**Konfiguration in `scripts/airflow_init_connections.py`**:
```python
Connection(
    conn_id="trino_default",
    conn_type="trino",
    host="trino",
    login="trino_user",  # Username, kein password nötig
    port=8080,
    schema="default",
    extra={"catalog": "iceberg"}
)
```

**Production-Upgrade**: Für echte Umgebungen könnte man einen Service-Account in Keycloak anlegen oder Trino mit LDAP/AD konfigurieren.

---

### Airflow DAG Discovery & DAG Processor (30.03.2026)

**Airflow 3.x Besonderheit**: DAG-Processor ist **kein integrierter Subprocess** des Schedulers mehr, sondern ein eigenständiger Prozess (`airflow dag-processor`).

**Problem**: Ohne laufenden DAG-Processor:
- DAGs werden nie serialisiert → OpenMetadata kann keine Pipelines finden
- Airflow UI zeigt 0 DAGs
- DAG-Abfragen: `airflow dags list` gibt nichts zurück

**Lösung**:
- Separate Container für `airflow-dag-processor` (gleiche Volumes + Environment wie Scheduler)
- DAG-Folder explizit setzen: `AIRFLOW__CORE__DAGS_FOLDER=/opt/airflow/dags`
- DAG Folder muss lesbar + beschreibbar sein (für `.airflow_dag_parser.lock`)

**Debugging**:
```bash
# Im Airflow-Container prüfen
docker exec lakehouse_airflow airflow dags list
# Sollte alle DAGs zeigen

# DAG-Parser-Log prüfen
ls -la /opt/airflow/logs/dag_processor/
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

### OpenMetadata (OM) – Konfiguration & Integration

26. **Drei Services, dedizierte Infrastruktur**: OM benötigt eine eigene PostgreSQL-Instanz (`openmetadata-db`) und Elasticsearch (`openmetadata-es`). Die shared postgres (Port 5432) darf NICHT genutzt werden – OM führt eigenständige Flyway-Migrationen durch die mit Airflow/Keycloak kollidieren würden.

27. **Erster Login**: `admin@open-metadata.org` / `admin` (getestet mit AUTHENTICATION_PROVIDER=basic + PostgreSQL, 20.03.2026). In der OM-UI unter Settings → Users → Admin → Edit Password sofort ändern. Das in der OM-Doku genannte Default-Passwort `Admin@1234!` gilt NUR wenn der Server mit dem internen MySQL-Default-Setup startet – bei PostgreSQL mit basic-Auth ist es `admin`.

28. **OpenLineage-Konfiguration in Airflow 3.x** (korrigiert 04.05.2026):
    - **Richtiger Endpunkt**: `POST /api/v1/openlineage/lineage` (NICHT `/api/v1/lineage` – dieser akzeptiert nur PUT!)
    - **`AIRFLOW__OPENLINEAGE__TRANSPORT`** als einheitliche JSON-Konfiguration verwenden – nicht `OPENLINEAGE_URL` allein:
      ```
      AIRFLOW__OPENLINEAGE__TRANSPORT={"type":"http","url":"http://openmetadata-server:8585",
        "endpoint":"/api/v1/openlineage/lineage",
        "auth":{"type":"api_key","apiKey":"${TOKEN}","apiKeyPrefix":"Bearer"}}
      ```
    - **Warum nicht OPENLINEAGE_URL + OPENLINEAGE_ENDPOINT?**: In Airflow 3.x emittiert der
      Scheduler START-Events, der Task-Executor COMPLETE-Events – als separate Prozesse. `OPENLINEAGE_ENDPOINT`
      wurde nur vom Task-Executor gelesen. `AIRFLOW__OPENLINEAGE__TRANSPORT` gilt für alle Prozesse uniform.
    - **Authentifizierung**: OpenMetadata erfordert Bearer-Token. Ohne Token: 405 Method Not Allowed
      (auch wenn der Endpunkt korrekt ist).
    - **Token**: ingestion-bot JWT-Token, permanent (`JWTTokenExpiry: Unlimited`), wird automatisch
      von `start.sh` aus der OM-API geholt und in `.env` als `OPENMETADATA_INGESTION_BOT_TOKEN` gespeichert.
    - Provider installieren: `apache-airflow-providers-openlineage` im Airflow-Dockerfile
    - dbt-Adapter: `openlineage_url: http://openmetadata-server:8585` in profiles.yml (dbt nutzt eigenen Client)

36. **OpenMetadata Connector-Setup vollautomatisiert** (05.05.2026):
    - `scripts/om_setup_connectors.py` legt Trino-, Airflow- und dbt-Konnektoren idempotent an
    - Wird automatisch von `start.sh` nach dem Bot-Token-Abruf ausgeführt
    - Airflow-Connector: Direktverbindung zu `postgres:5432` (SQLAlchemy), NICHT REST-API
      - Env-Vars: `POSTGRES_USER` / `POSTGRES_PASSWORD` werden aus `.env` übergeben
    - Reihenfolge in start.sh: OM-Health → Bot-Token → Ingestion-Health → Connector-Setup
    - Manuelle Ausführung: `POSTGRES_USER=airflow POSTGRES_PASSWORD=airflow123 python3 scripts/om_setup_connectors.py`
    - `om_setup_schedules.py` bleibt für nachträgliche Schedule-Änderungen erhalten

29. **Connector-Konfiguration in der OM-UI** (Settings → Services):
    - **Trino**: Type=Trino, Host=trino, Port=8080, Username=admin (kein Passwort bei OIDC-Auth)
    - **dbt**: dbt Cloud oder lokale Dateien – `manifest.json` + `catalog.json` aus `dbt/target/`
    - **Airflow**: Type=Airflow, Host=http://airflow:8080, Auth=admin/admin
    - **Dremio**: Type=Dremio, Host=dremio, Port=9047 (nachgelagert)

30. **Elasticsearch Version**: OM 1.12.3 bündelt `elasticsearch-java 9.2.4` (shaded). Dieser Client sendet Vendor-Header `Content-Type: application/vnd.elasticsearch+json;compatible-with=8` ohne passenden `Accept`-Header. ES 8.x (alle 8.x Versionen!) lehnt das mit `media_type_header_exception` ab. **Lösung: ES 7.16.3 verwenden** – ES 7.x prüft Vendor-Header nicht. Ein Upgrade auf ES 8.x ist erst möglich, wenn OM den ES-Client-Bug behebt.

31. **CSRF in OM-Ingestion (Airflow 3.x)**: Die FAB Flask-App hat `CSRFProtect` global aktiviert. `AIRFLOW__WEBSERVER__WTF_CSRF_ENABLED=false` wirkt NICHT – die FAB Flask-App liest `webserver_config.py` (Pfad: `[fab] config_file` → `/opt/airflow/webserver_config.py`). Dort muss `WTF_CSRF_ENABLED = False` stehen. Datei: `airflow/webserver_config_ingestion.py`, gemountet als `:ro`.

32. **SERVER_HOST_API_URL**: Im OM-Server muss `SERVER_HOST_API_URL=http://openmetadata-server:8585/api` gesetzt sein. Ohne diese Variable generiert OM Ingestion-DAGs mit `localhost:8585`, was im Container nicht auflöst.

33. **SearchIndexingApplication**: Nach ES-Neustart oder Volume-Löschung muss `POST /api/v1/apps/trigger/SearchIndexingApplication` aufgerufen werden, um die ES-Indizes aus der DB neu aufzubauen. Alternativ in der OM-UI: Settings → Applications → Search Indexing → Run.

34. **Airflow Container-Konsolidierung**: 3 separate Container (api-server, scheduler, dag-processor) zu 1 Container konsolidiert. `dag-processor` auch im OM-Ingestion-Container gestartet (ohne ihn werden keine DAGs geparst → keine DAGs in der UI sichtbar).

35. **RAM-Planung**: Vollständiger Stack mit OM benötigt ~12-16 GB RAM:
    - Bestehende Services (PG, Keycloak, MinIO, Nessie, Trino, Dremio, Airflow): ~6-8 GB
    - OpenMetadata (ES 512m + OM-Server ~1 GB + OM-DB ~200 MB): ~2-3 GB
    - Bei RAM-Engpässen: Dremio vorübergehend stoppen (`docker compose stop dremio`)

#### OpenMetadata OIDC Keycloak (optional, wenn SSO gewünscht)

Um OM an Keycloak anzubinden, folgende Schritte:

1. **Keycloak-Client anlegen** (Realm `lakehouse` → Clients → Create):
   - Client ID: `openmetadata`
   - Client Type: Confidential
   - Valid Redirect URIs: `http://localhost:8585/callback`
   - Client Secret: aus `.env` (`KEYCLOAK_CLIENT_SECRET_OPENMETADATA`)

2. **docker-compose.yml**: Im `openmetadata-server`-Service `AUTHENTICATION_PROVIDER=basic` ersetzen durch:
   ```yaml
   - AUTHENTICATION_PROVIDER=custom-oidc
   - CUSTOM_OIDC_AUTHENTICATION_PROVIDER_NAME=Keycloak
   - AUTHENTICATION_AUTHORITY=http://keycloak:8082/realms/lakehouse
   - AUTHENTICATION_CLIENT_ID=${KEYCLOAK_CLIENT_ID_OPENMETADATA:-openmetadata}
   - AUTHENTICATION_CALLBACK_URL=http://localhost:8585/callback
   - AUTHENTICATION_PUBLIC_KEYS=["http://keycloak:8082/realms/lakehouse/protocol/openid-connect/certs","http://localhost:8585/api/v1/system/config/jwks"]
   ```

3. `docker compose up -d --force-recreate openmetadata-server` ausführen.

4. **OIDC-Discovery-URL** für Debugging: `http://keycloak:8082/realms/lakehouse/.well-known/openid-configuration`

### Keycloak 26.x Healthcheck

17. **Management-Port 9000**: Keycloak 26.x nutzt Port 9000 für Management-Endpoints (inkl. Health). HTTP-Port 8082 hat KEINEN Health-Endpoint. Aktivierung: `--health-enabled=true`.

18. **Kein curl/wget im Container**: Keycloak-Container hat weder curl noch wget. Lösung: Bash TCP-Check `exec 3<>/dev/tcp/localhost/9000`.

### Portabilität

19. **Realm Auto-Import**: `--import-realm` importiert JSON-Dateien aus `/opt/keycloak/data/import/` nur bei frischer DB.

20. **`docker compose restart` vs `up -d`**: `restart` erstellt Container nicht neu → Port-Änderungen, Image-Änderungen etc. brauchen `up -d`.

### Airflow 3.1.8 Migration (19.03.2026)

21. **TrinoOperator entfernt in apache-airflow-providers-trino 6.5.0**: Airflow 3.x nutzt neues Provider-Modell. TrinoOperator gibt es nicht mehr. **Lösung**: TrinoHook direkt in PythonOperator verwenden (`TrinoHook.run()` statt TrinoOperator).

22. **Breaking Changes in Airflow 3.x**:
    - `schedule_interval` Parameter in DAG-Definition → Muss zu `schedule` werden
    - `airflow webserver` → `airflow api-server` (docker-compose command)
    - `airflow db init` → `airflow db migrate` (database initialization)
    - Operator-Imports haben sich verändert: `from airflow.operators.bash` → `from airflow.providers.standard.operators.bash`

23. **Import-Migrationsregel für Airflow 3.x**:
    - `airflow.operators.python` → `airflow.providers.standard.operators.python` (PythonOperator)
    - `airflow.operators.bash` → `airflow.providers.standard.operators.bash` (BashOperator)
    - `airflow.operators.http` nicht mehr existent → `airflow.providers.http.operators.http` (SimpleHttpOperator)
    - Standard-Operatoren sind jetzt in "standard providers" ausgelagert

24. **dbt-trino 1.10.1 bleibt stabil**: Nur 1 Patch nötig (connections.py behavior_flags null-guard). Mit dbt-adapters>=1.22.9 wird der Bug automatisch gefixt—sed-Patch wird umleitbar.

25. **DAG-Parsing unter Airflow 3.1.8**: Nach Import-Fixes können DAGs mit `docker exec airflow python -c "from dags.<dag_name> import dag"` validiert werden. Kein Airflow-CLI nötig für schnelle Checks.

26. **Airflow 3.x: DAG-Processor ist eigener Prozess** (CRITICAL):
    - Ab Airflow 3.x läuft der DAG-Processor NICHT mehr im Scheduler. Er ist ein eigenständiger Service: `airflow dag-processor`
    - Ohne diesen Prozess: `dag`- und `serialized_dag`-Tabellen bleiben leer – Scheduler findet keine DAGs, OM-Ingestion findet keine Pipelines
    - **Lösung**: Separater Docker-Compose-Service `airflow-dag-processor` mit Command `airflow dag-processor`
    - `airflow dags list` gibt "No data found" wenn der dag-processor nicht läuft

27. **Airflow 3.x: User Creation ohne CLI**:
    - `airflow users create` und `airflow fab create-admin` existieren in 3.x nicht mehr
    - `app.appbuilder.sm` → `AirflowSecurityManagerV2` – hat keine `add_user`/`find_user` Methoden
    - **Lösung**: `FabAirflowSecurityManagerOverride(app.appbuilder).add_user(...)` explizit instanziieren
    - Import: `from airflow.providers.fab.auth_manager.security_manager.override import FabAirflowSecurityManagerOverride`
    - App-Erzeugung: `create_app(enable_plugins=False)` aus `airflow.providers.fab.www.app`

30. **OpenMetadata: Schedule-Konfiguration überlebt keinen Stack-Neuanlage** (CRITICAL):
    - `docker compose down -v` löscht den `openmetadata_db_data`-Volume – alle OM-Daten incl. Schedules weg
    - **Lösung**: `scripts/om_setup_schedules.py` nach Stack-Neuanlage einmalig ausführen
    - Skript ist idempotent (wiederholbar), setzt alle drei Schedules: Airflow 02:00, Trino 03:00, dbt 04:00 UTC
    - Login: Passwort muss Base64-encodiert übergeben werden (`base64.b64encode(b'admin').decode()`)
    - **ACHTUNG**: Hardcodierte UUIDs wurden am 05.05.2026 durch dynamische Lookups ersetzt. Skript
      benötigt korrekte Service-Namen als Konstanten: `TRINO_SERVICE_NAME`, `AIRFLOW_SERVICE_NAME`.
    - Voraussetzung: Trino- und Airflow-Connector müssen in OM-UI bereits angelegt sein (Settings → Services).
    - `om_glossary_ingest.py` – korrekter Aufruf: `OM_TOKEN=<token> python3 scripts/om_glossary_ingest.py glossary_structure.json`
      (nicht `om_dbt_ingestion.yaml` – das ist eine Ingestion-Konfiguration, keine Glossar-Datei)

31. **dbt-Ingestion benötigt immer aktuelles `catalog.json`**:
    - `catalog.json` wird NICHT von dbt auto-generiert bei `dbt run` – nur bei `dbt docs generate`
    - Im DAG `dbt_run_lakehouse_ki` ist ein letzter Task `dbt_docs_generate` ergänzt
    - Volume-Mount `./dbt/target:/opt/dbt/target:ro` im `openmetadata-ingestion`-Container nötig
    - Erst nach dem ersten DAG-Lauf ist `catalog.json` vorhanden – bis dahin schlägt OM dbt-Ingestion fehl (graceful, kein Crash)

29. **OpenMetadata dbt-Connector (OM 1.12.3)**:
    - dbt ist kein eigener OM-Service – es reichert einen bestehenden Datenbank-Service an
    - `serviceName` muss auf den existierenden OM-Service zeigen (hier: `lakehouse_trino`)
    - **Korrektes YAML**: `sourceConfig.config.type: DBT` + `dbtConfigSource.dbtConfigType: local`
    - FALSCH: `type: DBTLocalConfig` oder Dateipfade direkt in sourceConfig.config (nicht verschachtelt)
    - RICHTIG: Dateipfade unter `dbtConfigSource:` verschachteln
    - `catalog.json` muss erst via `dbt docs generate` erzeugt werden (ist NICHT in git)
    - `dbt docs generate` ausführen: `docker exec lakehouse_airflow_scheduler bash -c "cd /opt/dbt && dbt docs generate --profiles-dir . --project-dir ."`
    - Volume-Mount für Ingestion: `-v /pfad/zu/dbt/target:/opt/dbt/target:ro`
    - Artefakte: manifest.json (Modell-DAG+Tests), catalog.json (Spalten), run_results.json (Testergebnisse)

28. **OpenMetadata Airflow-Connector (OM 1.12.3)**:
    - Connector liest Airflow-DB **direkt via SQLAlchemy** – kein REST-API-Aufruf!
    - Connection-Typ: `Postgres` mit `scheme: postgresql+psycopg2`, `authType: {password: "..."}` (nicht einfaches `password`-Feld)
    - `hostPort` für den Airflow-Service-Eintrag: URL der Airflow-UI (`http://airflow:8080`) – nur Metadaten, keine API-Calls
    - Voraussetzung: `serialized_dag`- und `dag`-Tabellen müssen befüllt sein (dag-processor muss laufen)

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

**Letzte Aktualisierung**: 20. März 2026, 21:45  
**Sachstandortingredienzen**: 50+ Einträge  
**Repository**: /Users/jens/git/lakehouse_ki

---

## 🔍 Session 3 - OpenMetadata Ingestion Setup (20. März 2026)

### OM Ingestion-Container
- **Image**: `docker.getcollate.io/openmetadata/ingestion:1.12.3` (~3 GB, enthält Airflow 3.1.5 + alle OM-Ingestion-Packages)
- **Plugin**: `openmetadata-managed-apis==1.12.3.0` muss zusätzlich installiert werden (nicht vorinstalliert im Image)
- **Airflow 3.x Kompatibilität**: Plugin unterstützt `/api/v2/openmetadata/` Endpoint (v2 für Airflow 3.x, v1 für 2.x)
- **collate-data-diff**: Package `>=0.11.9` existiert nicht auf PyPI für Python 3.9 → lokale Installation scheitert; Docker-Image enthält alles
- **metadata CLI**: Pfad im Container ist `/home/airflow/.local/bin/metadata` (nicht im System-PATH)

### Trino-Connector
- **Service Name**: `lakehouse_trino` (OM Service-ID: `462416d7-a94f-4861-839c-bab23b302bfd`)
- **Ingestion Pipeline ID**: `d81fcfc0-d439-4cf7-9088-15fba2aa577a`
- **Ergebnis erste Ingestion**: 12 Records, 6 Tabellen, 0 Fehler, 100% Success Rate, 1.6s Laufzeit
- **hostPort-Pitfall**: OM Ingestion-Client braucht `/api` Suffix: `http://openmetadata-server:8585/api` (Client hängt `v1/...` selbst an → ohne `/api` läuft der health_check auf `/v1/system/version` statt `/api/v1/system/version`)
- **health_check-Fehler**: `'Response' object is not subscriptable` = URL falsch, Client bekommt HTML statt JSON zurück

### Bot-Token API (OM 1.12.3)
- Token lesen: `GET /api/v1/users/token/{userId}` → `{"JWTToken": "eyJ..."}`
- Bot-User-ID: `GET /api/v1/users?limit=50&isBot=true` → Name `ingestion-bot`, ID `371fea1f-...`
- Bots abrufen: `GET /api/v1/bots/name/ingestion-bot` (gibt Bot-Entity, **nicht** Token)
- `generateToken` (PUT): **404** in 1.12.3 → existiert nicht mehr
- Token-Typ: `"tokenType": "BOT"`, Subject: `ingestion-bot`, Expiry: `null` (Unlimited)

### Docker-Compose Änderungen
- Neuer Service: `openmetadata-ingestion` (Port 8090→8080)
- Volume: `openmetadata_ingestion_data`
- OM-Server `PIPELINE_SERVICE_CLIENT_ENDPOINT` geändert: `http://airflow:8080` → `http://openmetadata-ingestion:8080`

---

## 🐛 Session 4 - OM Ingestion-Container Fix (23. März 2026)

### Issue #32: openmetadata-ingestion Container beendet sofort (Exit 0)

**Symptom**: OM-Katalog komplett leer nach `docker compose up`. Container lief laut `docker ps -a` nur kurz.

**Root Cause 1 – Subshell `(cmd &)` verhindert `wait`**:
```bash
# FALSCH – Subshell-Prozesse werden nicht als Jobs im Parent-Kontext registriert
(airflow api-server --port 8080 &)
wait  # sieht keine Jobs → kehrt sofort zurück

# RICHTIG – direkter Hintergrund-Job
airflow api-server --port 8080 &
wait  # wartet auf alle direkten Background-Jobs
```

**Root Cause 2 – Airflow 3.x SimpleAuthManager zufälliges Passwort**:
- Airflow 3.1.5 (im OM-Ingestion-Image) nutzt `SimpleAuthManager` statt `FabAuthManager`
- Beim ersten Start ohne explizite User-Config generiert SimpleAuthManager ein **zufälliges Passwort** (sichtbar in den Logs): `Simple auth manager | Password for user 'admin': gNtk6yrFG8MXebnH`
- OM-Server versucht `admin/admin` → 401 Unauthorized → Ingestion-Pipelines können nicht getriggert werden
- Fix: `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS=admin:admin` setzen (Format: `user:role`)
- Rollen: `admin`, `op`, `user`, `viewer`, `public`

**Root Cause 3 – `airflow users create` in Airflow 3.x entfernt**:
- Existiert nur für FabAuthManager (Airflow 2.x)
- In 3.x mit SimpleAuthManager obsolet → Fehler beim Start → Container bricht ab

**Fix**: Alle drei Punkte in `docker-compose.yml` korrigiert + Health-Check auf `/api/v2/monitor/health` (Airflow 3.x) aktualisiert

### Airflow 3.x SimpleAuthManager
- Config-Key: `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS` (Format: `user:role,user2:role2`)
- Config-Key: `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=true` → alle definierten User erhalten Admin-Rechte
- Health-Endpoint: `GET /api/v2/monitor/health` (Response: `{"metadatabase":{"status":"healthy"},"scheduler":{...}}`)
- Airflow 2.x Health-Endpoint `/health` existiert in 3.x **nicht mehr**

---

## 🔧 Cognos Analytics → OpenMetadata Bridge (ersetzt durch cognos_api_sync.py, 06.05.2026)

> **Veraltet**: Das alte dateibasierte Skript `cognos_to_openmetadata.py` und der zugehörige DAG wurden entfernt.
> Aktuelles Skript: `scripts/cognos_api_sync.py` – liest direkt von der Cognos REST API, kein manueller JSON-Export nötig.
> Aktueller DAG: `airflow/dags/cognos_api_sync_dag.py`
> Dokumentation: ARCHITECTURE.md Abschnitt "Cognos Data Module → Katalog Bridge"

**Klassen (Dashboard-Erweiterung)**:
- `CognosDashboard` – Parser für Dashboard JSON (Tabs, Widgets, Datenquellen, MetadataLoader)
- `CognosOMIngester.ingest_dashboard()` – Erstellt Dashboard + Charts in OM, verknüpft mit Data Models
- `CognosOMIngester._ingest_chart()` – Erstellt Chart-Entitäten pro Widget
- `CognosOMIngester._build_dashboard_description()` – Markdown mit Tab-Übersicht und Spalten-Gruppierung
