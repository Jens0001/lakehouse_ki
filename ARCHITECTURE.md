# Architekturentscheidungen – Lakehouse KI

Dieses Dokument hält konzeptuelle Architekturentscheidungen fest, die sich aus der Analyse des Stacks ergeben haben. Es ergänzt die technische README.md um die **Begründungen hinter Designentscheidungen**.

---

## 0. Stack-Übersicht

### Services

| Service    | Image                                       | Ports             | Zweck                         |
|------------|---------------------------------------------|-------------------|-------------------------------|
| PostgreSQL | `postgres:13`                               | 5432 (intern)     | Shared DB (Airflow + Keycloak)|
| Keycloak   | `quay.io/keycloak/keycloak:latest`          | 8082:8082         | OIDC Identity Provider        |
| MinIO      | `minio/minio:RELEASE.2024-11-07T00-52-20Z` | 9000, 9001        | S3 Object Storage             |
| Nessie     | `projectnessie/nessie:latest`               | 19120             | Iceberg Catalog               |
| Trino      | `trinodb/trino:latest`                      | 8080, 8443 (HTTPS)| Query Engine (Schreiben, dbt) |
| Dremio     | `dremio/dremio-oss:latest`                  | 9047, 31010       | Query Engine / Semantic Layer |
| Airflow    | Custom (`build: ./airflow`) v3.1.8          | 8081→8080         | Orchestrator (API-Server)     |
| Airflow Scheduler | Custom (`build: ./airflow`) v3.1.8   | –                 | DAG Task Execution             |
| Airflow DAG-Processor | Custom (`build: ./airflow`) v3.1.8 | –               | DAG Parsing & Serialization (Airflow 3.x) |
| OpenMetadata DB  | `postgres:15-alpine`                        | intern            | Dedizierte DB für OM          |
| OpenMetadata ES  | `elasticsearch:8.10.2`                      | intern            | Such-Backend für OM           |
| OpenMetadata     | `openmetadata/server:latest`                | 8585, 8586        | Data Governance Katalog       |
| OpenMetadata Ingestion | `openmetadata/ingestion:1.12.3`       | 8090→8080         | Ingestion Pipeline Runner     |

### Startup-Reihenfolge (depends_on)

```
PostgreSQL (healthcheck) → Keycloak (healthcheck) → MinIO, Trino
PostgreSQL (healthcheck) → Airflow
OpenMeta-DB (healthcheck) + OpenMeta-ES (healthcheck) → OpenMetadata → OpenMetadata Ingestion
```

**Keycloak Healthcheck**: Port **9000** (Management-Port in Keycloak 26.x), nicht der HTTP-Port 8082.
Bash TCP-Check im Container: `exec 3<>/dev/tcp/localhost/9000`

---

## 1. Rollen der Query Engines: Trino vs. Dremio

Der Stack betreibt zwei Query Engines bewusst nebeneinander – nicht als Redundanz, sondern mit klar getrennten Aufgaben:

```
Trino:   Transformation, Schreiben, dbt  →  die "Maschine" im Hintergrund
Dremio:  Lesen, BI-Anbindung, Beschleunigung  →  die Schnittstelle nach vorne
```

### Trino – Verantwortlichkeiten

- Einzige Engine die **schreibend** auf Iceberg zugreift (INSERT, DELETE, MERGE)
- Führt alle dbt-Transformationen aus (Raw → Data Vault → Business Vault → Marts)
- Erstellt und befüllt alle physischen Iceberg-Tabellen
- Einzige Engine die Iceberg Views lesen und schreiben kann

### Dremio – Verantwortlichkeiten

- **Lese-Schnittstelle** für BI-Tools (Cognos Analytics via JDBC / Arrow Flight)
- **Reflections**: Transparente Query-Beschleunigung ohne Änderung am Abfragecode
- **Cross-Source-Federation**: Joins über heterogene Quellen (Iceberg + PostgreSQL + weitere)
- **Arrow Flight SQL**: Moderneres, schnelleres Protokoll als JDBC für BI-Performance

### Warum beide Engines?

Trino allein wäre für Cognos funktional ausreichend (JDBC möglich). Dremio bringt jedoch:

1. **Reflections** – Pre-aggregierte Datenmaterialisierung die automatisch bei passenden Queries genutzt wird (kein Äquivalent in Trino OSS)
2. **Arrow Flight** – Deutlich höherer Datendurchsatz gegenüber JDBC, relevant bei großen Report-Abfragen
3. **Query-Pushdown** – Filter werden direkt an Quellsysteme delegiert, nicht erst nach dem Laden

> **Entscheidung**: Dremio bleibt im Stack. Performance via Reflections und Arrow Flight sind für produktiven Cognos-Betrieb relevante Vorteile.

---

## 2. Views: Keine Iceberg Views als Architektur-Komponente

### Problem

Trino kann Iceberg Views erstellen und lesen. Dremio OSS kann Iceberg Views **nicht lesen** – weder technisch noch durch Konfiguration. Es gibt keinen öffentlichen Roadmap-Eintrag für Iceberg View Support in Dremio OSS (Stand März 2026, recherchiert im GitHub und der Dremio Community).

### Entscheidung

Keine Iceberg Views als dauerhafte Schicht in der Datenarchitektur. Ausnahme: Views die nachweislich **ausschließlich über Trino** konsumiert werden und nie über Dremio erreichbar sein müssen.

### Konsequenz für `dim_date`

`dim_date` enthielt ursprünglich relative Zeitfelder (`is_yesterday`, `days_ago` etc.) und war als Trino VIEW materialisiert, damit diese Felder täglich aktuell bleiben ohne gespeichert zu werden.

**Warum relative Felder in der Datenschicht sinnvoll sind** (nicht nur "syntaktischer Zucker"):
- Kanonische Definition: `is_yesterday` ist für alle Konsumenten identisch – kein Risiko inkonsistenter Implementierungen
- Debuggbarkeit: Cognos-generiertes SQL kann gegen Trino/Dremio reproduziert werden
- Governance: Die Definition ist testbar (dbt-Test) und versioniert (Git)

**Lösung**: `dim_date` als **physische Tabelle** (`materialized: table`) mit täglichem Full Refresh:
- Refresh um 01:00 Uhr (vor Geschäftsbeginn)
- Staleness-Fenster: 00:00–01:00 Uhr (1 Stunde, akzeptabel)
- Dremio kann die Tabelle lesen ✅
- Cognos, SQL-Nutzer, alle Tools sehen dieselbe Definition ✅

---

## 3. Surrogate Key Strategie für Dimensionen

### Problem

Im Data Vault 2.0 wird der Hub Hash Key (HK) als MD5 auf den Business Key berechnet. Im bisherigen Star Schema (Business Vault) diente dieser HK direkt als Primärschlüssel der Dimensionen und als Fremdschlüssel in den Fakten.

