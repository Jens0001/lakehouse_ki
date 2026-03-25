"""
DAG: cognos_to_openmetadata
Tägliche Ingestion von Cognos Analytics JSON-Exporten (Datenmodule + Dashboards)
nach OpenMetadata. Erzeugt Dashboard Data Models, Dashboards, Charts und
Lineage-Kanten zu den physischen Trino-Tabellen.

Ablauf:
  1. Alle *.json-Dateien in /opt/airflow/cognos_exports/datamodules/ einlesen
     und als Dashboard Data Models in OpenMetadata ingesten (Spalten, Datentypen,
     Usage-Tags, Beziehungen, Drill-Hierarchien, Lineage zu Trino-Tabellen).
  2. Alle *.json-Dateien in /opt/airflow/cognos_exports/dashboards/ einlesen
     und als Dashboards + Charts in OpenMetadata ingesten (Tabs, Widgets,
     Chart-Typen, referenzierte Spalten, Lineage zu Data Models).

Voraussetzungen:
  - Cognos-JSON-Exporte müssen manuell in die entsprechenden Verzeichnisse
    gelegt werden (kein automatischer Export aus Cognos implementiert).
  - OpenMetadata muss erreichbar sein (OM_URL).
  - Bot-Token muss als Airflow Variable 'om_bot_token' hinterlegt sein.

Verzeichnisstruktur:
  /opt/airflow/cognos_exports/
    datamodules/     ← Cognos Data Module JSON-Exporte
    dashboards/      ← Cognos Dashboard JSON-Exporte
"""

from datetime import datetime

from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.bash import BashOperator

# ---------------------------------------------------------------------------
# Pfade und Konfiguration
# ---------------------------------------------------------------------------
SCRIPT_PATH = "/opt/airflow/scripts/cognos_to_openmetadata.py"
EXPORTS_BASE = "/opt/airflow/cognos_exports"
DATAMODULES_DIR = f"{EXPORTS_BASE}/datamodules"
DASHBOARDS_DIR = f"{EXPORTS_BASE}/dashboards"

# OM-Verbindung: Token aus Airflow Variable, URL aus Env oder Default
OM_URL = "http://openmetadata-server:8585/api"

with DAG(
    dag_id="cognos_to_openmetadata",
    start_date=datetime(2026, 3, 25),
    schedule="0 6 * * *",  # Täglich um 06:00 UTC
    catchup=False,
    tags=["cognos", "openmetadata", "lineage"],
    doc_md=__doc__,
) as dag:

    # Task 1: Datenmodule zuerst – sie erstellen die Data Models,
    # auf die Dashboards per Lineage referenzieren.
    ingest_datamodules = BashOperator(
        task_id="ingest_datamodules",
        bash_command=(
            f'export OM_URL="{OM_URL}" && '
            f'export OM_TOKEN="$(python3 -c '
            f""""from airflow.models import Variable; print(Variable.get('om_bot_token'))")" && """
            f"for f in {DATAMODULES_DIR}/*.json; do "
            f'  [ -f "$f" ] && python3 {SCRIPT_PATH} "$f" || true; '
            f"done"
        ),
    )

    # Task 2: Dashboards – referenzieren die Data Models aus Task 1
    # über den Datenmodul-Namen (dataSources.sources[].name).
    ingest_dashboards = BashOperator(
        task_id="ingest_dashboards",
        bash_command=(
            f'export OM_URL="{OM_URL}" && '
            f'export OM_TOKEN="$(python3 -c '
            f""""from airflow.models import Variable; print(Variable.get('om_bot_token'))")" && """
            f"for f in {DASHBOARDS_DIR}/*.json; do "
            f'  [ -f "$f" ] && python3 {SCRIPT_PATH} --dashboard "$f" || true; '
            f"done"
        ),
    )

    # Datenmodule müssen vor Dashboards ingestiert werden,
    # da Dashboards per name-Match auf Data Models verweisen.
    ingest_datamodules >> ingest_dashboards
