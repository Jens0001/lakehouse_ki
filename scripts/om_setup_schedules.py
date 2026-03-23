"""
om_setup_schedules.py – OpenMetadata Ingestion-Schedules konfigurieren

Setzt die Cron-Schedules für alle drei OM-Ingestion-Pipelines:
  - Trino:   täglich 03:00 UTC  (Tabellen & Schemas)
  - Airflow: täglich 02:00 UTC  (bereits gesetzt beim Anlegen, wird hier verifiziert)
  - dbt:     täglich 04:00 UTC  (Modelle, Tests, Lineage)

Dieses Skript ist idempotent: kann jederzeit erneut ausgeführt werden, um
Schedules zurückzusetzen oder zu ändern.

Ausführung:
    python3 scripts/om_setup_schedules.py

Voraussetzung: Stack läuft (openmetadata-server auf localhost:8585)
"""
import json
import base64
import urllib.request
import urllib.error

OM_URL = "http://localhost:8585/api/v1"

# Pipeline-IDs (einmalig beim Anlegen vergeben, stabil über Restarts)
TRINO_PIPELINE_ID   = "d81fcfc0-d439-4cf7-9088-15fba2aa577a"
AIRFLOW_PIPELINE_ID = "97bed354-3eb3-406d-9c50-7e193024257b"

# Service-ID des Trino-Services (für dbt-Pipeline-Anlage falls fehlend)
TRINO_SERVICE_ID = "462416d7-a94f-4861-839c-bab23b302bfd"


def om_login(url: str) -> str:
    """Gibt einen gültigen Bearer-Token zurück."""
    # Passwort muss Base64-encodiert übertragen werden
    password_b64 = base64.b64encode(b"admin").decode()
    req = urllib.request.Request(
        f"{url}/users/login",
        data=json.dumps({"email": "admin@open-metadata.org", "password": password_b64}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req).read())["accessToken"]


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


def ensure_dbt_pipeline(token: str) -> str:
    """Legt die dbt-Ingestion-Pipeline an falls nicht vorhanden, gibt Pipeline-ID zurück."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Prüfen ob schon vorhanden
    try:
        req = urllib.request.Request(
            f"{OM_URL}/services/ingestionPipelines/name/lakehouse_trino.lakehouse_dbt_metadata_ingestion",
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
        "service": {"id": TRINO_SERVICE_ID, "type": "databaseService"},
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

    # 1. Trino-Schedule
    sched = patch_schedule(token, TRINO_PIPELINE_ID, "0 3 * * *")
    print(f"Trino  schedule: {sched}  (tägl. 03:00 UTC)")

    # 2. Airflow-Schedule verifizieren (wurde beim Anlegen gesetzt)
    sched = patch_schedule(token, AIRFLOW_PIPELINE_ID, "0 2 * * *")
    print(f"Airflow schedule: {sched}  (tägl. 02:00 UTC)")

    # 3. dbt-Pipeline sicherstellen + Schedule setzen
    dbt_id, status = ensure_dbt_pipeline(token)
    sched = patch_schedule(token, dbt_id, "0 4 * * *")
    print(f"dbt    schedule: {sched}  (tägl. 04:00 UTC)  [{status}]")

    print("\nAlle Schedules konfiguriert.")
    print("Ingestion-Reihenfolge: Airflow 02:00 → Trino 03:00 → dbt 04:00 UTC")