Das hat zwei Schwächen:
1. **Kein eigenständiger Dimensionsschlüssel**: Der HK identifiziert die Business-Entität, nicht eine konkrete Version der Dimension. Bei Einführung von SCD2 (Slowly Changing Dimensions) hätten mehrere Versionen denselben HK.
2. **Architektonisch inkorrekt nach Kimball**: Im Dimensional Modeling nach Kimball muss der Surrogate Key in der Faktentabelle eindeutig auf genau eine Dimensionsversion zeigen.

### Entscheidung

Jede Dimension im Business Vault erhält einen **Hash-basierten Surrogate Key (SK)**:

```
SK = md5(hub_hash_key || '|' || valid_from)
```

- **Deterministisch**: Gleiche Eingabe → gleicher Hash. Re-runs erzeugen identische Keys.
- **Idempotent**: Full Refresh der Dimension produziert dieselben SKs.
- **Keine Sequenz nötig**: Trino/Iceberg bieten keine `SERIAL`/`IDENTITY`. Der Hash löst das.
- **SCD2-fähig**: Bei Einführung von SCD2 erzeugt jede neue Version (neues `valid_from`) automatisch einen neuen SK.
- **Pipe-Separator**: `'|'` zwischen den Hash-Bestandteilen verhindert Kollisionen (z.B. `A|BC` ≠ `AB|C`).

Ein wiederverwendbares dbt-Macro `generate_dimension_sk` kapselt die Generierung.

### Betroffene Dimensionen

| Dimension | Surrogate Key | Als FK in Fakten |
|---|---|---|
| `dim_location` | `location_sk` | `fact_weather_hourly`, `fact_weather_daily`, `weather_trends` |
| `dim_price_zone` | `price_zone_sk` | `fact_energy_price_hourly`, `fact_energy_price_daily`, `energy_price_trends` |
| `dim_date` | `date_id` (unverändert) | Alle Fakten |
| `dim_time` | `time_id` (unverändert) | Alle Fakten |

`dim_date` und `dim_time` haben bereits eigenständige Integer-PKs und sind nicht betroffen.

### Hub Hash Key bleibt erhalten

Der HK (`location_hk`, `price_zone_hk`) bleibt in Dimensionen und Fakten als Referenzspalte erhalten:
- Debugging: Rückverfolgung zum Data Vault Hub
- Lineage: OpenMetadata kann die Kette Hub → Dimension → Fakt nachvollziehen
- `unique_key` in inkrementellen Fakten bleibt auf HK-Basis (stabil, da HK sich nie ändert)

### Konsequenz für Fakt-Loads

Der Surrogate Key wird **zur Load-Time** in die Faktentabelle geschrieben (Join auf Dimension), nicht erst bei Query-Time. Consumer machen nur einen einfachen Equality-Join:

```sql
-- Consumer-Query: performant, kein Range-Join
SELECT ...
FROM fact_weather_hourly f
JOIN dim_location d ON f.location_sk = d.location_sk
```

### Zukunft: SCD2

Bei Einführung von SCD2 wird:
- `dim_location` um `valid_to` erweitert
- Der Fakt-Load macht einen Range-Join (`valid_from ≤ measured_at < valid_to`) zur Load-Time
- Der SK ändert sich pro Version → Consumer-Joins bleiben identisch

> **Konvention**: Jede neue Dimension erhält immer `<entity>_sk = md5(hk || '|' || valid_from)` als Primärschlüssel. Dokumentiert als wiederverwendbares Macro `generate_dimension_sk`.

---

## 4. Semantische Schicht

### Problem

Zwischen Iceberg-Tabellen und Cognos Analytics gibt es potentiell mehrere Schichten die semantische Beschreibungen enthalten können:
- dbt `schema.yml` (Spaltenbeschreibungen)
- Dremio Virtual Datasets (VDS)
- Cognos Data Module

Doppelte Pflege ist ein konkretes Risiko: Beschreibungen, berechnete Felder und Kennzahlen landen an mehreren Orten mit unterschiedlichen Lifecycles.

### Entscheidung: Single Source of Truth

```
dbt schema.yml          →  Single Source of Truth für Definitionen
      ↓ (automatisch via dbt manifest.json → Dremio API)
Dremio VDS              →  nur punktuell, für dynamische Berechnungen
      ↓
Cognos Data Module      →  nur Cognos-Spezifika (Joins, Hierarchien)
```

**dbt `schema.yml`** ist die primäre Dokumentationsquelle:
- Spaltenbeschreibungen, Tests, Tags
- Wird per `dbt docs generate` in `manifest.json` exportiert
- Kann automatisch via Dremio REST API in VDS-Metadaten überführt werden

**Dremio VDS** wird nur eingesetzt wenn:
- Berechnungen nötig sind die in physischen Iceberg-Tabellen nicht stehen sollen
- Dynamische Felder gebraucht werden die Dremio-seitig berechnet werden müssen
- Kein täglicher Full Refresh der Basistabelle akzeptabel ist

**Cognos Data Module** enthält ausschließlich Cognos-spezifische Konzepte:
- Join-Beziehungen zwischen Tabellen (Star-Schema-Kardinalitäten)
- Drill-Down-Hierarchien (Jahr → Monat → Tag)
- Keine Business-Logik, keine Spaltenbeschreibungen (diese kommen aus dbt/VDS)

### Datenkatalog

**OpenMetadata** wurde als Data Governance Katalog ausgewählt (Entscheidung 20.03.2026, Begründung in Abschnitt 7.2) und unter Port 8585 in den Stack integriert. Dieser crawlt:
- Trino (Iceberg-Tabellen + Spaltenmetadaten via Trino-Connector)
- dbt (Lineage aus `manifest.json`, Beschreibungen aus `schema.yml` via dbt-Connector)
- Airflow (Job-Lineage: welcher DAG füllt welche Tabelle via OpenLineage-Receiver)
- Dremio (VDS + physische Tabellen via Dremio-Connector, nachgelagert)

Damit wird End-to-End-Lineage (API → Landing → Raw → Data Vault → Business Vault → Marts → Cognos) in einem Tool sichtbar.

> **Konsequenz**: Dremios eingebaute Katalog-UI wird durch OpenMetadata ersetzt. Das ist kein Verlust – OM deckt alle Engines gleichzeitig ab und ergänzt sie um native Data Quality Checks.

### OpenLineage – Transportprotokoll für Lineage

Die Verbindung zwischen Airflow, dbt und dem Katalog wird über das **OpenLineage-Protokoll** hergestellt. Es ist kein Tool, sondern ein offenes JSON-Format für Verarbeitungsereignisse. Jeder Job sendet beim Start und Ende ein sogenanntes RunEvent per HTTP POST an den Katalog:

```json
{
  "eventType": "COMPLETE",
  "job":     { "name": "open_meteo_to_raw",   "namespace": "airflow" },
  "inputs":  [{ "name": "api.open-meteo.com", "namespace": "http" }],
  "outputs": [{ "name": "raw.weather_hourly", "namespace": "iceberg" }]
}
```

