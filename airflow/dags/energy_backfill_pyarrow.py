"""
DAG: energy_backfill_pyarrow
Liest alle vorhandenen JSON-Dateien aus der Landing Zone und schreibt sie via
PyArrow + PyIceberg direkt in iceberg.raw.energy_price_hourly.

Kein Trino INSERT → kein Token-Limit. Trino wird nur für den Lade-Check
(SELECT) verwendet, alle Writes gehen direkt über PyIceberg → Nessie REST.

Trigger: manuell (schedule=None).

Airflow Variables:
  MINIO_ENDPOINT   http://minio:9000
  MINIO_ACCESS_KEY minioadmin
  MINIO_SECRET_KEY minioadmin123
  NESSIE_URI       http://nessie:19120/iceberg
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date as date_type, datetime, timedelta, timezone

from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook


BUCKET = "lakehouse"
LANDING_PREFIX = "landing/json/energy_prices"
NAMESPACE = "raw"
TABLE_NAME = "energy_price_hourly"
TRINO_TABLE = f"iceberg.{NAMESPACE}.{TABLE_NAME}"

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


def _get_iceberg_catalog():
    from pyiceberg.catalog import load_catalog

    return load_catalog(
        "nessie",
        **{
            "type": "rest",
            "uri": Variable.get("NESSIE_URI", default_var="http://nessie:19120/iceberg"),
            "s3.endpoint": Variable.get("MINIO_ENDPOINT", default_var="http://minio:9000"),
            "s3.access-key-id": Variable.get("MINIO_ACCESS_KEY", default_var="minioadmin"),
            "s3.secret-access-key": Variable.get("MINIO_SECRET_KEY", default_var="minioadmin123"),
            "s3.path-style-access": "true",
            "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
        },
    )


def _get_or_create_table(catalog):
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.transforms import IdentityTransform
    from pyiceberg.types import (
        DateType,
        DoubleType,
        IntegerType,
        NestedField,
        StringType,
        TimestampType,
    )

    schema = Schema(
        NestedField(1, "timestamp", TimestampType(), required=False),
        NestedField(2, "date_key", DateType(), required=False),
        NestedField(3, "hour", IntegerType(), required=False),
        NestedField(4, "bidding_zone", StringType(), required=False),
        NestedField(5, "price_eur_mwh", DoubleType(), required=False),
        NestedField(6, "_source_file", StringType(), required=False),
        NestedField(7, "_loaded_at", TimestampType(), required=False),
    )
    partition_spec = PartitionSpec(
        PartitionField(source_id=2, field_id=1000, transform=IdentityTransform(), name="date_key")
    )

    identifier = (NAMESPACE, TABLE_NAME)
    if not catalog.table_exists(identifier):
        existing_namespaces = [ns[0] for ns in catalog.list_namespaces()]
        if NAMESPACE not in existing_namespaces:
            catalog.create_namespace(NAMESPACE)
        return catalog.create_table(identifier, schema=schema, partition_spec=partition_spec)
    try:
        return catalog.load_table(identifier)
    except Exception as e:
        print(f"⚠️  load_table fehlgeschlagen ({e}), Tabelle wird neu erstellt.")
        catalog.drop_table(identifier)
        return catalog.create_table(identifier, schema=schema, partition_spec=partition_spec)


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
    Liest Landing-JSONs, baut pro Tag eine PyArrow-Tabelle und schreibt sie
    via PyIceberg direkt in den Nessie REST Catalog. Kein Trino INSERT.
    Idempotent: overwrite pro Tag (retry-sicher).
    """
    import pyarrow as pa
    from pyiceberg.expressions import EqualTo

    keys = context["ti"].xcom_pull(key="landing_keys", task_ids="list_landing_files")
    if not keys:
        print("⚠️  Keine Dateien gefunden – Abbruch.")
        return

    # Bereits geladene Tage via Trino ermitteln (nur SELECT, kein Token-Problem)
    hook = TrinoHook(trino_conn_id="trino_default")
    try:
        loaded_rows = hook.get_records(
            f"SELECT DISTINCT CAST(date_key AS VARCHAR) FROM {TRINO_TABLE}"
        )
        loaded_days = {str(row[0])[:10] for row in loaded_rows}
    except Exception:
        loaded_days = set()
    print(f"ℹ️  {len(loaded_days)} Tage bereits in Raw-Tabelle vorhanden.")

    catalog = _get_iceberg_catalog()
    table = _get_or_create_table(catalog)
    arrow_schema = table.schema().as_arrow()

    # Dateien nach Monat gruppieren (Key-Format: landing/json/energy_prices/YYYY-MM-DD.json)
    by_month: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        filename = key.split("/")[-1]
        by_month[filename[:7]].append(key)

    s3 = _get_minio_client()
    loaded_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    total_months = len(by_month)

    for idx, (month, month_keys) in enumerate(sorted(by_month.items()), 1):
        keys_to_load = [k for k in month_keys if k.split("/")[-1][:10] not in loaded_days]
        if not keys_to_load:
            print(f"⏭️  [{idx}/{total_months}] {month} – alle Tage bereits geladen.")
            continue

        month_rows = 0
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

            day_date = date_type.fromisoformat(day)
            timestamps, date_keys, hours, zones, price_vals, sources, loaded_ats = (
                [], [], [], [], [], [], []
            )

            for i, unix_ts in enumerate(unix_seconds):
                dt = datetime.utcfromtimestamp(unix_ts)
                timestamps.append(dt)
                date_keys.append(day_date)
                hours.append(dt.hour)
                zones.append(bidding_zone)
                price_vals.append(prices[i] if i < len(prices) else None)
                sources.append(source_file)
                loaded_ats.append(loaded_at)

            arrow_table = pa.table(
                {
                    "timestamp": pa.array(timestamps, type=pa.timestamp("us")),
                    "date_key": pa.array(date_keys, type=pa.date32()),
                    "hour": pa.array(hours, type=pa.int32()),
                    "bidding_zone": pa.array(zones, type=pa.large_utf8()),
                    "price_eur_mwh": pa.array(price_vals, type=pa.float64()),
                    "_source_file": pa.array(sources, type=pa.large_utf8()),
                    "_loaded_at": pa.array(loaded_ats, type=pa.timestamp("us")),
                },
                schema=arrow_schema,
            )

            # overwrite löscht bestehende Dateien für diesen Tag und schreibt neu
            # → idempotent bei Airflow-Retries
            table.overwrite(arrow_table, overwrite_filter=EqualTo("date_key", day_date))
            month_rows += len(timestamps)
            print(f"  ✅ {day} – {len(timestamps)} Rows.")

        skipped = len(month_keys) - len(keys_to_load)
        suffix = f", {skipped} bereits vorhanden" if skipped else ""
        print(
            f"✅ [{idx}/{total_months}] {month} – {month_rows} Rows "
            f"({len(keys_to_load)} Tage neu{suffix})."
        )

    print("🎉 Backfill abgeschlossen.")


with DAG(
    dag_id="energy_backfill_pyarrow",
    description="Backfill: Landing Zone JSON → iceberg.raw.energy_price_hourly via PyArrow/PyIceberg (kein Trino INSERT)",
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
