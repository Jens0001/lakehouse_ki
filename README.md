# 🏗️ Lakehouse KI - Data & Analytics Stack

Ein modernes, containerisiertes Data Lakehouse Setup mit Keycloak-basierter Authentifizierung, Apache Iceberg für Datenversionierung und mehreren Query Engines.

## 📋 Projektübersicht

Das Projekt kombiniert führende Open-Source-Tools zu einem integrierten Data Analytics Stack:

- **Data Storage**: MinIO (S3-kompatible Object Storage)
- **Datenkatalog**: Nessie (Iceberg Table Management)
- **Query Engines**: Trino und Dremio (SQL-basierte Abfragen)
- **Orchestration**: Apache Airflow (Workflow-Automatisierung)
- **Authentication**: Keycloak (OpenID Connect / OAuth2)
- **Versionskontrolle**: Iceberg (Apache Iceberg Format)

## 🏗️ Architektur

```
┌─────────────────────────────────────────────────────────────┐
│                    Keycloak (OIDC/OAuth2)                   │
│                      Port: 8082                              │
└──────────────┬──────────────────────────────────────────────┘
              │ Authentication
              ▼
┌──────────────────────────────────────────────────────────────┐
│                        Lakehouse Network                     │
│  (Docker Network: lakehouse_network)                        │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   MinIO      │  │   Nessie     │  │   Postgres   │        │
│  │  (S3 Store)  │  │  (Catalog)   │  │  (DB)        │        │
│  │   9000,9001  │  │    19120     │  │   5432       │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│         ▲                  ▲                ▲                  │
│         │                  │                │                  │
│  ┌──────┴──────┬───────────┴────┬──────────┴──────┐            │
│  ▼             ▼                ▼                 ▼            │
│ ┌────────┐  ┌────────┐  ┌────────────┐  ┌────────────┐        │
│ │ Trino  │  │ Dremio │  │  Airflow   │  │ Keycloak   │        │
│ │ 8080   │  │  9047  │  │   8081     │  │   8082     │        │
│ └────────┘  └────────┘  └────────────┘  └────────────┘        │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## 🚀 Schnellstart

### Voraussetzungen

- Docker & Docker Compose v2+
- 8GB RAM (empfohlen)
- 20GB freier Speicherplatz
- **`/etc/hosts` Eintrag** (einmalig, erforderlich für OIDC/SSO):
  ```
  # Lokale Entwicklung (gleicher Rechner):
  127.0.0.1 keycloak

  # Zugriff von einem anderen Rechner (VM-IP einsetzen):
  # 192.168.1.50 keycloak
  ```

  **Eintrag setzen (einmalig):**

  <details>
  <summary>🐧 Linux / 🍎 macOS</summary>

  ```bash
  # Lokale Entwicklung:
  echo '127.0.0.1 keycloak' | sudo tee -a /etc/hosts

  # Remote-Zugriff (VM-IP anpassen):
  # echo '192.168.1.50 keycloak' | sudo tee -a /etc/hosts
  ```
  </details>

  <details>
  <summary>🪟 Windows (PowerShell als Administrator)</summary>

  ```powershell
  # Lokale Entwicklung:
  Add-Content -Path "$env:SystemRoot\System32\drivers\etc\hosts" -Value "127.0.0.1 keycloak"

  # Remote-Zugriff (VM-IP anpassen):
  # Add-Content -Path "$env:SystemRoot\System32\drivers\etc\hosts" -Value "192.168.1.50 keycloak"
  ```
  </details>

  > **Warum?** Keycloak gibt in OIDC-Redirects die URL `http://keycloak:8082/...` zurück. Der Browser muss diesen Hostnamen auflösen können, sonst scheitert der SSO-Login.

### Installation

1. **Repository klonen und in das Verzeichnis gehen**:
   ```bash
   git clone <repo-url>
   cd lakehouse_ki
   ```

2. **Umgebungsvariablen konfigurieren**:
   ```bash
   cp .env.example .env
   # Secrets anpassen (oder Defaults für lokale Entwicklung beibehalten)
   ```

