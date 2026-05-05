"""
DAG: energy_backfill_landing_to_raw
Liest alle vorhandenen JSON-Dateien aus der Landing Zone und schreibt sie in
iceberg.raw.energy_price_hourly. Kein API-Aufruf – nur Landing → Raw.

Trigger: manuell (schedule=None), einmalig ausführen.

Pro Monat ein einziger INSERT → minimale Snapshot-Anzahl in Iceberg (~90 Snapshots
für ~8 Jahre statt 2.700+).

Airflow Variables (dieselben wie energy_charts_to_raw):
  MINIO_ENDPOINT        http://minio:9000
  MINIO_ACCESS_KEY      minioadmin
  MINIO_SECRET_KEY      minioadmin123
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook


BUCKET = "lakehouse"
LANDING_PREFIX = "landing/json/energy_prices"
RAW_TABLE = "iceberg.raw.energy_price_hourly"

DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def _get_minio_client():
    import boto3
    from botocore.client import Config

    return boto3.client(
        "s3",
        endpoint_url=Variable.get("MINIO_ENDPOINT", default_var="http://minio:9000"),
        aws_access_key_id=Variable.get("MINIO_ACCESS_KEY", default_var="minioadmin"),
        aws_secret_access_key=Variable.get("MINIO_SECRET_KEY", default_var="minioadmin123"),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def list_landing_files(**context):
    """Listet alle JSON-Keys in der Landing Zone und gibt sie sortiert per XCom zurück."""
    s3 = _get_minio_client()
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=LANDING_PREFIX + "/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                keys.append(key)
    keys.sort()
    print(f"📋 {len(keys)} JSON-Dateien in Landing Zone gefunden.")
    context["ti"].xcom_push(key="landing_keys", value=keys)


def backfill_to_raw(**context):
    """
    Liest alle Landing-JSONs, gruppiert nach Monat und schreibt fehlende Tage in
    iceberg.raw.energy_price_hourly. Idempotent: DELETE pro Tag vor dem INSERT.
    Überspringt Tage, die bereits geladen sind.

    Preise können NULL oder negativ sein (Überangebot Erneuerbarer) – werden 1:1 übernommen.
    """
    keys = context["ti"].xcom_pull(key="landing_keys", task_ids="list_landing_files")
    if not keys:
        print("⚠️  Keine Dateien gefunden – Abbruch.")
        return

    hook = TrinoHook(trino_conn_id="trino_default")

    # Tabelle anlegen, falls nicht vorhanden (idempotent)
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
            timestamp       TIMESTAMP,
            date_key        DATE,
            hour            INTEGER,
            bidding_zone    VARCHAR,
            price_eur_mwh   DOUBLE,
            _source_file    VARCHAR,
            _loaded_at      TIMESTAMP
        )
        WITH (
            format       = 'PARQUET',
            partitioning = ARRAY['date_key']
        )
    """)

    # Bereits geladene Tage ermitteln (Skip-Optimierung auf Tages-Ebene)
    loaded_rows = hook.get_records(
        f"SELECT DISTINCT CAST(date_key AS VARCHAR) FROM {RAW_TABLE}"
    )
    loaded_days = {str(row[0])[:10] for row in loaded_rows}  # 'YYYY-MM-DD'
    print(f"ℹ️  {len(loaded_days)} Tage bereits in Raw-Tabelle vorhanden.")

    # Dateien nach Monat gruppieren (Key-Format: landing/json/energy_prices/YYYY-MM-DD.json)
    by_month: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        filename = key.split("/")[-1]          # YYYY-MM-DD.json
        month = filename[:7]                   # YYYY-MM
        by_month[month].append(key)

    s3 = _get_minio_client()
    loaded_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    total_months = len(by_month)

    for idx, (month, month_keys) in enumerate(sorted(by_month.items()), 1):
        keys_to_load = [k for k in month_keys if k.split("/")[-1][:10] not in loaded_days]
        if not keys_to_load:
            print(f"⏭️  [{idx}/{total_months}] {month} – alle Tage bereits geladen, übersprungen.")
            continue

        rows = []
        days_in_batch: set[str] = set()
        bidding_zone = None

        for key in sorted(keys_to_load):
            day = key.split("/")[-1][:10]
            obj = s3.get_object(Bucket=BUCKET, Key=key)
            data = json.loads(obj["Body"].read())

            unix_seconds = data.get("unix_seconds", [])
            prices = data.get("price", [])
            bidding_zone = data["_meta"]["bidding_zone"]
            ds = data["_meta"]["fetch_date"]
            source_file = f"s3://{BUCKET}/{key}"

            if not unix_seconds:
                print(f"  ⚠️  {ds} – leere API-Response, übersprungen.")
                continue

            for i, unix_ts in enumerate(unix_seconds):
                dt = datetime.utcfromtimestamp(unix_ts)
                price = (
                    "NULL"
                    if i >= len(prices) or prices[i] is None
                    else str(prices[i])
                )
                rows.append(
                    f"(TIMESTAMP '{dt.strftime('%Y-%m-%d %H:%M:%S')}', "
                    f"DATE '{ds}', "
                    f"{dt.hour}, "
                    f"'{bidding_zone}', "
                    f"{price}, "
                    f"'{source_file}', "
                    f"TIMESTAMP '{loaded_at}')"
                )
            days_in_batch.add(day)

        if not rows:
            print(f"⚠️  [{idx}/{total_months}] {month} – keine Rows, übersprungen.")
            continue

        # Batched DELETE mit IN-Klausel (eine IN-Liste pro Monat, ~31 Datums-Literale)
        # Vermeidet sqlparse Token-Limit-Error bei vielen einzelnen DELETEs
        days_in_batch_sorted = sorted(days_in_batch)
        date_in_clause = ", ".join(f"DATE '{d}'" for d in days_in_batch_sorted)
        hook.run(f"""
            DELETE FROM {RAW_TABLE}
            WHERE date_key IN ({date_in_clause})
              AND bidding_zone = '{bidding_zone}'
        """)

        # Batched INSERT (100 Zeilen je Statement, um SQLParseError zu vermeiden)
        batch_size = 100
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            hook.run(f"INSERT INTO {RAW_TABLE} VALUES {', '.join(batch)}")

        skipped = len(month_keys) - len(keys_to_load)
        suffix = f", {skipped} bereits vorhanden" if skipped else ""
        print(f"✅ [{idx}/{total_months}] {month} – {len(rows)} Rows ({len(keys_to_load)} Tage neu{suffix}).")

    print("🎉 Backfill abgeschlossen.")


with DAG(
    dag_id="energy_backfill_landing_to_raw",
    description="Einmaliger Backfill: Landing Zone JSON → iceberg.raw.energy_price_hourly (kein API-Aufruf)",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["energy-charts", "prices", "raw", "backfill"],
) as dag:

    t1 = PythonOperator(
        task_id="list_landing_files",
        python_callable=list_landing_files,
    )

    t2 = PythonOperator(
        task_id="backfill_to_raw",
        python_callable=backfill_to_raw,
    )

    t1 >> t2
