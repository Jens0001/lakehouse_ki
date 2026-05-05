"""
om_setup_schedules.py – OpenMetadata Ingestion-Schedules konfigurieren

Setzt die Cron-Schedules für alle drei OM-Ingestion-Pipelines:
  - Trino:   täglich 03:00 UTC  (Tabellen & Schemas)
  - Airflow: täglich 02:00 UTC  (DAG-Strukturen)
  - dbt:     täglich 04:00 UTC  (Modelle, Tests, Lineage)

IDs werden dynamisch per API abgefragt – keine hartkodierten UUIDs.
Das Skript ist idempotent und übersteht docker compose down -v.

Ausführung:
    python3 scripts/om_setup_schedules.py

Voraussetzung: Stack läuft (openmetadata-server auf localhost:8585)
               Trino- und Airflow-Connector bereits in OM angelegt.
"""
import json
import base64
import urllib.request
import urllib.error
import sys

OM_URL = "http://localhost:8585/api/v1"

# Service-Namen wie sie in OM angelegt wurden (Settings → Services)
TRINO_SERVICE_NAME   = "lakehouse_trino"
AIRFLOW_SERVICE_NAME = "lakehouse_airflow"


def om_login(url: str) -> str:
    """Gibt einen gültigen Bearer-Token zurück."""
    # Passwort muss Base64-encodiert übertragen werden (OM-Anforderung)
    password_b64 = base64.b64encode(b"admin").decode()
    req = urllib.request.Request(
        f"{url}/users/login",
        data=json.dumps({"email": "admin@open-metadata.org", "password": password_b64}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req).read())["accessToken"]


def get_service_id(token: str, service_name: str, service_type: str) -> str | None:
    """Gibt die UUID eines OM-Service zurück, None wenn nicht gefunden."""
    req = urllib.request.Request(
        f"{OM_URL}/services/{service_type}/name/{service_name}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        return json.loads(urllib.request.urlopen(req).read())["id"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def get_metadata_pipeline_id(token: str, service_name: str) -> str | None:
    """Findet die erste Metadata-Ingestion-Pipeline für einen Service per API-Lookup."""
    req = urllib.request.Request(
        f"{OM_URL}/services/ingestionPipelines?service={service_name}&limit=25",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        data = json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    for pipeline in data.get("data", []):
        if pipeline.get("pipelineType") == "metadata":
            return pipeline["id"]
    return None


def patch_schedule(token: str, pipeline_id: str, cron: str) -> str:
    """Setzt den Cron-Schedule einer bestehenden Ingestion-Pipeline."""
    patch = [{"op": "add", "path": "/airflowConfig/scheduleInterval", "value": cron}]
    req = urllib.request.Request(
        f"{OM_URL}/services/ingestionPipelines/{pipeline_id}",
        data=json.dumps(patch).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json-patch+json",
        },
        method="PATCH",
    )
    result = json.loads(urllib.request.urlopen(req).read())
    return result.get("airflowConfig", {}).get("scheduleInterval", "?")


def ensure_dbt_pipeline(token: str, trino_service_id: str) -> tuple[str, str]:
    """Legt die dbt-Ingestion-Pipeline an falls nicht vorhanden, gibt (id, status) zurück."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    fqn = f"{TRINO_SERVICE_NAME}.lakehouse_dbt_metadata_ingestion"

    # Prüfen ob schon vorhanden
    try:
        req = urllib.request.Request(
            f"{OM_URL}/services/ingestionPipelines/name/{fqn}",
            headers={"Authorization": f"Bearer {token}"},
        )
        existing = json.loads(urllib.request.urlopen(req).read())
        return existing["id"], "existing"
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    # Neu anlegen
    dbt_pipeline = {
        "name": "lakehouse_dbt_metadata_ingestion",
        "displayName": "dbt Metadata Ingestion",
        "pipelineType": "dbt",
        "service": {"id": trino_service_id, "type": "databaseService"},
        "sourceConfig": {
            "config": {
                "type": "DBT",
                "dbtConfigSource": {
                    "dbtConfigType": "local",
                    # Pfade im OM-Ingestion-Container (docker-compose Volume-Mount)
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
    }
    req = urllib.request.Request(
        f"{OM_URL}/services/ingestionPipelines",
        data=json.dumps(dbt_pipeline).encode(),
        headers=headers,
        method="POST",
    )
    created = json.loads(urllib.request.urlopen(req).read())
    return created["id"], "created"


if __name__ == "__main__":
    print("Logging in to OpenMetadata...")
    token = om_login(OM_URL)
    print("Login: OK")

    # 1. Trino-Metadata-Pipeline dynamisch suchen
    trino_pipeline_id = get_metadata_pipeline_id(token, TRINO_SERVICE_NAME)
    if not trino_pipeline_id:
        print(f"  ✗ Trino Metadata-Pipeline nicht gefunden (Service '{TRINO_SERVICE_NAME}' fehlt?)")
        print("    → Bitte in OM-UI anlegen: Settings → Services → Database Services → Add Service")
        sys.exit(1)
    sched = patch_schedule(token, trino_pipeline_id, "0 3 * * *")
    print(f"Trino  schedule: {sched}  (tägl. 03:00 UTC)  [id={trino_pipeline_id[:8]}...]")

    # 2. Airflow-Metadata-Pipeline dynamisch suchen
    airflow_pipeline_id = get_metadata_pipeline_id(token, AIRFLOW_SERVICE_NAME)
    if not airflow_pipeline_id:
        print(f"  ✗ Airflow Metadata-Pipeline nicht gefunden (Service '{AIRFLOW_SERVICE_NAME}' fehlt?)")
        print("    → Bitte in OM-UI anlegen: Settings → Services → Pipeline Services → Add Service")
        sys.exit(1)
    sched = patch_schedule(token, airflow_pipeline_id, "0 2 * * *")
    print(f"Airflow schedule: {sched}  (tägl. 02:00 UTC)  [id={airflow_pipeline_id[:8]}...]")

    # 3. Trino Service-ID für dbt-Pipeline-Anlage dynamisch ermitteln
    trino_service_id = get_service_id(token, TRINO_SERVICE_NAME, "databaseServices")
    if not trino_service_id:
        print(f"  ✗ Trino-Service '{TRINO_SERVICE_NAME}' nicht gefunden")
        sys.exit(1)
    dbt_id, status = ensure_dbt_pipeline(token, trino_service_id)
    sched = patch_schedule(token, dbt_id, "0 4 * * *")
    print(f"dbt    schedule: {sched}  (tägl. 04:00 UTC)  [{status}]")

    print("\nAlle Schedules konfiguriert.")
    print("Ingestion-Reihenfolge: Airflow 02:00 → Trino 03:00 → dbt 04:00 UTC")
