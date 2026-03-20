"""
DAG: postgres_public_query
===========================
Demonstriert die Anbindung an eine öffentliche PostgreSQL-Datenbank.

Zieldatenbank: RNAcentral (European Bioinformatics Institute / EMBL-EBI)
  Host:     hh-pgsql-public.ebi.ac.uk
  Port:     5432
  Datenbank: pfmegrnargs
  User:     reader
  Passwort: NWDMCE5xdipIjRrp  (öffentlich dokumentiert, read-only)

Was dieser DAG tut:
  1. query_rnacentral_stats  – fragt Sequenz-Statistiken aus rna_precomputed ab
                               (Anzahl Sequenzen pro Datenbank, Top 10)
  2. store_to_landing        – schreibt das Ergebnis als JSON in MinIO
                               s3://lakehouse/landing/json/rnacentral/YYYY-MM-DD.json
  3. landing_to_raw          – legt die Ergebnisse in iceberg.raw.rnacentral_stats ab
                               (INSERT-only, kein MERGE – täglich ein Snapshot)

Connection einrichten (Airflow UI → Admin → Connections):
  Conn Id:   postgres_rnacentral
  Conn Type: Postgres
  Host:      hh-pgsql-public.ebi.ac.uk
  Schema:    pfmegrnargs
  Login:     reader
  Password:  NWDMCE5xdipIjRrp
  Port:      5432
"""

from __future__ import annotations

import json
import io
from datetime import datetime, timedelta

import boto3
from botocore.client import Config
from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.trino.hooks.trino import TrinoHook


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
# Airflow Connection ID (in der Airflow-UI hinterlegen, siehe Docstring oben)
PG_CONN_ID     = "postgres_rnacentral"
BUCKET         = "lakehouse"
LANDING_PREFIX = "landing/json/rnacentral"
RAW_TABLE      = "iceberg.raw.rnacentral_stats"

DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

# Abfrage: Anzahl Sequenzen pro Quell-Datenbank (Top 10 nach Anzahl)
# rna_precomputed enthält aggregierte Metadaten für jede RNA-Sequenz.
# taxid ist die NCBI Taxonomie-ID des Organismus.
SQL_QUERY = """
SELECT
    d.display_name   AS source_database,
    COUNT(DISTINCT xr.upi) AS sequence_count,
    MIN(rp.len)      AS min_length,
    MAX(rp.len)      AS max_length,
    AVG(rp.len)::INT AS avg_length
FROM xref xr
JOIN database d    ON xr.dbid = d.id
JOIN rna    rp     ON xr.upi  = rp.upi
WHERE xr.deleted = 'N'
GROUP BY d.display_name
ORDER BY sequence_count DESC
LIMIT 10
"""


# ---------------------------------------------------------------------------
# Task 1: PostgreSQL abfragen und Ergebnis in MinIO (Landing Zone) speichern
# ---------------------------------------------------------------------------
def query_and_store_landing(ds: str, **_) -> None:
    """
    Fragt die RNAcentral-Datenbank ab (PostgresHook) und schreibt
    das rohe Ergebnis als JSON-Datei in den MinIO-Landing-Bucket.

    ds (data_interval_start Date): wird als Partitionsschlüssel genutzt.
    """
    # PostgreSQL-Abfrage via Airflow PostgresHook
    pg_hook = PostgresHook(postgres_conn_id=PG_CONN_ID)
    conn = pg_hook.get_conn()
    cursor = conn.cursor()
    cursor.execute(SQL_QUERY)
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    print(f"Abfrage lieferte {len(rows)} Zeilen")

    # Ergebnis als JSON serialisieren
    payload = {
        "query_date":  ds,
        "source":      "rnacentral_ebi",
        "row_count":   len(rows),
        "data":        rows,
    }
    json_bytes = json.dumps(payload, indent=2, default=str).encode("utf-8")

    # In MinIO schreiben
    minio_endpoint   = Variable.get("MINIO_ENDPOINT",   default_var="http://minio:9000")
    minio_access_key = Variable.get("MINIO_ACCESS_KEY", default_var="minioadmin")
    minio_secret_key = Variable.get("MINIO_SECRET_KEY", default_var="minioadmin123")

    s3 = boto3.client(
        "s3",
        endpoint_url=minio_endpoint,
        aws_access_key_id=minio_access_key,
        aws_secret_access_key=minio_secret_key,
        config=Config(signature_version="s3v4"),
    )
    key = f"{LANDING_PREFIX}/{ds}.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json_bytes, ContentType="application/json")
    print(f"✅ Gespeichert: s3://{BUCKET}/{key}")


