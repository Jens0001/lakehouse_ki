from airflow import DAG
from airflow.operators.bash import BashOperator
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

    dbt_deps >> dbt_run >> dbt_test