3. **Stack starten**:

   **Option A – Lokale Entwicklung** (Zugriff nur von localhost):
   ```bash
   docker compose up -d --build
   ```

   **Option B – VM / Remote-Zugriff** (von anderen Rechnern im Netzwerk):
   ```bash
   ./start.sh              # IP wird automatisch erkannt
   # oder:
   ./start.sh 192.168.1.50 # manuelle IP-Angabe
   ```
   `start.sh` erkennt die Netzwerk-IP, setzt `EXTERNAL_HOST` in `.env`, startet den Stack und aktualisiert die Keycloak Redirect URIs automatisch.

   > **⚠️ /etc/hosts auf jedem Client-Rechner**: Jeder Rechner, der auf den Stack zugreifen soll, braucht den Eintrag `<VM-IP> keycloak` in seiner `/etc/hosts` Datei.

   `--build` ist erforderlich, da Airflow ein Custom Image nutzt (Java JRE 17, JDBC-Treiber für Oracle & DB2, dbt-trino 1.10.1 und zugehörige Provider).
   Beim ersten Start passiert automatisch:
   - Keycloak importiert Realm `lakehouse` (OIDC-Clients + Test-User)
   - `minio-init` Container legt Buckets `lakehouse` und `airflow-logs` in MinIO an
   - Airflow initialisiert DB + Admin-User
   - OpenMetadata startet und ist unter http://localhost:8585 erreichbar

4. **Überprüfen, ob alle Services laufen**:
   ```bash
   docker compose ps
   # Oder ausführlicher mit OIDC-Checks:
   bash scripts/health_check.sh
   ```

### start.sh – Ablauf im Detail

```
./start.sh [IP] [--build]
│
├── 1  Parameter & IP-Erkennung
│        EXTERNAL_HOST aus Argument oder automatisch (hostname -I)
│
├── 2  .env aktualisieren
│        EXTERNAL_HOST · OM_URL · KEYCLOAK_HOSTNAME · KEYCLOAK_URL
│
├── 3  Pre-Start-Checks
│        ├── Verzeichnis-Berechtigungen  (airflow/dags, logs, dbt)
│        ├── Airflow Fernet Key          (generieren falls fehlt/ungültig)
│        └── Keycloak Client Secrets     (generieren falls Platzhalter)
│
├── 4  Stack starten
│        docker compose up -d [--build]
│
├── 5  Keycloak warten & konfigurieren           (max. 120 s)
│        ├── Client Secrets in Keycloak injizieren
│        └── Redirect URIs aktualisieren          (nur bei externer IP)
│
├── 6  Business Glossary importieren
│        scripts/om_glossary_ingest.py            (nur wenn OM_TOKEN gesetzt)
│
├── 7  OpenMetadata warten                        (max. 180 s)
│        Health-Check: GET /api/v1/system/version
│
├── 8  ingestion-bot Token holen
│        ├── Token aus OM-API → .env  (OPENMETADATA_INGESTION_BOT_TOKEN)
│        ├── OpenLineage Transport-JSON → .env  (OPENLINEAGE_TRANSPORT_JSON)
│        └── Airflow-Neustart wenn Token neu
│
├── 9  Konnektoren anlegen & Ingestion triggern  (max. 90 s)
│        scripts/om_setup_connectors.py
│        ├── Trino   Database Service  (tägl. 03:00 UTC)
│        ├── Airflow Pipeline Service  (tägl. 02:00 UTC)
│        └── dbt     Pipeline          (tägl. 04:00 UTC)  → alle sofort getriggert
│
└── 10 Fertig – Service-URLs ausgeben
```

## 📊 Service-URLs & Zugangsdaten

| Service | URL | Port | Standard-Login | Notiz |
|---------|-----|------|-----------------|-------|
| **Keycloak Admin** | http://localhost:8082 | 8082 | admin / admin123 | Zentrale Authentifizierung |
| **MinIO Console** | http://localhost:9001 | 9001 | OAuth2 (Keycloak) | S3-kompatible Objektspeicherung |
| **MinIO S3 API** | http://localhost:9000 | 9000 | - | Programmatischer Zugriff |
| **Nessie Catalog** | http://localhost:19120/api | 19120 | - | Iceberg-Datenkatalog API |
| **Trino UI** | https://localhost:8443/ui | 8443 | OAuth2 (Keycloak) | SQL Query Engine (HTTPS, Self-Signed Cert) |
| **Dremio** | http://localhost:9047 | 9047 | admin / Admin1234! | SQL Query Engine (kein OIDC, Enterprise-only) |
| **Airflow** | http://localhost:8081 | 8081 | admin / admin | Workflow-Orchestrierung (api-server + scheduler + dag-processor All-in-One) |
| **PostgreSQL** | localhost:5432 | 5432 | airflow / airflow123 | Interne Datenbank |
| **OpenMetadata** | http://localhost:8585 | 8585 | admin@open-metadata.org / admin | Data Governance Katalog (Swagger: /swagger-ui) |
| **OM Ingestion** | http://localhost:8090 | 8090 | admin / admin | Ingestion Pipeline Runner (Airflow 3.x mini) |

