"""
om_setup_connectors.py – OpenMetadata Konnektoren und Ingestion-Pipelines anlegen

Legt alle drei Konnektoren idempotent an (prüft ob bereits vorhanden):
  1. Trino Database Service  + Metadata-Ingestion-Pipeline  (Schedule: 03:00 UTC)
  2. Airflow Pipeline Service + Metadata-Ingestion-Pipeline (Schedule: 02:00 UTC)
  3. dbt-Ingestion-Pipeline  (am Trino-Service angehängt)   (Schedule: 04:00 UTC)

Triggert anschließend alle drei Pipelines sofort (kein Warten auf den nächsten
Schedule) und baut den Elasticsearch-Suchindex neu auf.

Ausführung:
    python3 scripts/om_setup_connectors.py

Umgebungsvariablen (Defaults entsprechen .env.example):
    POSTGRES_USER     = airflow
    POSTGRES_PASSWORD = airflow123

Voraussetzung:
    - openmetadata-server ist healthy  (Port 8585)
    - openmetadata-ingestion ist healthy (Port 8090)
    - Airflow DAG-Processor läuft (serialized_dag-Tabelle muss befüllt sein)
"""
import json
import base64
import urllib.request
import urllib.error
import os
import sys

OM_URL = "http://localhost:8585/api/v1"

# Service-Namen wie sie in OM angelegt werden (Settings → Services)
TRINO_SERVICE_NAME   = "lakehouse_trino"
AIRFLOW_SERVICE_NAME = "lakehouse_airflow"

# Airflow-DB Zugangsdaten für den Airflow-Connector (direkte Postgres-Verbindung)
# Der OM-Ingestion-Container liest die Airflow-DB via SQLAlchemy – kein REST-Aufruf.
AIRFLOW_DB_USER = os.environ.get("POSTGRES_USER", "airflow")
AIRFLOW_DB_PASS = os.environ.get("POSTGRES_PASSWORD", "airflow123")


# ── HTTP-Hilfsfunktionen ──────────────────────────────────────────────────────