Das Event sagt: *Job X hat Dataset A gelesen und Dataset B geschrieben.* Der Katalog verwebt alle Events zur durchgehenden Lineage-Kette.

```
┌─────────────┐  OpenLineage-Event  ┌──────────────────┐
│   Airflow   │ ───────────────────▶│                  │
│  (Emitter)  │                     │  DataHub /       │
├─────────────┤  OpenLineage-Event  │  OpenMetadata    │
│    dbt      │ ───────────────────▶│  (Receiver)      │
│  (Emitter)  │                     └──────────────────┘
└─────────────┘                              │
                                    Lineage-Graph aufbauen,
                                    visualisieren, Impact-Analyse
```

**Warum das die entscheidende Lücke schließt**: Airflow und dbt kennen sich gegenseitig nicht. Ohne OpenLineage sieht man in dbt, dass `stg_weather` aus `iceberg.raw.weather_hourly` liest – aber nicht welcher Airflow-DAG diese Tabelle gefüllt hat. Der Katalog verknüpft beide Event-Streams automatisch über den gemeinsamen Dataset-Namen zur lückenlosen Kette:

```
Ohne OpenLineage:   DAG ──?──▶ iceberg.raw ──✅──▶ stg ──✅──▶ DV ──✅──▶ marts

Mit OpenLineage:    DAG ──✅──▶ iceberg.raw ──✅──▶ stg ──✅──▶ DV ──✅──▶ marts
                   (Airflow-Event)           (dbt aus manifest.json)
```

**Technische Einbindung**:
- Airflow: `apache-airflow-providers-openlineage` – Umgebungsvariable `OPENLINEAGE_URL` zeigt auf den Katalog-Endpunkt
- dbt: `openlineage-integration-common` + Katalog-spezifischer Adapter
- Marquez als separates Backend entfällt: DataHub und OpenMetadata haben den OpenLineage-Receiver nativ eingebaut

> **Must-have bei der Katalog-Auswahl**: Der gewählte Katalog muss einen OpenLineage-kompatiblen HTTP-Endpunkt bereitstellen – sonst kann die lückenlose Lineage nicht realisiert werden.

### Cognos Data Module → Katalog Bridge

Cognos Analytics kennt kein OpenLineage. Das letzte Stück der Lineage-Kette (Trino-Tabellen → Cognos Data Module → Cognos Dashboard) sowie die semantische Schicht (Labels, Rollen, Hierarchien, Visualisierungen) landen daher **nicht automatisch** im Katalog. Das Skript `scripts/cognos_to_openmetadata.py` schließt diese Lücke.

#### Was das Skript tut

Das Skript liest zwei Arten von Cognos Analytics JSON-Exporten und macht sie für OpenMetadata "lesbar":

**1. Datenmodul-Ingestion** (Data Module JSON → OM Dashboard Data Models)

Das Cognos Data Module beschreibt die semantische Schicht über den physischen Tabellen: welche Spalten es gibt, welche Rolle sie haben (Identifier / Attribute / Measure), wie Tabellen verknüpft sind und welche Drill-Hierarchien existieren. Das Skript übersetzt diese Informationen in OpenMetadata-Entitäten:

```
  Cognos Data Module JSON           cognos_to_openmetadata.py          OpenMetadata
  ─────────────────────             ───────────────────────            ─────────────
  querySubject[] ─────────────────→ Dashboard Data Model (pro QS)
    └─ queryItem[] ───────────────→   Columns (Name, Typ, Tags)
        └─ usage ─────────────────→   Classification-Tag (Identifier/Attribute/Measure)
        └─ datatype ──────────────→   OM dataType (BIGINT→BIGINT, VARCHAR→VARCHAR, …)
        └─ regularAggregate ──────→   Beschreibungs-Feld (Aggregation: total/count/…)
  relationship[] ─────────────────→ Markdown-Beschreibung (Join-Spalten, Kardinalitäten)
  drillGroup[] ───────────────────→ Markdown-Beschreibung (Hierarchie-Stufen)
  useSpec.dataSourceOverride ─────→ Lineage-Kante: Trino-Tabelle → Data Model
```

| Data Module JSON | OpenMetadata Entität |
|---|---|
| `querySubject[]` (pro Tabelle/View) | Dashboard Data Model mit Spalten + Usage-Tags |
| `querySubject[].item[]` (Spalten) | Columns mit Datentyp-Mapping, Usage-Klassifikation |
| `relationship[]` (Joins, Kardinalitäten) | Markdown-Beschreibung am Data Model |
| `drillGroup[]` (Drill-Down-Hierarchien) | Markdown-Beschreibung (z.B. Minute → Stunde → Tag → Monat → Jahr) |
| `customSort[]` (benutzerdef. Sortierungen) | Dokumentiert in der Modul-Beschreibung |
| `useSpec.dataSourceOverride` (Trino-Katalog/Schema) | Lineage-Kante: `lakehouse_trino.iceberg.smarthome.<table>` → Data Model |

**2. Dashboard-Ingestion** (Dashboard JSON → OM Dashboard + Charts)

Das Cognos Dashboard JSON beschreibt die Visualisierungsschicht: welche Tabs/Seiten ein Dashboard hat, welche Widgets (Charts) darauf liegen und welche Spalten aus dem Datenmodul jedes Widget verwendet. Das Skript übersetzt dies in OpenMetadata Dashboard- und Chart-Entitäten:

```
  Cognos Dashboard JSON             cognos_to_openmetadata.py          OpenMetadata
  ─────────────────────             ───────────────────────            ─────────────
  layout.items[] (Tabs) ─────────→ Dashboard (mit Tab-Übersicht)
    └─ items[] (Widgets) ────────→   Charts (Name, Typ, Beschreibung)
        └─ visId ────────────────→   chartType (bundlecolumn→Bar, rave2line→Line, …)
        └─ dataViews[].dataItems →   Referenzierte Spalten (→ Lineage zu Data Models)
  dataSources.sources[] ─────────→ Zuordnung: Dashboard → Datenmodul (über name-Match)
```

| Dashboard JSON | OpenMetadata Entität |
|---|---|
| `layout.items[]` (Tabs/Seiten) | Dashboard mit Beschreibung der Tab-Struktur |
| Widget `features.Models_internal` | Chart (Name, chartType, referenzierte Spalten) |
| `dataSources.sources[].name` | Verknüpfung zum Datenmodul (name-Match → `dataModels`-Referenz) |
| `features.MetadataLoader.metadataSubsetIds` | Gesamtliste aller im Dashboard genutzten Spalten |

**3. Ende-zu-Ende Lineage**

Durch die Kombination beider Ingestions entsteht die vollständige Lineage-Kette in OpenMetadata:

