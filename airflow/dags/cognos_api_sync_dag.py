"""
DAG: cognos_api_sync
Tägliche Ingestion von Cognos Analytics Modulen und Dashboards nach OpenMetadata
via direktem REST-API-Zugriff auf Cognos. Kein manueller JSON-Export erforderlich.

Ablauf:
  1. ingest_modules   – legt OM Dashboard Data Models an und setzt Lineage-Kanten
                        von den physischen Trino-Tabellen zu den Data Models.
  2. ingest_dashboards – legt OM Dashboards + Charts an und verknüpft sie mit den
                         Data Models aus Schritt 1 (über das dataModels[]-Feld).

Konfiguration (alle Werte aus .env via docker-compose.yml):
  om_bot_token      – Airflow Variable; aus OPENMETADATA_INGESTION_BOT_TOKEN, wird von
                      start.sh automatisch befüllt
  COGNOS_USERNAME   – Container-Env-Variable; leer lassen wenn Cognos anonym erlaubt
  COGNOS_PASSWORD   – Container-Env-Variable; leer lassen wenn Cognos anonym erlaubt

Voraussetzungen:
  - Cognos Analytics erreichbar unter COGNOS_URL (192.168.178.149:9300).
  - Trino-Service 'lakehouse_trino' in OM bereits crawled (für Lineage-Kanten).
  - start.sh wurde nach Stack-Start ausgeführt (setzt om_bot_token).
"""

from datetime import datetime

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

SCRIPT_PATH = "/opt/airflow/scripts/cognos_api_sync.py"
COGNOS_URL  = "http://192.168.178.149:9300/api/v1"
OM_URL      = "http://openmetadata-server:8585/api"
EXPORT_DIR  = "/opt/airflow/cognos/api_exports"

# Umgebungsvariablen für beide Tasks.
# OM_TOKEN: Airflow Variable "om_bot_token" (aus OPENMETADATA_INGESTION_BOT_TOKEN, gesetzt von start.sh)
# COGNOS_USERNAME / COGNOS_PASSWORD: direkt aus Container-Env (gesetzt via docker-compose aus .env)
_ENV = (
    f'COGNOS_URL="{COGNOS_URL}" '
    f'OM_URL="{OM_URL}" '
    f'EXPORT_DIR="{EXPORT_DIR}" '
    'OM_TOKEN="{{ var.value.om_bot_token }}"'
)

with DAG(
    dag_id="cognos_api_sync",
    start_date=datetime(2026, 5, 6),
    schedule="0 5 * * *",  # Täglich 05:00 UTC – nach Trino (03:00) und dbt (04:00)
    catchup=False,
    tags=["cognos", "openmetadata", "lineage"],
    doc_md=__doc__,
) as dag:

    # Datenmodule zuerst – erstellt Data Models, auf die Dashboards verweisen.
    ingest_modules = BashOperator(
        task_id="ingest_modules",
        bash_command=f"{_ENV} python3 {SCRIPT_PATH} --ingest-om --modules-only",
    )

    # Dashboards danach – referenziert die Data Models aus ingest_modules.
    ingest_dashboards = BashOperator(
        task_id="ingest_dashboards",
        bash_command=f"{_ENV} python3 {SCRIPT_PATH} --ingest-om --dashboards-only",
    )

    ingest_modules >> ingest_dashboards
