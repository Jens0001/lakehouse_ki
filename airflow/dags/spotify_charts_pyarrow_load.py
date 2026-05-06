"""
DAG: spotify_charts_pyarrow_load
Vollständiger Bulk-Load der Spotify Charts CSV (~26 Mio. Zeilen) via PyIceberg + PyArrow.
Schreibt direkt Parquet-Dateien in den Iceberg-Table – kein Trino INSERT,
kein sqlparse Token-Limit, kein Connection-Timeout.

Laufzeit: ~5-15 Minuten (statt Stunden mit SQL-Inserts).
RAM-Bedarf Airflow-Worker: ~8 GB.

Betriebsmodus:
  - Einmaliger Run (schedule=None)
  - Idempotent: overwrite der gesamten Tabelle

Airflow Variables:
  MINIO_ENDPOINT     http://minio:9000
  MINIO_ACCESS_KEY   minioadmin
  MINIO_SECRET_KEY   minioadmin123
  NESSIE_URI         http://nessie:19120/iceberg
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

from airflow import DAG
from airflow.datasets import Dataset
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator

OUTLET_SPOTIFY_CHARTS = Dataset("trino://trino:8080/iceberg/raw/spotify_charts")

BUCKET = "lakehouse"
CHARTS_KEY = "landing/csv/spotify/charts/spotify_charts.csv"
NAMESPACE = "raw"
TABLE_NAME = "spotify_charts"

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
            "warehouse": "warehouse",
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
        IntegerType,
        LongType,
        NestedField,
        StringType,
        TimestampType,
    )

    schema = Schema(
        NestedField(1, "chart_date", DateType(), required=False),
        NestedField(2, "position", IntegerType(), required=False),
        NestedField(3, "track_name", StringType(), required=False),
        NestedField(4, "artist_name", StringType(), required=False),
        NestedField(5, "streams", LongType(), required=False),
        NestedField(6, "region", StringType(), required=False),
        NestedField(7, "chart_type", StringType(), required=False),
        NestedField(8, "trend", StringType(), required=False),
        NestedField(9, "url", StringType(), required=False),
        NestedField(10, "_source_file", StringType(), required=False),
        NestedField(11, "_loaded_at", TimestampType(), required=False),
    )
    partition_spec = PartitionSpec(
        PartitionField(source_id=6, field_id=1001, transform=IdentityTransform(), name="region")
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


def load_charts_pyarrow(**_):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.csv as pacsv

    s3 = _get_minio_client()
    catalog = _get_iceberg_catalog()
    iceberg_table = _get_or_create_table(catalog)
    arrow_schema = iceberg_table.schema().as_arrow()

    source_file = f"s3://{BUCKET}/{CHARTS_KEY}"
    loaded_at = datetime.utcnow()

    # Download zuerst komplett – S3-Verbindung wird geschlossen bevor PyArrow startet
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv")
    try:
        os.close(tmp_fd)
        print(f"Downloading {CHARTS_KEY} ...")
        s3.download_file(BUCKET, CHARTS_KEY, tmp_path)
        print("Download complete, streaming CSV ...")

        convert_options = pacsv.ConvertOptions(
            column_types={
                "rank": pa.int32(),
                "streams": pa.int64(),
            },
            null_values=["", "NULL"],
            strings_can_be_null=True,
        )
        rename_map = {
            "date": "chart_date",
            "rank": "position",
            "title": "track_name",
            "artist": "artist_name",
            "chart": "chart_type",
        }

        # Streaming-Read in 64MB-Blöcken – verarbeitet Batches einzeln,
        # damit nie sowohl rohe CSV-Daten als auch verarbeitete Tabelle gleichzeitig im RAM liegen
        reader = pacsv.open_csv(
            tmp_path,
            read_options=pacsv.ReadOptions(block_size=64 * 1024 * 1024),
            convert_options=convert_options,
        )

        processed_batches = []
        total_rows = 0

        for batch in reader:
            batch_table = pa.Table.from_batches([batch])
            n = len(batch_table)
            total_rows += n

            # Spalten umbenennen
            batch_table = batch_table.rename_columns(
                [rename_map.get(name, name) for name in batch_table.schema.names]
            )

            # Datum von String auf date32 casten
            batch_table = batch_table.set_column(
                batch_table.schema.get_field_index("chart_date"),
                "chart_date",
                pc.cast(batch_table.column("chart_date"), pa.date32()),
            )

            # Metadaten-Spalten anhängen (pro Batch klein, effizient)
            batch_table = batch_table.append_column(
                "_source_file",
                pa.array([source_file] * n, type=pa.large_utf8()),
            )
            batch_table = batch_table.append_column(
                "_loaded_at",
                pa.array([loaded_at] * n, type=pa.timestamp("us")),
            )

            # Auf exaktes Iceberg-Schema casten (z.B. utf8 → large_utf8)
            batch_table = batch_table.cast(arrow_schema)
            processed_batches.append(batch_table)

    finally:
        os.unlink(tmp_path)

    print(f"Read {total_rows:,} rows, combining batches ...")
    arrow_table = pa.concat_tables(processed_batches)

    print(f"Writing {len(arrow_table):,} rows to iceberg.{NAMESPACE}.{TABLE_NAME} ...")
    # Vollständiger Overwrite – idempotent für Initial-Load
    iceberg_table.overwrite(arrow_table)

    print(f"✅ {len(arrow_table):,} rows written to iceberg.{NAMESPACE}.{TABLE_NAME}")


with DAG(
    dag_id="spotify_charts_pyarrow_load",
    description="Bulk-Load: Spotify Charts CSV (26 Mio.) via PyIceberg+PyArrow direkt nach Iceberg",
    start_date=datetime(2026, 3, 24),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["spotify", "kaggle", "raw", "initial-load", "pyarrow"],
) as dag:

    PythonOperator(
        task_id="load_charts_to_raw_pyarrow",
        python_callable=load_charts_pyarrow,
        outlets=[OUTLET_SPOTIFY_CHARTS],
    )