```
Trino-Tabelle (iceberg.smarthome.dim_tag)
    │ Lineage-Kante (automatisch)
    ▼
Dashboard Data Model (dim_tag – semantische Schicht)
    │ dataModels-Referenz (automatisch)
    ▼
Dashboard (Jahresauswertung Heizkosten Lakehouse)
    │ charts-Referenz (automatisch)
    ▼
Chart (Heizkosten Inkl Warmwasser – Bar-Chart)
```

#### Implementierung

**Skript**: `scripts/cognos_to_openmetadata.py`
- Reine Python-Stdlib (urllib, json, argparse) – keine externen Abhängigkeiten
- Idempotent: PUT-basierte Upserts, beliebig oft ausführbar
- `--dry-run` zum Validieren ohne API-Calls, `-v` für Debug-Ausgaben
- Umgebungsvariablen: `OM_URL`, `OM_TOKEN`, `OM_TRINO_SVC`

**Airflow DAG**: `airflow/dags/cognos_to_openmetadata_dag.py`
- Täglicher Lauf um 06:00 UTC
- Liest Datenmodul- und Dashboard-JSON aus `/opt/airflow/cognos_exports/`
- Task-Reihenfolge: Datenmodul zuerst (erstellt Data Models), dann Dashboard (referenziert Data Models)

#### Einschränkungen

- Kein Event-Trigger möglich – Cognos emittiert keine Events. Das Skript läuft täglich als Airflow DAG
- Änderungen am Data Module / Dashboard werden erst beim nächsten Skript-Lauf im Katalog sichtbar (max. 24h Verzögerung)
- Cognos-Dashboards müssen als JSON exportiert und im `cognos_exports/`-Verzeichnis abgelegt werden (kein automatischer Export per Cognos REST API implementiert)

**Gesamtbild der vollständigen Lineage-Kette:**

```
Open-Meteo API
    │ (manuell als Upstream im Katalog eingetragen)
    ▼
Airflow DAG ────────────────────────────────── OpenLineage-Event
    │
    ▼
iceberg.raw.weather_hourly ◀── OpenLineage-Event empfangen
    │
    ▼
dbt staging → data_vault → business_vault → marts ◀── dbt manifest.json
    │
    ▼
Trino (Iceberg-Tabellen) ◀── Trino-Connector crawlt
    │
    ▼
Cognos Data Model ◀── cognos_to_openmetadata.py (täglich, Airflow DAG)
    │
    ▼
Cognos Dashboard + Charts ◀── cognos_to_openmetadata.py (täglich, Airflow DAG)
```

---

## 5. Measures und Kennzahlen

### Entscheidungsrahmen

| Kennzahl-Typ | Definition in | Begründung |
|---|---|---|
| Einfache Aggregationen (sum, avg, count) | Cognos Data Module | Dynamisch aggregierbar auf jeder Granularität, interaktiv |
| Komplexe Business-Logik (mehrstufige Formeln, Joins) | dbt Marts | Versioniert, testbar, für alle Tools (Trino, Dremio, direkte SQL-Abfragen) |
| Relative Zeitfilter (is_yesterday, days_ago) | Physische Tabelle `dim_date` (täglich refresht) | Kanonisch, debuggbar, konsistent für alle Konsumenten |

### Faustregel

> Wenn die Kennzahl SQL braucht das komplexer ist als eine einzelne Aggregationsfunktion, gehört sie in dbt. Wenn sie eine einfache Aggregation ist die Cognos on-the-fly auf beliebiger Granularität anwenden soll, gehört sie ins Data Module.

### Warum keine Measures in Dremio VDS?

Measures in VDS sind für Cognos nicht sichtbar anders als in Dremio selbst. Cognos kann zwar auf VDS zugreifen, muss aber die Aggregationen im Data Module erneut definieren. Die VDS-Ebene würde Measures-Definitionen verdoppeln ohne Mehrwert.

---

## 6. Multi-User-Security und Zugriffsrechte

### Dremio OSS – freie Rollen

Dremio OSS kennt nur zwei feste Rollen: `ADMIN` und `PUBLIC`. Custom Roles (z.B. `analyst-group-a`) sind Enterprise-only. Eine automatische Abbildung von AD-Gruppen auf granulare Dremio-Zugriffsrechte ist in OSS **nicht möglich**.

### Apache Ranger – Trino-Absicherung

Für granulare Multi-User-Zugriffsrechte (Table-Level, Column-Level, Row-Level) ist Apache Ranger die OSS-Lösung für Trino. Ranger hat offizielle Trino-Plugin-Unterstützung, für Dremio OSS existiert kein stabiles Ranger-Plugin.

> **Architekturprinzip für Multi-User-Szenarien**: Endnutzer-Datenzugang immer über Trino (abgesichert via Ranger). Dremio bleibt Admin-/BI-Tool-Schicht ohne granulare Endnutzer-Isolation.

### LDAP / Active Directory

Dremio OSS unterstützt LDAP/AD für die **Authentifizierung** (Login), aber nicht für die Autorisierung (Rechtevergabe auf Basis von AD-Gruppen). AD-Gruppen können nicht auf Dremio-Ressourcen gemappt werden (Enterprise-Funktion).

---

## 7. Cognos Analytics Anbindung

> **Scope-Hinweis**: Cognos Analytics läuft **nicht** in diesem Docker Compose Stack und ist nicht containerisiert. Es wird auf der finalen Zielplattform zur Verfügung stehen und sich von dort per JDBC oder Arrow Flight SQL an Dremio (ggf. Trino) anbinden. Die folgenden Architekturentscheidungen beschreiben diese externe Anbindung.

### Verbindungsarchitektur

```
Cognos Analytics
      │ JDBC oder Arrow Flight SQL
      ▼
   Dremio OSS  ←── Reflections (Query-Beschleunigung)
      │
   Iceberg-Tabellen (business_vault, marts)
      │
   Trino / Nessie / MinIO
```

### Service Account vs. Pass-Through

Cognos verbindet sich über einen **Service Account** (`cognos-svc`) – nicht per Pass-Through-Authentication. Das bedeutet:

- Dremio sieht immer denselben Datenbanknutzer
- Cognos übernimmt das User-Management (Cognos-Rollen, Object-Security)
- Multi-User-Datenbankberechtigungen (wer darf welche Tabelle sehen) sind **nicht** nötig solange Cognos die einzige Konsumenten-Schicht bleibt

> **Konsequenz**: Dremio Enterprise für Multi-User-Security ist für Cognos-Anbindung nicht erforderlich. OSS reicht.

---

## 8. Offene Fragen

### 7.1 Dremio – langfristige Relevanz neben DataHub

Mit einem dedizierten Datenkatalog (DataHub/OpenMetadata) entfällt Dremios eingebaute Katalog-UI als Differenzierungsmerkmal. Verbleibende echte Vorteile:

- **Reflections** – kein Äquivalent in Trino OSS
- **Arrow Flight SQL** – Performance-Vorteil für Cognos
- **Cross-Source-Federation** – relevant wenn PostgreSQL + Iceberg in einer Query nötig ist