# ---------------------------------------------------------------------------
# Task 2: Landing Zone → Iceberg Raw-Tabelle via Trino
# ---------------------------------------------------------------------------
def landing_to_raw(ds: str, **_) -> None:
    """
    Liest das JSON aus MinIO und schreibt die Statistikzeilen in
    iceberg.raw.rnacentral_stats (Iceberg-Tabelle via Trino).

    Strategie: DELETE WHERE query_date = ds, dann INSERT.
    So kann der Task idempotent erneut laufen (Airflow-Retry).
    """
    # JSON aus MinIO lesen
    minio_endpoint   = Variable.get("MINIO_ENDPOINT",   default_var="http://minio:9000")
    minio_access_key = Variable.get("MINIO_ACCESS_KEY", default_var="minioadmin")
    minio_secret_key = Variable.get("MINIO_SECRET_KEY", default_var="minioadmin123")

    s3 = boto3.client(
        "s3",
        endpoint_url=minio_endpoint,
        aws_access_key_id=minio_access_key,
        aws_secret_access_key=minio_secret_key,
        config=Config(signature_version="s3v4"),
    )
    key = f"{LANDING_PREFIX}/{ds}.json"
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    payload = json.load(obj["Body"])
    rows = payload["data"]

    trino = TrinoHook(trino_conn_id="trino_default")

    # Tabelle anlegen falls noch nicht vorhanden (CREATE TABLE IF NOT EXISTS)
    trino.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
            query_date       DATE,
            source_database  VARCHAR,
            sequence_count   BIGINT,
            min_length       INTEGER,
            max_length       INTEGER,
            avg_length       INTEGER
        )
        WITH (
            format = 'PARQUET',
            partitioning = ARRAY['query_date']
        )
    """)

    # Idempotenz: bestehende Daten für diesen Tag löschen
    trino.run(f"DELETE FROM {RAW_TABLE} WHERE query_date = DATE '{ds}'")

    # Neue Zeilen einfügen
    for row in rows:
        trino.run(f"""
            INSERT INTO {RAW_TABLE}
                (query_date, source_database, sequence_count, min_length, max_length, avg_length)
            VALUES (
                DATE '{ds}',
                '{row['source_database'].replace("'", "''")}',
                {row['sequence_count']},
                {row['min_length']},
                {row['max_length']},
                {row['avg_length']}
            )
        """)

    print(f"✅ {len(rows)} Zeilen in {RAW_TABLE} geschrieben (Tag: {ds})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="postgres_public_query",
    description="RNAcentral (EBI) PostgreSQL → Landing Zone → iceberg.raw",
    start_date=datetime(2026, 3, 20),
    schedule="@daily",
    catchup=False,
    default_args=DEFAULT_ARGS,
    tags=["postgres", "jdbc", "rnacentral", "demo"],
) as dag:

    t1_query = PythonOperator(
        task_id="query_and_store_landing",
        python_callable=query_and_store_landing,
        doc_md="""
        **query_and_store_landing**
        Verbindet per PostgresHook mit `postgres_rnacentral` (muss in Airflow Connections
        hinterlegt sein) und speichert Top-10-Datenbankstatistiken als JSON in MinIO.
        """,
    )

    t2_load = PythonOperator(
        task_id="landing_to_raw",
        python_callable=landing_to_raw,
        doc_md="""
        **landing_to_raw**
        Liest das Landing-JSON aus MinIO und schreibt es idempotent in
        `iceberg.raw.rnacentral_stats` (Partition: query_date).
        """,
    )

    t1_query >> t2_load
