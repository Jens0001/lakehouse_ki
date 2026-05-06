"""
DAG: weather_backfill_landing_to_raw
Liest alle vorhandenen JSON-Dateien aus der Landing Zone und schreibt sie in
iceberg.raw.weather_hourly. Kein API-Aufruf – nur Landing → Raw.

Trigger: manuell (schedule=None), einmalig ausführen.

Pro Monat ein einziger INSERT → minimale Snapshot-Anzahl in Iceberg (~75 Snapshots
für 6 Jahre statt 2.000+).

Airflow Variables (dieselben wie open_meteo_to_raw):
  MINIO_ENDPOINT        http://minio:9000
  MINIO_ACCESS_KEY      minioadmin
  MINIO_SECRET_KEY      minioadmin123
  WEATHER_LOCATION_KEY  z.B. berlin-reinickendorf  (für Skip-Check)
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta

from airflow import DAG
from airflow.datasets import Dataset
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook

OUTLET_WEATHER_HOURLY = Dataset("trino://trino:8080/iceberg/raw/weather_hourly")


BUCKET = "lakehouse"
LANDING_PREFIX = "landing/json/weather"
RAW_TABLE = "iceberg.raw.weather_hourly"

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
    iceberg.raw.weather_hourly. Idempotent: DELETE pro Tag vor dem INSERT.
    Überspringt Tage, die bereits geladen sind.
    """
    keys = context["ti"].xcom_pull(key="landing_keys", task_ids="list_landing_files")
    if not keys:
        print("⚠️  Keine Dateien gefunden – Abbruch.")
        return

    hook = TrinoHook(trino_conn_id="trino_default")

    # Tabelle anlegen, falls nicht vorhanden (idempotent)
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
            timestamp            TIMESTAMP,
            date_key             DATE,
            hour                 INTEGER,
            location_key         VARCHAR,
            latitude             DOUBLE,
            longitude            DOUBLE,
            temperature_2m       DOUBLE,
            apparent_temperature DOUBLE,
            precipitation        DOUBLE,
            windspeed_10m        DOUBLE,
            weathercode          INTEGER,
            relative_humidity_2m DOUBLE,
            _source_file         VARCHAR,
            _loaded_at           TIMESTAMP
        )
        WITH (
            format = 'PARQUET',
            partitioning = ARRAY['date_key']
        )
    """)

    # Bereits geladene Tage ermitteln (Skip-Optimierung auf Tages-Ebene)
    loaded_rows = hook.get_records(
        f"SELECT DISTINCT CAST(date_key AS VARCHAR) FROM {RAW_TABLE}"
    )
    loaded_days = {str(row[0])[:10] for row in loaded_rows}  # 'YYYY-MM-DD'
    print(f"ℹ️  {len(loaded_days)} Tage bereits in Raw-Tabelle vorhanden.")

    # Dateien nach Monat gruppieren (Key-Format: landing/json/weather/YYYY-MM-DD.json)
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
        location_key = None

        for key in sorted(keys_to_load):
            day = key.split("/")[-1][:10]
            obj = s3.get_object(Bucket=BUCKET, Key=key)
            data = json.loads(obj["Body"].read())

            hourly = data["hourly"]
            location_key = data["_meta"]["location_key"]
            lat = data["latitude"]
            lon = data["longitude"]
            ds = data["_meta"]["fetch_date"]
            source_file = f"s3://{BUCKET}/{key}"

            timestamps = hourly["time"]
            temperatures = hourly.get("temperature_2m", [None] * len(timestamps))
            apparent_temps = hourly.get("apparent_temperature", [None] * len(timestamps))
            precipitations = hourly.get("precipitation", [None] * len(timestamps))
            windspeeds = hourly.get("windspeed_10m", [None] * len(timestamps))
            weathercodes = hourly.get("weathercode", [None] * len(timestamps))
            humidities = hourly.get("relative_humidity_2m", [None] * len(timestamps))

            for i, ts in enumerate(timestamps):
                dt = datetime.fromisoformat(ts)

                def fmt(v):
                    return "NULL" if v is None else str(v)

                rows.append(
                    f"(TIMESTAMP '{dt.strftime('%Y-%m-%d %H:%M:%S')}', "
                    f"DATE '{ds}', "
                    f"{dt.hour}, "
                    f"'{location_key}', "
                    f"{lat}, "
                    f"{lon}, "
                    f"{fmt(temperatures[i])}, "
                    f"{fmt(apparent_temps[i])}, "
                    f"{fmt(precipitations[i])}, "
                    f"{fmt(windspeeds[i])}, "
                    f"{fmt(weathercodes[i])}, "
                    f"{fmt(humidities[i])}, "
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
              AND location_key = '{location_key}'
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
    dag_id="weather_backfill_landing_to_raw",
    description="Einmaliger Backfill: Landing Zone JSON → iceberg.raw.weather_hourly (kein API-Aufruf)",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["weather", "raw", "backfill"],
) as dag:

    t1 = PythonOperator(
        task_id="list_landing_files",
        python_callable=list_landing_files,
    )

    t2 = PythonOperator(
        task_id="backfill_to_raw",
        python_callable=backfill_to_raw,
        outlets=[OUTLET_WEATHER_HOURLY],
    )

    t1 >> t2
