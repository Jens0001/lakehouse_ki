from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator
from datetime import datetime

with DAG(
    dag_id="dbt_run_lakehouse_ki",
    start_date=datetime(2026, 3, 19),
    schedule="@daily",
    catchup=False,
    tags=["dbt", "lakehouse"],
) as dag:

    dbt_deps = BashOperator(
        task_id="dbt_deps",
        bash_command="cd /opt/dbt && dbt deps --profiles-dir /opt/dbt",
    )

    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command="cd /opt/dbt && dbt run --profiles-dir /opt/dbt",
    )

    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command="cd /opt/dbt && dbt test --profiles-dir /opt/dbt",
    )

    # dbt docs generate erzeugt catalog.json + manifest.json im target/-Verzeichnis.
    # Diese Artefakte werden vom openmetadata-ingestion-Container täglich um 04:00 UTC
    # gelesen, um Spaltentypen, Beschreibungen und Tests in OpenMetadata zu aktualisieren.
    # Das Volume /opt/dbt/target ist in den OM-Ingestion-Container gemountet (docker-compose.yml).
    dbt_docs_generate = BashOperator(
        task_id="dbt_docs_generate",
        bash_command="cd /opt/dbt && dbt docs generate --profiles-dir /opt/dbt",
    )

    dbt_deps >> dbt_run >> dbt_test >> dbt_docs_generate
