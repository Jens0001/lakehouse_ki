"""
DAG: cognos_api_sync
Tägliche Ingestion von Cognos Analytics Modulen und Dashboards nach OpenMetadata
via direktem REST-API-Zugriff auf Cognos. Kein manueller JSON-Export erforderlich.

Ablauf:
  1. ingest_modules   – legt OM Dashboard Data Models an und setzt Lineage-Kanten
                        von den physischen Trino-Tabellen zu den Data Models.
  2. ingest_dashboards – legt OM Dashboards + Charts an und verknüpft sie mit den
                         Data Models aus Schritt 1 (über das dataModels[]-Feld).

Konfiguration – alle URLs und Credentials kommen aus dem Container-Environment
(gesetzt in docker-compose.yml aus .env), kein Hardcoding im DAG:
  COGNOS_URL        – Cognos REST API Basis-URL (aus .env)
  COGNOS_USERNAME   – Cognos-Benutzer (aus .env, leer = anonymer Zugriff)
  COGNOS_PASSWORD   – Cognos-Passwort (aus .env, leer = anonymer Zugriff)
  OM_URL            – OpenMetadata intern: http://openmetadata-server:8585/api
                      (in docker-compose.yml fix gesetzt, nicht aus .env –
                       .env OM_URL zeigt auf externe URL, die im Container nicht gilt)
  om_bot_token      – Airflow Variable; Token wird von start.sh nach Stack-Start
                      in .env geschrieben und per AIRFLOW_VAR_OM_BOT_TOKEN bereitgestellt

Voraussetzungen:
  - Cognos Analytics erreichbar unter COGNOS_URL.
  - Trino-Service 'lakehouse_trino' in OM bereits gecrawlt (für Lineage-Kanten).
  - start.sh wurde nach Stack-Start ausgeführt (befüllt om_bot_token).
"""

from datetime import datetime

from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

SCRIPT_PATH = "/opt/airflow/scripts/cognos_api_sync.py"
EXPORT_DIR  = "/opt/airflow/cognos/api_exports"

# COGNOS_URL, COGNOS_USERNAME, COGNOS_PASSWORD und OM_URL liegen bereits im
# Container-Environment (docker-compose.yml). Der BashOperator erbt sie automatisch.
# Einzig OM_TOKEN wird separat injiziert, da start.sh das Token erst nach
# Container-Start setzt und es über die Airflow Variable "om_bot_token" bereitstellt.
_ENV = f'EXPORT_DIR="{EXPORT_DIR}" OM_TOKEN="{{{{ var.value.om_bot_token }}}}"'

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
