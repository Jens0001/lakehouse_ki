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
  127.0.0.1 keycloak
  ```
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
   ```bash
   docker compose up -d --build
   ```
   `--build` ist erforderlich, da Airflow ein Custom Image nutzt (mit `authlib` für OIDC).
   Beim ersten Start passiert automatisch:
   - Keycloak importiert Realm `lakehouse` (OIDC-Clients + Test-User)
   - `minio-init` Container legt Buckets `lakehouse` und `airflow-logs` in MinIO an
   - Airflow initialisiert DB + Admin-User

4. **Überprüfen, ob alle Services laufen**:
   ```bash
   docker compose ps
   # Oder ausführlicher mit OIDC-Checks:
   bash scripts/health_check.sh
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
| **Airflow** | http://localhost:8081 | 8081 | OAuth2 (Keycloak) | Workflow-Orchestrierung |
| **PostgreSQL** | localhost:5432 | 5432 | airflow / airflow123 | Interne Datenbank |

## 🔐 Authentifizierung

Alle Web-Services nutzen **Keycloak OIDC / OAuth2** zur Authentifizierung:

### OIDC-Clients (automatisch konfiguriert)

- **MinIO**: Client-ID `minio`, Redirect `http://localhost:9001/oauth_callback`
- **Airflow**: Client-ID `airflow`, Redirect `http://localhost:8081/oauth-authorized/keycloak`
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

## 📁 Projektstruktur

```
lakehouse_ki/
├── .env                          # Umgebungsvariablen für alle Services
├── .instructions.md              # Workspace-Anweisungen
├── docker-compose.yml            # Docker Compose Konfiguration
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
│   └── setup_buckets.sh         # MinIO Buckets Setup
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
    schedule_interval='@daily'
) as dag:
    task = BashOperator(
        task_id='my_task',
        bash_command='echo "Hello from Lakehouse"'
    )
```

Dann im Airflow UI aktivieren.

## 🐛 Troubleshooting

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
**Letzte Aktualisierung**: 19. März 2026  
**Version**: 0.3.0-alpha (dbt + Data Vault 2.0 Layer-Architektur)