**Offene Frage**: Bei niedrigem Cognos-Abfragevolumen oder wenn Arrow Flight von Cognos nicht genutzt wird – ist der Betriebsaufwand eines zusätzlichen Services gerechtfertigt? Entscheidung nach erstem produktivem Cognos-Betrieb.

### 7.2 Datenkatalog: DataHub vs. OpenMetadata

**Entscheidung (20.03.2026): OpenMetadata**

Kriterien für die Auswahl:

| Kriterium | OpenMetadata | DataHub |
|---|---|---|
| UI-Usability | Moderner, intuitiver | Funktional, komplexer |
| Data Quality | **Eingebaut** (eigener Test-Runner gegen Trino) | Nur Import von ext. Tools |
| Docker-Footprint | 3 Container, ~2-3 GB | ~7 Container, ~4-6 GB |
| RAM-Budget (8 GB Stack) | Knapp, aber machbar | Nicht realistisch |
| OpenLineage-Receiver | Nativ | Über Bridge aufwändiger |
| Keycloak-OIDC | Nativ (custom-oidc) | Nativ |

DataHub wäre vorzuziehen bei: sehr großer Community-Abhängigkeit, Column-Level-Lineage-Anforderungen, Domains/Data-Products-Konzept. Für diesen Stack dominiert OM durch geringeren Ressourcenbedarf und integrierten DQ-Test-Runner als klares Alleinstellungsmerkmal.

### 7.3 Views bei wachsendem Stack

Verzicht auf Iceberg Views funktioniert gut bei überschaubarer Modell-Anzahl. Bei stark wachsendem Stack (viele Teams, ACL-Segmentierungen, viele KPI-Varianten) entsteht kombinatorischer Druck Richtung Views. Dann wird die Entscheidung Trino-only-Schnittstelle vs. Dremio Enterprise vs. Alternative (Apache Polaris + Spark) neu zu bewerten sein.

---

## 9. Data Lakehouse Schichtenarchitektur

Der Stack implementiert eine klar geschichtete Datenarchitektur nach dem Medallion-Pattern, erweitert um Data Vault 2.0 für robuste Historisierung.

### Übersicht der Schichten

```
                 ┌─────────────┐
  Datenproduzenten│   Landing   │  MinIO: s3://lakehouse/landing/
                 │  (Rohdaten) │  Kein Iceberg – nur Files
                 └──────┬──────┘
                        │ Airflow lädt Daten
                 ┌──────▼──────┐
                 │     raw     │  Iceberg-Namespace: iceberg.raw
                 │  (1:1 Kopie)│  Unveränderliche Quelle
                 └──────┬──────┘
                        │ dbt ephemeral
                 ┌──────▼──────┐
                 │   staging   │  dbt ephemeral (kein Iceberg)
                 │ (Bereinigt) │  Hash-Keys, Casts, Checks
                 └──────┬──────┘
                        │ dbt incremental
                 ┌──────▼──────┐
                 │ data_vault  │  Iceberg-Namespace: iceberg.data_vault
                 │ (DV 2.0)   │  Hubs, Links, Satellites (automate-dv)
                 └──────┬──────┘
                        │ dbt table
                 ┌──────▼──────┐
                 │business_vault│ Iceberg-Namespace: iceberg.business_vault
                 │ (Star-Schema)│ Facts, Dimensions
                 └──────┬──────┘
                        │ dbt table
                 ┌──────▼──────┐
                 │    marts    │  Iceberg-Namespace: iceberg.marts
                 │(Anwendungen)│  Aggregationen, BI-Tabellen
                 └─────────────┘
```

---

### Schicht 1: Landing Zone

| Eigenschaft | Wert |
|-------------|------|
| **Speicherort** | `s3://lakehouse/landing/` |
| **Format** | CSV, JSON, Excel, Parquet (Rohdateien) |
| **Iceberg** | ❌ Nein – reine Datei-Ablage |
| **Verwaltung** | Airflow lädt Dateien direkt per mc/boto3 |

Unterpfade nach Dateiformat (automatisch angelegt):

```
s3://lakehouse/landing/
├── csv/        ← z.B. Tagesexporte aus ERP-Systemen
├── json/       ← z.B. API-Responses, Event-Streams
├── excel/      ← z.B. manuelle Reports
└── parquet/    ← z.B. vorverarbeitete Daten
```

**Beispiel (Datei ablegen):**
```bash
mc alias set local http://localhost:9000 minioadmin minioadmin123
mc cp export_2026-03-19.csv local/lakehouse/landing/csv/orders/
```

---

### Schicht 2: Raw

| Eigenschaft | Wert |
|-------------|------|
| **Iceberg-Schema** | `iceberg.raw` |
| **Materialisierung** | Iceberg-Tabellen (append-only empfohlen) |
| **Zweck** | 1:1-Abbildung der Quelldaten in Iceberg |
| **dbt-Schema** | `raw` |

Rohdaten werden unverändert (bis auf Typ-Konvertierungen) als Iceberg-Tabellen gespeichert. Keine Geschäftslogik.

**Beispiel:**
```sql
-- Tabelle: iceberg.raw.orders
CREATE TABLE iceberg.raw.orders (
    order_id     VARCHAR,
    customer_id  VARCHAR,
    order_date   DATE,
    total_amount DECIMAL(18,2),
    _loaded_at   TIMESTAMP   -- technisches Ladefeld
);
```

---

### Schicht 3: Staging

| Eigenschaft | Wert |
|-------------|------|
| **Materialisierung** | dbt `ephemeral` (kein Iceberg, nur CTE) |
| **Zweck** | Bereinigung, Typ-Casts, Hash-Keys, Rename |

Staging-Modelle erzeugen keine dauerhaften Tabellen – sie werden als Common Table Expressions (CTEs) direkt in nachgelagerte Modelle hineinkopiert.

**Wie `ephemeral` funktioniert:**

```sql
-- stg_orders.sql (ephemeral) – erzeugt keine Tabelle in Trino
select order_id, customer_id, ...
from iceberg.raw.orders

-- h_order.sql (incremental) referenziert stg_orders
-- dbt erzeugt daraus intern automatisch:
WITH stg_orders AS (
    select order_id, customer_id, ...  -- Code aus stg_orders wird als CTE eingefügt
    from iceberg.raw.orders
)
SELECT ... FROM stg_orders
```

**Warum Staging ephemeral?**
- Staging ist nur eine Zwischenschicht (Typ-Casts, Umbenennungen, Hash-Keys) – kein Mehrwert, sie dauerhaft zu speichern
- Kein Storage-Verbrauch in MinIO/Iceberg, kein Namespace `iceberg.staging` nötig
- Nicht direkt in Trino oder Dremio abfragbar (erscheint in keiner Query Engine)

---

### Schicht 4: Data Vault (DV 2.0)