def om_login() -> str:
    """Gibt einen gültigen Admin-Bearer-Token zurück."""
    # OM erwartet das Passwort Base64-kodiert im Login-Request
    password_b64 = base64.b64encode(b"admin").decode()
    req = urllib.request.Request(
        f"{OM_URL}/users/login",
        data=json.dumps({
            "email": "admin@open-metadata.org",
            "password": password_b64,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req).read())["accessToken"]


def api_get(token: str, path: str) -> dict | None:
    """GET-Request, gibt None bei 404 zurück."""
    req = urllib.request.Request(
        f"{OM_URL}/{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def api_post(token: str, path: str, data: dict) -> dict:
    """POST-Request. Returns {} when the response body is empty (e.g. trigger/deploy)."""
    req = urllib.request.Request(
        f"{OM_URL}/{path}",
        data=json.dumps(data).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    body = urllib.request.urlopen(req).read()
    return json.loads(body) if body.strip() else {}


# ── Service- und Pipeline-Helfer ──────────────────────────────────────────────

def ensure_service(token: str, service_type_path: str, payload: dict) -> tuple[str, str]:
    """
    Legt einen OM-Service an wenn er nicht existiert.
    Gibt (uuid, 'existing'|'created') zurück.
    service_type_path: z.B. 'databaseServices' oder 'pipelineServices'
    """
    name = payload["name"]
    existing = api_get(token, f"services/{service_type_path}/name/{name}")
    if existing:
        return existing["id"], "existing"
    created = api_post(token, f"services/{service_type_path}", payload)
    return created["id"], "created"


def ensure_pipeline(
    token: str,
    fqn: str,
    service_id: str,
    service_ref_type: str,
    payload: dict,
) -> tuple[str, str]:
    """
    Legt eine Ingestion-Pipeline an wenn sie noch nicht existiert.
    fqn:              Fully Qualified Name, z.B. 'lakehouse_trino.trino_metadata'
    service_ref_type: 'databaseService' | 'pipelineService'
    Gibt (uuid, 'existing'|'created') zurück.
    """
    existing = api_get(token, f"services/ingestionPipelines/name/{fqn}")
    if existing:
        return existing["id"], "existing"
    # Service-Referenz erst beim Anlegen setzen (nicht im Template)
    payload_with_svc = {**payload, "service": {"id": service_id, "type": service_ref_type}}
    created = api_post(token, "services/ingestionPipelines", payload_with_svc)
    return created["id"], "created"


def deploy_pipeline(token: str, pipeline_id: str, label: str) -> bool:
    """Deployt eine Pipeline zum Airflow-Backend (nötig nach Neuanlage).
    Gibt True zurück wenn erfolgreich."""
    try:
        api_post(token, f"services/ingestionPipelines/deploy/{pipeline_id}", {})
        print(f"    ✓ {label} deployed")
        return True
    except urllib.error.HTTPError as e:
        print(f"    ⚠ {label}: Deploy fehlgeschlagen (HTTP {e.code})")
        return False


def trigger_pipeline(token: str, pipeline_id: str, label: str) -> None:
    """Startet eine Ingestion-Pipeline sofort."""
    try:
        api_post(token, f"services/ingestionPipelines/trigger/{pipeline_id}", {})
        print(f"    ✓ {label} gestartet")
    except urllib.error.HTTPError as e:
        # 409 = Pipeline läuft bereits, kein Fehler
        if e.code == 409:
            print(f"    ℹ {label} läuft bereits")
        else:
            print(f"    ⚠ {label}: Trigger fehlgeschlagen (HTTP {e.code}) – läuft beim nächsten Schedule")


def trigger_search_indexing(token: str) -> None:
    """Baut den Elasticsearch-Suchindex aus der OM-Datenbank neu auf."""
    try:
        api_post(token, "apps/trigger/SearchIndexingApplication", {})
        print("    ✓ SearchIndexingApplication gestartet")
    except urllib.error.HTTPError as e:
        print(f"    ⚠ SearchIndexing fehlgeschlagen (HTTP {e.code})")


# ── Haupt-Logik ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Logging in to OpenMetadata...")
    try:
        token = om_login()
    except Exception as e:
        print(f"  ✗ Login fehlgeschlagen: {e}")
        sys.exit(1)
    print("  ✓ Login OK\n")

    # ── 1. Trino Database Service ─────────────────────────────────────────────
    print("── Trino Database Service ──")
    trino_id, status = ensure_service(token, "databaseServices", {
        "name": TRINO_SERVICE_NAME,
        "displayName": "Lakehouse Trino",
        "serviceType": "Trino",
        "connection": {
            "config": {
                "type": "Trino",
                "hostPort": "trino:8080",
                # kein Passwort – Trino akzeptiert Username ohne Auth im Docker-Netz
                "username": "admin",
                "catalog": "iceberg",
            }
        },
    })
    print(f"  Service '{TRINO_SERVICE_NAME}': {status}  [id={trino_id[:8]}...]")

    trino_pipe_id, status = ensure_pipeline(
        token,
        fqn=f"{TRINO_SERVICE_NAME}.lakehouse_trino_metadata_ingestion",
        service_id=trino_id,
        service_ref_type="databaseService",
        payload={
            "name": "lakehouse_trino_metadata_ingestion",
            "displayName": "Trino Metadata Ingestion",
            "pipelineType": "metadata",
            "sourceConfig": {
                "config": {
                    "type": "DatabaseMetadata",
                    "markDeletedTables": False,
                    "includeTables": True,
                    "includeViews": True,
                }
            },
            "airflowConfig": {
                "scheduleInterval": "0 3 * * *",
                "pausePipeline": False,
                "concurrency": 1,
                "retries": 1,
            },
        },
    )
    print(f"  Metadata-Pipeline: {status}  [id={trino_pipe_id[:8]}...]")

    # ── 2. Airflow Pipeline Service ───────────────────────────────────────────
    print("\n── Airflow Pipeline Service ──")
    airflow_id, status = ensure_service(token, "pipelineServices", {
        "name": AIRFLOW_SERVICE_NAME,
        "displayName": "Lakehouse Airflow",
        "serviceType": "Airflow",
        "connection": {
            "config": {
                "type": "Airflow",
                # hostPort ist nur für Metadaten/UI-Links – OM liest die DB direkt
                "hostPort": "http://airflow:8080",
                "numberOfStatus": 10,
                # Direktverbindung zur Airflow-Postgres-DB via SQLAlchemy
                # (kein REST-API-Aufruf; Airflow-DB liegt im selben Docker-Netz)
                "connection": {
                    "type": "Postgres",
                    "scheme": "postgresql+psycopg2",
                    "username": AIRFLOW_DB_USER,
                    "authType": {"password": AIRFLOW_DB_PASS},
                    "hostPort": "postgres:5432",
                    "database": "airflow",
                },
            }
        },
    })
    print(f"  Service '{AIRFLOW_SERVICE_NAME}': {status}  [id={airflow_id[:8]}...]")

    airflow_pipe_id, status = ensure_pipeline(
        token,
        fqn=f"{AIRFLOW_SERVICE_NAME}.lakehouse_airflow_metadata_ingestion",
        service_id=airflow_id,
        service_ref_type="pipelineService",
        payload={
            "name": "lakehouse_airflow_metadata_ingestion",
            "displayName": "Airflow Metadata Ingestion",
            "pipelineType": "metadata",
            "sourceConfig": {
                "config": {
                    "type": "PipelineMetadata",
                    "includeLineage": True,
                }
            },
            "airflowConfig": {
                "scheduleInterval": "0 2 * * *",
                "pausePipeline": False,
                "concurrency": 1,
                "retries": 1,
            },
        },
    )
    print(f"  Metadata-Pipeline: {status}  [id={airflow_pipe_id[:8]}...]")

    # ── 3. dbt-Pipeline (am Trino-Service angehängt) ──────────────────────────
    print("\n── dbt Ingestion Pipeline ──")
    dbt_pipe_id, status = ensure_pipeline(
        token,
        fqn=f"{TRINO_SERVICE_NAME}.lakehouse_dbt_metadata_ingestion",
        service_id=trino_id,
        service_ref_type="databaseService",
        payload={
            "name": "lakehouse_dbt_metadata_ingestion",
            "displayName": "dbt Metadata Ingestion",
            "pipelineType": "dbt",
            "sourceConfig": {
                "config": {
                    "type": "DBT",
                    "dbtConfigSource": {
                        "dbtConfigType": "local",
                        # Pfade im openmetadata-ingestion-Container
                        # (Volume-Mount: ./dbt/target → /opt/dbt/target)
                        "dbtManifestFilePath": "/opt/dbt/target/manifest.json",
                        "dbtCatalogFilePath": "/opt/dbt/target/catalog.json",
                        "dbtRunResultsFilePath": "/opt/dbt/target/run_results.json",
                    },
                    "dbtUpdateDescriptions": True,
                    "includeTags": True,
                }
            },
            "airflowConfig": {
                "scheduleInterval": "0 4 * * *",
                "pausePipeline": False,
                "concurrency": 1,
                "retries": 1,
            },
        },
    )
    print(f"  dbt-Pipeline: {status}  [id={dbt_pipe_id[:8]}...]")

    # ── 4. Alle Pipelines deployen und sofort triggern ────────────────────────
    # Deploy synct die Pipeline zum Airflow-Backend – ohne Deploy schlägt Trigger
    # mit HTTP 400 fehl (Pipeline existiert in OM, aber noch nicht in Airflow).
    print("\n── Ingestion-Pipelines deployen & triggern ──")
    for pipe_id, label in [
        (trino_pipe_id,   "Trino Metadata"),
        (airflow_pipe_id, "Airflow Metadata"),
        (dbt_pipe_id,     "dbt Metadata"),
    ]:
        deploy_pipeline(token, pipe_id, label)
        trigger_pipeline(token, pipe_id, label)

    # ── 5. Elasticsearch-Suchindex aufbauen ───────────────────────────────────
    print("\n── Elasticsearch-Suchindex ──")
    trigger_search_indexing(token)

    print("\n✓ Setup abgeschlossen.")
    print("  Ingestion läuft im Hintergrund – Katalog in ~1-2 Minuten befüllt.")
    print("  Status prüfen: OM-UI → Settings → Services → [Service] → Ingestions")