> **Hinweis**: Bei Nutzung von `start.sh` mit einer externen IP ersetzen alle URLs `localhost` durch die gesetzte `EXTERNAL_HOST` Adresse.

## 🔐 Authentifizierung

Alle Web-Services nutzen **Keycloak OIDC / OAuth2** zur Authentifizierung:

### OIDC-Clients (automatisch konfiguriert)

- **MinIO**: Client-ID `minio`, Redirect `http://localhost:9001/oauth_callback`
- **Airflow**: Client-ID `airflow`, Redirect `http://localhost:8081/auth/oauth-authorized/keycloak`
- **Trino**: Client-ID `trino`, Redirect `http://localhost:8080/oauth2/callback`
- **Dremio**: ❌ Kein OIDC-Support (nur Dremio Enterprise/Cloud)

### Test-User

Beim ersten Start wird automatisch ein Test-User importiert:
- **Username**: `testuser`
- **Passwort**: `test123`
- **Realm**: `lakehouse`

Dieser User kann sich an MinIO, Trino und Airflow via SSO anmelden.

### Weitere Benutzer anlegen

1. Öffnen Sie http://localhost:8082 (Keycloak Admin)
2. Melden Sie sich mit `admin` / `admin123` an
3. Wählen Sie Realm `lakehouse`
4. Gehen Sie zu **Users** → **Add User**
5. Vergeben Sie Passwort und aktivieren Sie "Temporary = OFF"
6. Der Benutzer kann sich nun an allen SSO-fähigen Services anmelden

## 🎵 Kaggle API Setup (Spotify-Daten)

Der `spotify_kaggle_download` DAG lädt Spotify-Datensätze von Kaggle herunter. Dafür benötigt ihr einen kostenlosen Kaggle API-Key.

### API-Key generieren

1. **Kaggle-Konto erstellen** (falls noch nicht vorhanden):
   - https://www.kaggle.com (mit Google/Email registrieren)