| Eigenschaft | Wert |
|-------------|------|
| **Iceberg-Schema** | `iceberg.data_vault` |
| **Materialisierung** | dbt `incremental` |
| **Bibliothek** | [automate-dv](https://automate-dv.readthedocs.io/) ≥ 0.10 |
| **Objekte** | Hubs, Links, Satellites |

Data Vault 2.0 bietet auditierbare, historisierte Datenspeicherung durch klare Trennung von Identität (Hub), Beziehung (Link) und beschreibenden Attributen (Satellite).

```
Hub  → eindeutige Geschäftsobjekte (z.B. h_location, h_track, h_artist)
Link → Beziehungen zwischen Hubs  (z.B. l_track_artist)
Sat  → beschreibende Attribute, historisiert mit load_date + hashdiff
```

Macros: `automate_dv.hub`, `automate_dv.link`, `automate_dv.sat`

#### Data Vault Objekte nach Domäne

**Wetter (Open-Meteo):** h_location, s_location_details, s_weather_hourly
**Energiepreise (Energy-Charts):** h_price_zone, s_energy_price_hourly
**Spotify:** h_track, h_artist, h_country, l_track_artist, s_track_details, s_track_audio_features, s_artist_profile, s_chart_entry

#### Datenfluss Spotify-Domäne

```
Kaggle CSV (Tracks + Charts)
    ↓ [DAG: spotify_initial_load]
MinIO: s3://lakehouse/landing/csv/spotify/
    ↓ [Trino INSERT]
iceberg.raw.spotify_tracks, iceberg.raw.spotify_charts
    ↓ [dbt staging ephemeral]
stg_spotify_track, stg_spotify_chart

Spotify Web API (wöchentlich)
    ↓ [DAG: spotify_artist_update]
MinIO: s3://lakehouse/landing/json/spotify_artists/YYYY-MM-DD.json
    ↓ [Trino INSERT]
iceberg.raw.spotify_artist_snapshots
    ↓ [dbt staging ephemeral]
stg_spotify_artist_snapshot

Staging → Data Vault:
    h_track, h_artist, h_country, l_track_artist
    s_track_details, s_track_audio_features, s_artist_profile, s_chart_entry

Data Vault → Business Vault:
    dim_artist (SCD2!), dim_track, dim_country
    fact_chart_entry

Business Vault → Marts:
    artist_chart_performance (weekly/monthly)
```

---

### Schicht 5: Business Vault / Star-Schema

| Eigenschaft | Wert |
|-------------|------|
| **Iceberg-Schema** | `iceberg.business_vault` |
| **Materialisierung** | dbt `table` (Dimensionen), dbt `incremental` (Fakten) |
| **Objekte** | Dimensionstabellen (`dim_*`), Faktentabellen (`fact_*`) |

Aus den Data Vault Objekten werden klassische Dimensional Models (Star-Schema) abgeleitet. Diese Schicht ist optimiert für Performance und Lesbarkeit.

#### Modellierungsgrundsätze Business Vault

**Dimensionen:**
- Jede Dimension hat einen eigenen Surrogate Key (`<entity>_sk`), erzeugt über `generate_dimension_sk` (→ Abschnitt 3)
- Der Hub Hash Key (`<entity>_hk`) bleibt als Referenzspalte erhalten (Lineage, Debugging)
- Dimensionsattribute beschreiben die Geschäftsentität vollständig (alle relevanten Felder aus Satelliten)
- Bei Einführung von SCD2 ändert sich nur die Dimension – Fakten referenzieren weiterhin den SK

**Fakten:**
- Fremdschlüssel auf Dimensionen = Surrogate Key (SK), nicht Hub Hash Key
- `date_id` und `time_id` bleiben Integer-FKs (dim_date und dim_time haben eigene Integer-PKs)
- SK-Auflösung geschieht **zur Load-Time** (Join auf Dimension beim Schreiben der Fakt), nicht zur Query-Time
- Hub Hash Key bleibt als zusätzliche Referenzspalte in der Fakt erhalten
- Degenerate Dimensions (z.B. `measured_at`, `date_key`) werden direkt in die Fakt geschrieben

**SCD Type 2 (Slowly Changing Dimensions):**
- Aktuell implementiert für: `dim_artist` (Spotify-Künstler)
- SCD2-Felder: `valid_from`, `valid_to`, `is_current`, `version_number`
- Logik: `LEAD(load_date) OVER (PARTITION BY hk ORDER BY load_date)` berechnet `valid_to`
- `is_current = (valid_to IS NULL)` → aktuelle Version
- Satellite `s_artist_profile` liefert die History (append-only, Hashdiff-basiert)
- Die Dimension berechnet alle Versionen per Full Refresh (TABLE-Materialisierung)
- Fakten-Joins auf SCD2-Dimensionen: `WHERE is_current = TRUE` oder zeitpunktbasiert (`valid_from <= event_date AND (valid_to IS NULL OR valid_to > event_date)`)

**Zielpublikum:** dbt-Downstream-Modelle, technische Analysten, OLAP-Tools (Cognos) die selbst joinen.

Besonderheit `dim_date`:
- Enthält relative Zeitfelder (`is_yesterday`, `days_ago`, `is_current_month` etc.)
- Materialisierung: `table` mit täglichem Full Refresh um 01:00 Uhr
- Begründung: Iceberg Views aus Trino sind in Dremio OSS nicht lesbar (→ Kapitel 2)

---

### Schicht 6: Marts

| Eigenschaft | Wert |
|-------------|------|
| **Iceberg-Schema** | `iceberg.marts` |
| **Materialisierung** | dbt `table` |
| **Zweck** | Anwendungs- oder Abteilungs-spezifische Aggregationen |

Marts sind schmale, performante Tabellen für konkrete Anwendungsfälle (BI-Tools, APIs, Reports). Komplexe Kennzahlen-Logik wird hier implementiert statt in Cognos Data Modules (→ Kapitel 5).

#### Modellierungsgrundsätze Marts

**Selektive Denormalisierung:**
- Marts sind **vollständig denormalisiert** – alle für den Use Case relevanten Dimensionsattribute werden per Join aufgelöst und direkt als Spalten in den Mart geschrieben
- Der Consumer (Grafana, Dremio, Cognos) braucht keine Joins mehr – jede Query ist ein einfaches `SELECT ... WHERE ...`
- Technische Schlüssel (SK, HK) werden **nicht** in den Mart übernommen; stattdessen enthält der Mart lesbare Business-Attribute (z.B. `location_key`, `bidding_zone`, `year`, `month`)

**Auswahl der Attribute:**
- Nicht alle Dimensionsattribute werden blind übernommen – nur die für den **konkreten Anwendungsfall** relevanten
- Faustregel: Wenn ein Attribut zum Filtern, Gruppieren oder Anzeigen gebraucht wird, gehört es in den Mart. Wenn nicht, bleibt es in `business_vault.dim_*`
- Bei Dimensionen mit vielen Attributen (z.B. 30+) landen typischerweise 5–15 im Mart – der Rest ist über das Star Schema in `business_vault` erreichbar

**Abgrenzung zu Business Vault:**

| Aspekt | Business Vault (Star Schema) | Marts |
|---|---|---|
| Joins nötig? | Ja (Fakt ↔ Dimension über SK) | Nein (alles denormalisiert) |
| Technische Keys sichtbar? | Ja (SK, HK, date_id, time_id) | Nein (nur lesbare Attribute) |
| Zielpublikum | Tech-User, OLAP-Tools, dbt-Downstream | Endanwender, Dashboards, APIs |
| Flexibilität | Hoch (beliebige Joins) | Eingeschränkt (vordefinierte Perspektive) |
| Performance | Join-Overhead bei Abfrage | Maximal (kein Join) |

> **Faustregel**: OLAP-Tools (Cognos, Power BI) die eigene Joins definieren können, arbeiten besser gegen `business_vault` (Star Schema). Dashboards (Grafana) und APIs arbeiten besser gegen `marts` (flat tables).

---

### dbt Modell-Struktur

```
dbt/models/
├── staging/          ← ephemeral; Hash-Keys, Casts, Bereinigung
├── data_vault/
│   ├── hubs/         ← incremental (automate_dv.hub)
│   ├── links/        ← incremental (automate_dv.link)
│   └── satellites/   ← incremental (automate_dv.sat)
├── business_vault/   ← table; Dims + Facts (Star-Schema)
└── marts/            ← table; Aggregationen für BI/Reports
```

### dbt ausführen

```bash
# Im Airflow-Container (empfohlen – Trino erreichbar über Container-DNS)
docker compose exec airflow bash
cd /opt/dbt

dbt deps                            # automate-dv Pakete installieren (einmalig)
dbt run --profiles-dir /opt/dbt    # alle Modelle ausführen
dbt test --profiles-dir /opt/dbt   # Tests ausführen
```

Automatisierung: DAG `dbt_run_lakehouse_ki` in Airflow führt täglich `dbt deps → dbt run → dbt test` aus.

---

## 8. Orchestration – Airflow 3.x Entscheidung

### Warum Airflow 3.1.8 statt 2.8.4?

| Aspekt | Airflow 2.x | Airflow 3.x |
|--------|------------|------------|
| **Stabilität** | Etabliert, große Nutzerbasis | LTS möglich (3.1.x) |
| **Provider-Modell** | Legacy Operatoren | Modernisiert, Standardisierung |
| **Admin-UI** | Webserver (veraltete Flask-AppBuilder) | API-Server (FastAPI) |
| **TrinoOperator** | Verfügbar | Entfernt (nutze TrinoHook) |
| **Deployment** | `airflow webserver` | `airflow api-server` |
| **Sicherheit** | OAuth2 via FAB | OAuth2 via standard mechanisms |
| **Zukunftssicherheit** | EOL geplant | Active Development bis mind. 2027 |

**Entscheidung**: Airflow 3.1.8 für moderne Architektur und Zukunftssicherheit.

### Breaking Changes und die Migration

**Änderungen in Airflow 3.1.8** (müssen in DAGs beachtet werden):

1. **DAG-Parameter**: `schedule_interval="..."` → `schedule="..."`
   - Betrifft: Alle DAGs mit Scheduling
   - Impact: **Critical** – fehlerhafte DAG-Definitionen

2. **Operator-Imports**: Standardoperatoren haben neue Namespaces
   - `airflow.operators.bash` → `airflow.providers.standard.operators.bash`
   - `airflow.operators.python` → `airflow.providers.standard.operators.python`
   - Impact: **Critical** – DAGs laden nicht

3. **TrinoOperator Entfernung**: `apache-airflow-providers-trino>=6.0`
   - Alternative: TrinoHook direkt in PythonOperator verwenden
   - `hook.run(sql_statement)` ersetzt TrinoOperator
   - Impact: **Medium** – bedingt für DAGs die Trino nutzen

4. **Admin API**: `airflow webserver` → `airflow api-server`
   - Betrifft: docker-compose services/commands
   - Impact: **Critical** – Container starten nicht

5. **Datenbank-Initialisierung**: `airflow db init` → `airflow db migrate`
   - Betrifft: Startup-Sequenz in docker-compose
   - Impact: **High** – DB nicht initialisiert

### Migration-Strategie und Testplan

✅ **Durchgeführt**:
1. Dockerfile: `apache/airflow:2.8.4-python3.11` → `apache/airflow:3.1.8-python3.11`
2. Airflow Version: 3.1.8 (aus apache/airflow base image)
3. Provider Versions:
   - `apache-airflow-providers-trino>=6,<7` (TrinoHook nur)
   - `apache-airflow-providers-http>=6,<7`
4. dbt-Integration unverändert:
   - dbt-trino 1.10.1 weiterhin stabil
   - Alle 9 dbt Models PASS (0 Regressions)
5. DAG-Fixes:
   - `dbt_run_lakehouse_ki.py` – BashOperator-Import, Schedule OK
   - `open_meteo_to_raw.py` – PythonOperator-Import, TrinoHook OK, Schedule gefixt
6. Validierung: Beide DAGs laden via `python -c "from dags.<dag_name> import dag"` fehlerfrei

### Warum nicht doch Airflow 2.x?

**Optionen erwogen und verworfen**:

| Option | Risiko | Grund der Ablehnung |
|--------|--------|-------------------|
| Airflow 2.8.4 beibehalten | **Technisch höher**: Ältere Architektur, EOL absehbar | |
| Airflow 2.11.2 (unreleased Tag nutzbar) | **Sehr hoch**: Nicht in PyPI, instabil, nicht getestet | Ursprüngliches Setup – NICHT produktiv |
| Downgrade auf 2.x nach Upgrade zu 3.x | **Hoch**: Code-Churn, doppelter Aufwand | Unnötig; Migration bereits abgeschlossen |
| **Upgrade zu 3.1.8 percormance** | **Niedrig**: SLA & Architektur verbessert sich | ✅ **GEWÄHLT** |

---

## 8. Startup-Automatisierung & Konfigurationsmanagement (30.03.2026)

### Problem

Ein manueller Start des Stacks mit Docker Compose erfordert mehrere fehlerträchtige manuelle Schritte:

1. **EXTERNAL_HOST**: IP / Hostname für Remote-Zugriff manuell in `.env` setzen
2. **Keycloak URLs**: Bei Remote-Zugriff Keycloak Config für externe URLs anpassen
3. **Permissions**: DAG- und Logs-Verzeichnis Berechtigungen fixieren (sonst DAGs nicht sichtbar)
4. **Trino Config**: Keycloak-URLs in `trino/etc/config.properties` aktualisieren
5. **Keycloak Clients**: OAuth2 Redirect URIs, Root URLs für externe IPs konfigurieren
6. **Client Secrets**: Überprüfen / generieren / injizieren in Keycloak
7. **Airflow Connections**: Trino-Connection manuell erstellen

**Folge**: Neue Entwickler / Remote-Zugriff → viele manuelle Fehlerquellen.

### Entscheidung: Automatisiertes Startup-Skript + Init-Scripts

```
User:          ./start.sh [IP|--build]
                     ↓
start.sh:      Erkennt IP automatisch (oder nimmt Parameter)
               Setzt EXTERNAL_HOST in .env
               Setzt dynamische Umgebungsvariablen (KEYCLOAK_HOSTNAME, KEYCLOAK_URL)
               Fixiert Berechtigungen (chmod 777 airflow/dags, airflow/logs)
               Ruft update-trino-config.sh auf (Keycloak-URLs in Trino-Config)
                     ↓
docker compose up: Startet alle Services
                   Airflow führt scripts/airflow_init_users.py + scripts/airflow_init_connections.py aus
                     ↓
start.sh:      Wartet auf Keycloak Health-Check (Port 9000)
               Ruft setup-keycloak-secrets.sh auf (Secret-Validierung + Keycloak Injection)
               Ruft update-keycloak-redirects.sh auf (Redirect URIs, Root URLs aktualisieren)
                     ↓
Stack ready:   Alle Services laufen, Keycloak konfiguriert, OAuth2 funktioniert
```

### Implementierung

#### `start.sh` – Main Orchestrator

**Features**:
- **Automatische IP-Erkennung**: `hostname -I` (Linux), `ipconfig getifaddr` (macOS)
- **Flexible Parametrisierung**: `./start.sh [IP|localhost] [--build]`
- **Fehlertoleranz**: Prüft auf Berechtigungen, .env-Existenz bevor Container starten
- **Health-Checks**: Wartet auf Keycloak Readiness-Probe (max. 120s, 5s-Intervalle)
- **Ausgabe**: Service-URLs am Ende für schnelle Navigation

**Besonderheiten**:
```bash
# Dynamische Umgebungsvariablen (statt hardcoded docker-compose.yml):
if [ "${EXTERNAL_HOST}" = "localhost" ]; then
  export KEYCLOAK_HOSTNAME="keycloak"  # Container-intern
  export KEYCLOAK_URL="http://keycloak:8082"
else
  export KEYCLOAK_HOSTNAME="${EXTERNAL_HOST}"  # Für OIDC Issuer
  export KEYCLOAK_URL="http://${EXTERNAL_HOST}:8082"  # Für Services außerhalb Docker
fi

docker compose up -d $BUILD_FLAG
```

#### `init-scripts/update-trino-config.sh` – Trino-Config Anpassung

**Was**: Ersetzt Docker-interne Keycloak-URLs mit externen IPs in `trino/etc/config.properties`

**Warum**: Trino validiert OIDC Token-Issuer. Wenn Keycloak intern `keycloak:8082` sagt, aber Token Issuer = `<IP>:8082` hat, schlägt Validierung fehl.

**Wann**: VOR `docker compose up` (damit Trino beim Start die richtige Config hat)

**Implementation**:
```bash
sed -i.bak "s|http://keycloak:8082|http://${EXTERNAL_HOST}:8082|g" "$TRINO_CONFIG"
```

#### `init-scripts/setup-keycloak-secrets.sh` – Secret Management

**Was**: Überprüft Keycloak Client Secrets, generiert neue falls nötig, speichert in `.env` + Keycloak.

**Pattern**:
1. Liest Secret aus `.env`: `KEYCLOAK_CLIENT_SECRET_<CLIENT>`
2. Prüft ob gültig (Länge ≥ 20, kein Placeholder wie `CHANGE_ME_*`, `TODO_*`)
3. Wenn ungültig: Generiert mit `openssl rand -base64 48`
4. Speichert in `.env` (für wiederholten Start)
5. Injiziert in Keycloak Admin API: `PUT /realms/{realm}/clients/{client-uuid}` + `secret`

**Clients**: `minio`, `airflow`, `trino` (separate Secrets)

**Idempotent**: Mehrfaches Ausführen ändert nichts wenn Secrets bereits gültig sind.

#### `init-scripts/update-keycloak-redirects.sh` – Client-URL Konfiguration

**Was**: Aktualisiert Keycloak Client-Einstellungen für externe Host-Namen.

**Welche URLs**:
- **Redirect URIs**: `http://<IP>:<port>/oauth_callback` (für OAuth2 Return)
- **Web Origins**: `http://<IP>:<port>` (für CORS)
- **Root URL**: `http://<IP>:<port>/` (für relative Links)
- **Admin URL**: `http://<IP>:<port>/` (für Client-Management)

**Clients**: MinIO, Airflow, Trino (je nach konfig)

**Implementation**: Python JSON-Manipulation, Keycloak Admin REST API

#### `scripts/airflow_init_users.py` & `scripts/airflow_init_connections.py`

**airflow_init_users.py**: Erstellt Default-User `admin:admin` (FAB-App-Integration in Airflow 3.x)

**airflow_init_connections.py**: Erstellt `trino_default` Connection automatisch (Airflow 3.x Hat keine `airflow users create` CLI mehr)

**Wann**: Während `docker compose up`, im Airflow-Container-Startup

### Anforderungen

**Im `docker-compose.yml`**:
```yaml
services:
  airflow:
    command: >
      bash -c "
        airflow db migrate &&
        python /opt/airflow/scripts/airflow_init_users.py &&
        python /opt/airflow/scripts/airflow_init_connections.py &&
        airflow api-server &
        sleep 5 &&
        airflow scheduler &
        airflow dag-processor
      "
    environment:
      - KEYCLOAK_URL=${KEYCLOAK_URL:-http://keycloak:8082}
      - KEYCLOAK_HOSTNAME=${KEYCLOAK_HOSTNAME:-keycloak}
```

**Im `.env`**:
```
EXTERNAL_HOST=localhost  # Default, wird von start.sh überschrieben
KEYCLOAK_URL=http://keycloak:8082  # Default, wird von start.sh überschrieben
KEYCLOAK_HOSTNAME=keycloak  # Default
```

### Ergebnis

- ✅ **Einzeiliger Start**: `./start.sh` oder `./start.sh 192.168.1.50`
- ✅ **Keine manuellen Fehlerquellen** mehr für Permissions, URLs, Secrets
- ✅ **Remote-Zugriff on-the-fly**: Einfach IP mitgeben, alles automatisch konfiguriert
- ✅ **Idempotent**: Mehrfaches Ausführen ändert nur was nötig ist
- ✅ **Transparent**: Alle Skripte loggbar, debugging möglich

### Bekannte Limitierung: `/etc/hosts` Eintrag

**Grund**: Split-DNS-Problem bei OIDC in Docker-Umgebungen (dokumentiert in Memory.md)

**Workaround**: Jeder Client-Rechner braucht manuell:
```bash
echo '<EXTERNAL_HOST> keycloak' | sudo tee -a /etc/hosts
```

**Langfristige Lösung**: Traefik Reverse Proxy + DNS-Split-View (nicht implementiert, Aufwand > Nutzen für MVP)

---
