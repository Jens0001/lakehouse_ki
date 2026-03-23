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

## 3. Semantische Schicht

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

Cognos Analytics kennt kein OpenLineage. Das letzte Stück der Lineage-Kette (`Dremio → Cognos Data Module`) sowie die semantische Schicht des Data Modules (Labels, Rollen, Hierarchien) landen daher **nicht automatisch** im Katalog. Ein Custom-Bridge-Skript schließt diese Lücke.

```
 Cognos REST API        Python Bridge-Skript         DataHub / OpenMetadata
GET /datamodules/{id} → JSON parsen + mappen  →  REST API pushen
     (täglich per Airflow DAG)
```

**Was das Skript aus dem Data Module JSON extrahiert:**

| Data Module JSON | Mapping im Katalog |
|---|---|
| `tableSet[].columnList` (label, datatype, usage) | Dataset + Schema mit Spalten-Beschreibungen + Rollen |
| `relationshipSet` (joins, cardinality) | Join-Metadaten auf Dataset-Ebene |
| `hierarchySet` (Drill-Down-Stufen) | Glossar-Terme oder Tags (z.B. "Zeitdimension: Jahr > Monat > Tag") |
| `query.sourceList` (Dremio-Tabelle) | Lineage-Kante: `Dremio-Tabelle → Cognos Dataset` |

**Beispiel für die Lineage-Kante (DataHub Payload):**

```json
{
  "upstreamLineage": {
    "upstreams": [{
      "dataset": "urn:li:dataset:(urn:li:dataPlatform:dremio,business_vault.fact_weather_hourly,PROD)",
      "type": "TRANSFORMED"
    }]
  }
}
```

**Einschränkungen des Bridge-Ansatzes:**

- Kein Event-Trigger möglich – Cognos emittiert keine Events. Das Skript läuft täglich als Airflow DAG
- Änderungen am Data Module werden erst beim nächsten Skript-Lauf im Katalog sichtbar (max. 24h Verzögerung)
- Report → Data Module Lineage (welcher Cognos-Report nutzt welches Data Module) ist über die öffentliche API nicht zuverlässig abfragbar – bleibt vorerst undokumentiert

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
Dremio (Iceberg-Connector) ◀── Dremio-Connector crawlt
    │
    ▼
Cognos Data Module ◀── Bridge-Skript (täglich, Airflow DAG)
```

---

## 4. Measures und Kennzahlen

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

## 5. Multi-User-Security und Zugriffsrechte

### Dremio OSS – freie Rollen

Dremio OSS kennt nur zwei feste Rollen: `ADMIN` und `PUBLIC`. Custom Roles (z.B. `analyst-group-a`) sind Enterprise-only. Eine automatische Abbildung von AD-Gruppen auf granulare Dremio-Zugriffsrechte ist in OSS **nicht möglich**.

### Apache Ranger – Trino-Absicherung

Für granulare Multi-User-Zugriffsrechte (Table-Level, Column-Level, Row-Level) ist Apache Ranger die OSS-Lösung für Trino. Ranger hat offizielle Trino-Plugin-Unterstützung, für Dremio OSS existiert kein stabiles Ranger-Plugin.

> **Architekturprinzip für Multi-User-Szenarien**: Endnutzer-Datenzugang immer über Trino (abgesichert via Ranger). Dremio bleibt Admin-/BI-Tool-Schicht ohne granulare Endnutzer-Isolation.

### LDAP / Active Directory

Dremio OSS unterstützt LDAP/AD für die **Authentifizierung** (Login), aber nicht für die Autorisierung (Rechtevergabe auf Basis von AD-Gruppen). AD-Gruppen können nicht auf Dremio-Ressourcen gemappt werden (Enterprise-Funktion).

---

## 6. Cognos Analytics Anbindung

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

## 7. Offene Fragen

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

## 8. Data Lakehouse Schichtenarchitektur

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
Hub  → eindeutige Geschäftsobjekte (z.B. h_location, h_customer)
Link → Beziehungen zwischen Hubs  (z.B. l_order_customer)
Sat  → beschreibende Attribute, historisiert mit load_date + hashdiff
```

Macros: `automate_dv.hub`, `automate_dv.link`, `automate_dv.sat`

---

### Schicht 5: Business Vault / Star-Schema

| Eigenschaft | Wert |
|-------------|------|
| **Iceberg-Schema** | `iceberg.business_vault` |
| **Materialisierung** | dbt `table` |
| **Objekte** | Dimensionstabellen (`dim_*`), Faktentabellen (`fact_*`) |

Aus den Data Vault Objekten werden klassische Dimensional Models (Star-Schema) abgeleitet. Diese Schicht ist optimiert für Performance und Lesbarkeit.

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

Marts sind schmale, performante Tabellen für konkrete Anwendungsfälle (BI-Tools, APIs, Reports). Komplexe Kennzahlen-Logik wird hier implementiert statt in Cognos Data Modules (→ Kapitel 4).

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

## 7. Orchestration – Airflow 3.x Entscheidung

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