2. **API-Token generieren**:
   - Einloggen auf https://www.kaggle.com
   - Gehen Sie zu **Settings** → **API** (oder https://www.kaggle.com/settings/account)
   - Klicken Sie auf **"Create New API Token"**
   - Eine `kaggle.json` Datei wird heruntergeladen
   - Öffnen Sie die Datei mit einem Texteditor – darin finden Sie:
     ```json
     {"username":"<dein_username>","key":"KGAT_..."}
     ```

3. **Credentials in `.env` eintragen** (optional):
   ```bash
   KAGGLE_USERNAME=<dein_username>
   KAGGLE_API_KEY=KGAT_...
   ```

   Alternativ: `start.sh` setzt die Werte automatisch beim Start.

4. **DAG triggern**:
   - Airflow UI → DAGs → `spotify_kaggle_download` → "Trigger DAG"
   - Der DAG lädt die CSV-Dateien von Kaggle und speichert sie in MinIO
   - Anschließend triggert `spotify_initial_load` den Bulk-Load in Iceberg raw-Schema

### Datensätze

- **Spotify Tracks**: https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset
- **Spotify Charts**: https://www.kaggle.com/datasets/dhruvildave/spotify-charts

## 📁 Projektstruktur

```
lakehouse_ki/
├── .env                          # Umgebungsvariablen für alle Services
├── .instructions.md              # Workspace-Anweisungen
├── docker-compose.yml            # Docker Compose Konfiguration
├── start.sh                      # Start-Script mit IP-Erkennung (VM-Zugriff)
├── README.md                      # Diese Datei
├── KEYCLOAK_SETUP.md            # Detailliertes Keycloak Setup Guide
├── Changelog.md                  # Historie aller Änderungen
├── Tasks.md                      # Anstehende Aufgaben
├── Memory.md                     # Notizen & Erkenntnisse
│
├── airflow/
│   ├── Dockerfile
│   ├── dags/                     # Airflow DAGs (Workflows)
│   ├── plugins/                  # Airflow Custom Plugins
│   ├── logs/                     # Airflow Logs
│   └── webserver_config.py       # Airflow OIDC-Konfiguration
│
├── dbt/
│   ├── dbt_project.yml          # DBT Projektkonfiguration
│   ├── profiles.yml             # DBT Profile
│   ├── models/                  # DBT Datenmodelle
│   ├── tests/                   # DBT Tests
│   └── ...
│
├── dremio/
│   ├── data/                    # Dremio Datenspeicher
│   └── ...
│
├── dremio-etc/
│   ├── dremio.conf              # Dremio OIDC-Konfiguration
│   ├── core-site.xml
│   └── logback.xml
│
├── trino/
│   ├── config.properties        # Trino OIDC-Konfiguration
│   ├── etc/                     # Trino Konfiguration
│   ├── catalog/                 # Trino Datenkatalog-Verbindungen
│   └── ...
│
├── catalog/
│   └── conf/                    # Nessie Katalog-Konfiguration
│
├── storage/
│   └── data/                    # MinIO Objektspeicher
│
├── init-scripts/
│   ├── postgres-init.sql        # PostgreSQL Initialisierung
│   ├── setup-keycloak.sh        # Keycloak automatisches Setup
│   ├── setup_buckets.sh         # MinIO Buckets Setup
│   └── update-keycloak-redirects.sh # Redirect URIs für Remote-Zugriff
│
└── volumes/
    └── ...                      # Docker Volumes
```

## 🔧 Konfiguration & Customization

### Environment-Variablen

Bearbeiten Sie `.env`, um die Konfiguration anzupassen:

```bash
# Keycloak Admin
KEYCLOAK_ADMIN_PASSWORD=admin123  # ÄNDERN SIE DIES!

# Service Secrets
KEYCLOAK_CLIENT_SECRET_AIRFLOW=airflow-secret-key-12345  # ÄNDERN SIE DIES!

# Datenbank
POSTGRES_PASSWORD=airflow123      # ÄNDERN SIE DIES!
```

### Service-spezifische Konfiguration

- **Airflow**: [airflow/webserver_config.py](airflow/webserver_config.py)
- **Trino**: [trino/config.properties](trino/config.properties)
- **Dremio**: [dremio-etc/dremio.conf](dremio-etc/dremio.conf)
- **PostgreSQL**: [init-scripts/postgres-init.sql](init-scripts/postgres-init.sql)

Siehe [KEYCLOAK_SETUP.md](KEYCLOAK_SETUP.md) für Details.

## 📊 Workflow-Beispiele

### 1. Daten mit MinIO hochladen

Der Bucket `lakehouse` wird beim Stack-Start automatisch erstellt (`minio-init` Container).

```bash
# Vom Host (mc CLI installieren: https://min.io/docs/minio/linux/reference/minio-mc.html)
mc alias set lakehouse http://localhost:9000 minioadmin minioadmin123
mc cp myfile.parquet lakehouse/lakehouse/

# Oder per Docker:
docker compose exec minio-init mc cp /data/myfile.parquet lakehouse/lakehouse/
```

### 2. dbt Models ausführen

dbt ist im Airflow-Container integriert und läuft gegen Trino (Iceberg-Catalog → Nessie → MinIO).

```
Airflow DAG → dbt run → Trino → Iceberg/Nessie → MinIO (S3)
```

- **DAG**: `dbt_run_lakehouse_ki` (in Airflow UI aktivieren)
- **dbt-Projekt**: `dbt/` Verzeichnis (gemountet nach `/opt/dbt`)
- **Profil**: `dbt/profiles.yml` → Trino (`host: trino`, Catalog: `iceberg`)
- **Manuell im Container**:
  ```bash
  docker compose exec airflow bash -c "cd /opt/dbt && dbt run --profiles-dir /opt/dbt"
  ```

### 3. Abfrage via Trino

```bash
# Mit trino CLI (HTTPS, Self-Signed Cert)
trino --server https://localhost:8443 --user testuser --insecure

# Alle Schichten anzeigen
SHOW SCHEMAS IN iceberg;

# SQL-Abfrage auf Iceberg-Tabellen
SELECT * FROM iceberg.raw.my_table LIMIT 10;
```

## 🏛️ Data Lakehouse Layer-Architektur

Die detaillierte Beschreibung aller Schichten (Landing, Raw, Staging, Data Vault, Business Vault, Marts) und die dbt-Modellstruktur sind in [ARCHITECTURE.md](ARCHITECTURE.md#8-data-lakehouse-schichtenarchitektur) dokumentiert.

### 4. Airflow DAG erstellen

Datei: `airflow/dags/my_dag.py`
```python
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    'my_etl_dag',
    start_date=datetime(2026, 1, 1),
    schedule='@daily'
) as dag:
    task = BashOperator(
        task_id='my_task',
        bash_command='echo "Hello from Lakehouse"'
    )
```

Dann im Airflow UI aktivieren.

## � Metadaten-Ingestion (OpenMetadata)

OpenMetadata crawlt automatisch alle drei Datenquellen nach folgendem Zeitplan:

| Connector | Schedule (UTC) | Was wird geingestet |
|-----------|---------------|--------------------|
| **Trino** (`lakehouse_trino_metadata_ingestion`) | täglich 03:00 | Tabellen, Schemas, Spalten, Tags aus `iceberg.*` |
| **Airflow** (`lakehouse_airflow_metadata_ingestion`) | täglich 02:00 | DAGs, Tasks, Run-History |
| **dbt** (`lakehouse_dbt_metadata_ingestion`) | täglich 04:00 | Modell-Beschreibungen, Tests, Lineage, Tags |

**Voraussetzungen nach `docker compose up`**: Keine – alles läuft vollautomatisch.

### Wie es funktioniert

```
Airflow DAG (dbt_run_lakehouse_ki) läuft täglich @daily:
  dbt_deps → dbt_run → dbt_test → dbt_docs_generate
                                       ↓
                              target/catalog.json aktuell

OM-Ingestion-Container liest täglich um 03/04 Uhr:
  Trino  (03:00) → Schemas & Tabellen
  dbt    (04:00) → Beschreibungen, Tests, Lineage aus target/
  Airflow (02:00) → DAGs & Pipeline-Runs
```

- Alle drei Ingestion-Schedules sind direkt in OpenMetadata konfiguriert und werden vom `openmetadata-ingestion`-Container (Port 8090) ausgeführt.
- Das Verzeichnis `dbt/target/` ist per Volume-Mount im OM-Ingestion-Container unter `/opt/dbt/target` eingebunden.
- Der Airflow-DAG `dbt_run_lakehouse_ki` generiert nach jedem dbt-Lauf mit `dbt docs generate` die aktuellen Artefakte, damit OM stets frische Daten hat.

### Manuell triggern (ad-hoc)

```bash
# dbt-Artefakte aktualisieren (catalog.json neu generieren)
docker exec lakehouse_airflow_scheduler bash -c "cd /opt/dbt && dbt docs generate --profiles-dir /opt/dbt"

# Alle drei Ingestion-Pipelines manuell ausführen
docker run --rm \
  --network lakehouse_ki_lakehouse_network \
  -v "$(pwd)/dbt/target:/opt/dbt/target:ro" \
  -v "$(pwd)/scripts/om_dbt_ingestion.yaml:/tmp/dbt.yaml:ro" \
  docker.getcollate.io/openmetadata/ingestion:1.12.3 \
  bash -c "/home/airflow/.local/bin/metadata ingest -c /tmp/dbt.yaml"
```

### 🛠️ OpenMetadata Helper-Scripts

Alle drei Skripte werden von `start.sh` automatisch aufgerufen. Sie können auch manuell ausgeführt werden.

---

#### `scripts/om_setup_connectors.py` — Konnektoren anlegen *(Hauptskript)*

Legt die drei Konnektoren und ihre Ingestion-Pipelines idempotent an und triggert sie sofort.
Wird von `start.sh` automatisch nach dem Stack-Start ausgeführt. Nach einem `docker compose down -v`
(Volume-Löschung) stellt dieses Skript den gesamten Katalog-Setup wieder her.

| Konnektor | Service-Typ | Schedule |
|---|---|---|
| Trino | Database Service | täglich 03:00 UTC |
| Airflow | Pipeline Service | täglich 02:00 UTC |
| dbt | Pipeline am Trino-Service | täglich 04:00 UTC |

```bash
# Manuelle Ausführung (Stack muss laufen):
POSTGRES_USER=airflow POSTGRES_PASSWORD=airflow123 \
  python3 scripts/om_setup_connectors.py
```

> **Hinweis**: Das Skript ist idempotent – mehrfaches Ausführen hat keinen negativen Effekt.
> Bereits vorhandene Services und Pipelines werden nicht überschrieben.

---

#### `scripts/om_glossary_ingest.py` — Business Glossar importieren

Liest `glossary_structure.json` und erstellt die hierarchischen Business-Begriffe in OpenMetadata.
Wird von `start.sh` automatisch aufgerufen wenn `OM_TOKEN` in der `.env` gesetzt ist.

```bash
# Manuelle Ausführung (z.B. nach Änderungen an glossary_structure.json):
export OM_URL="http://localhost:8585/api"
export OM_TOKEN="<token aus OM-UI: Settings → Users → [User] → Token>"
python3 scripts/om_glossary_ingest.py glossary_structure.json
```

> **Hinweis**: `OM_TOKEN` muss einmalig manuell in der OM-UI generiert und in die `.env` eingetragen
> werden. `OM_URL` wird von `start.sh` automatisch gesetzt.

---

#### `scripts/om_setup_schedules.py` — Schedules nachträglich ändern

Ändert die Cron-Schedules bestehender Ingestion-Pipelines ohne Stack-Neustart.
Wird **nicht** automatisch von `start.sh` aufgerufen – nur bei Bedarf manuell ausführen.

```bash
# Manuelle Ausführung (z.B. nach Schedule-Anpassung):
python3 scripts/om_setup_schedules.py
```

Alternativ in der OM-UI: **Settings → Services → [Service] → Ingestions → Edit → Schedule**

## �🐛 Troubleshooting

### Container starten nicht
```bash
# Logs prüfen
docker compose logs postgres
docker compose logs keycloak

# Clean Start (alle Volumes löschen + neu aufbauen)
docker compose down -v
rm -rf storage/data/.minio.sys dremio/data
docker compose up -d --build
```

### OAuth2 Login funktioniert nicht
```bash
# 1. /etc/hosts prüfen (MUSS vorhanden sein!)
grep keycloak /etc/hosts
# Erwartete Ausgabe: 127.0.0.1 keycloak

# 2. Keycloak-Logs prüfen
docker logs lakehouse_keycloak

# 3. Health-Check ausführen
bash scripts/health_check.sh

# 4. Redirect URIs in Keycloak Admin überprüfen
# http://localhost:8082/admin → Realm lakehouse → Clients
```

### Trino zeigt "Web UI disabled"
Trino Web UI erfordert HTTPS bei aktiviertem OAuth2. Zugriff über **https://localhost:8443/ui/** (nicht http://localhost:8080). Browser-Warnung für Self-Signed Cert akzeptieren.

### Services erreichen sich nicht untereinander
```bash
# Netzwerk überprüfen
docker compose exec airflow ping postgres

# DNS-Resolution testen
docker compose exec trino nslookup keycloak
```

## 📈 Performance-Tipps

- **MinIO**: `MINIO_MAX_CONNECTIONS=1000` für hohen Durchsatz
- **Trino**: Query-Parallelisierung über mehr Workers
- **Dremio**: Reflection für häufig abgefragte Felder aktivieren
- **PostgreSQL**: Tuning der `shared_buffers` und `work_mem` für größere Workloads

## 🔒 Sicherheit - Production Checklist

- [ ] Alle Passwörter in `.env` ändern
- [ ] HTTPS/SSL für alle Services aktivieren
- [ ] Keycloak im Production Mode starten (nicht `start-dev`)
- [ ] Firewall auf Port 8082 (Keycloak Admin) beschränken
- [ ] Regelmäßige PostgreSQL-Backups einrichten
- [ ] Monitoring und Alerting (z.B. Prometheus) aktivieren
- [ ] Audit-Logs für Keycloak aktivieren
- [ ] Minimale Berechtigungen für alle Services setzen

## 📝 Dokumentation

- [KEYCLOAK_SETUP.md](KEYCLOAK_SETUP.md) - Detailliertes Keycloak Setup-Guide
- [Changelog.md](Changelog.md) - Historie aller Änderungen
- [Tasks.md](Tasks.md) - Anstehende Aufgaben
- [Memory.md](Memory.md) - Notizen & Erkenntnisse

## 🤝 Beitragen

Für Feedback, Bugs oder Feature-Requests: Siehe [Tasks.md](Tasks.md) und [Changelog.md](Changelog.md).

## 📜 Lizenz

Alle verwendeten Komponenten folgen ihren eigenen Open-Source-Lizenzen.

---

**Projekt-Status**: 🟡 Laufend (In Development)  
**Letzte Aktualisierung**: 17. April 2026  
**Version**: 0.4.1-alpha (OpenMetadata Governance Katalog + automatisierte URL-Konfiguration & Glossary-Integration)
