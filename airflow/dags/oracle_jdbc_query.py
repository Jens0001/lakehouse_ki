"""
DAG: oracle_jdbc_query
=======================
Demonstriert die Anbindung an eine Oracle-Datenbank via JDBC (JayDeBeApi).

Da kein öffentlich zugänglicher Oracle-Server existiert, ist dieser DAG als
lauffähiges Template konzipiert. Er wird aktiv sobald die Connection
"oracle_jdbc" in Airflow hinterlegt ist.

Zieldatenbank: Beliebige Oracle 19c/21c/23ai-Instanz
  JDBC-URL Format:  jdbc:oracle:thin:@//hostname:1521/servicename
  JDBC-URL Beispiel: jdbc:oracle:thin:@//my-oracle-server:1521/ORCLPDB1
  Treiber-Klasse:   oracle.jdbc.OracleDriver
  Treiber-JAR:      /opt/jdbc_drivers/ojdbc11.jar  (im Container)

Was dieser DAG tut:
  1. query_oracle_metadata  – fragt Tabellen-Metadaten aus ALL_TABLES ab
                              (Statistiken über Tabellen im Oracle-Schema)
  2. store_to_landing       – schreibt das Ergebnis als JSON in MinIO
                              s3://lakehouse/landing/json/oracle/YYYY-MM-DD.json
  3. landing_to_raw         – legt die Ergebnisse in iceberg.raw.oracle_tables ab

Connection einrichten (Airflow UI → Admin → Connections):
  Conn Id:    oracle_jdbc
  Conn Type:  Generic  (oder "JDBC" falls der JDBC-Provider angezeigt wird)
  Host:       <oracle-hostname>
  Schema:     <oracle-schema oder service-name>
  Login:      <db-user>
  Password:   <db-passwort>
  Port:       1521
  Extra:      {"jdbc_url": "jdbc:oracle:thin:@//<host>:1521/<service>",
               "jdbc_driver_class": "oracle.jdbc.OracleDriver",
               "jdbc_driver_loc": "/opt/jdbc_drivers/ojdbc11.jar"}
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import boto3
import jaydebeapi
from botocore.client import Config
from airflow import DAG
from airflow.models import Variable, Connection
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
# Airflow Connection ID (in der Airflow-UI hinterlegen, siehe Docstring oben)
ORACLE_CONN_ID  = "oracle_jdbc"
BUCKET          = "lakehouse"
LANDING_PREFIX  = "landing/json/oracle"
RAW_TABLE       = "iceberg.raw.oracle_tables"

# Treiber-JAR liegt via Docker-Volume im Container (./jdbc_drivers:/opt/jdbc_drivers)
ORACLE_JAR      = "/opt/jdbc_drivers/ojdbc11.jar"
ORACLE_DRIVER   = "oracle.jdbc.OracleDriver"

DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

# Abfrage: Tabellen-Metadaten aus Oracle Data Dictionary
# ALL_TABLES enthält Informationen über alle Tabellen die der User sehen darf.
# NUM_ROWS / BLOCKS können NULL sein wenn keine Statistiken gesammelt wurden (DBMS_STATS).
SQL_QUERY = """
SELECT
    owner,
    table_name,
    tablespace_name,
    num_rows,
    blocks,
    ROUND(avg_row_len / 1024, 2)                   AS avg_row_kb,
    TO_CHAR(last_analyzed, 'YYYY-MM-DD HH24:MI:SS') AS last_analyzed
FROM all_tables
WHERE owner NOT IN (
    'SYS','SYSTEM','OUTLN','DBSNMP','APPQOSSYS','DBSFWUSER',
    'GGSYS','ANONYMOUS','CTXSYS','DVSYS','DVF','GSMADMIN_INTERNAL',
    'MDSYS','OLAPSYS','ORDDATA','ORDSYS','ORDPLUGINS','SI_INFORMTN_SCHEMA',
    'WMSYS','XDB','APEX_PUBLIC_USER','FLOWS_FILES','LBACSYS','REMOTE_SCHEDULER_AGENT'
)
ORDER BY owner, num_rows DESC NULLS LAST
FETCH FIRST 50 ROWS ONLY
"""


# ---------------------------------------------------------------------------
# Hilfsfunktion: JDBC-Verbindung zu Oracle aufbauen
# ---------------------------------------------------------------------------
def _get_oracle_connection():
    """
    Baut eine JDBC-Verbindung zu Oracle auf via JayDeBeApi.
    Liest Verbindungsparameter aus der Airflow Connection 'oracle_jdbc'.

    Rückgabe: jaydebeapi.Connection
    """
    # Verbindungsparameter aus Airflow Connection lesen
    conn = Connection.get_connection_from_secrets(ORACLE_CONN_ID)

    # JDBC-URL aus den Extra-Feldern oder aus Host/Port/Schema zusammenbauen
    extra = conn.extra_dejson if conn.extra else {}
    jdbc_url = extra.get(
        "jdbc_url",
        f"jdbc:oracle:thin:@//{conn.host}:{conn.port or 1521}/{conn.schema}"
    )

    print(f"Verbinde mit Oracle via JDBC: {jdbc_url}")
    return jaydebeapi.connect(
        ORACLE_DRIVER,
        jdbc_url,
        [conn.login, conn.password],
        ORACLE_JAR,
    )


# ---------------------------------------------------------------------------
# Task 1: Oracle abfragen und Ergebnis in MinIO (Landing Zone) speichern
# ---------------------------------------------------------------------------
def query_and_store_landing(ds: str, **_) -> None:
    """
    Fragt Oracle-Tabellen-Metadaten ab (JayDeBeApi/JDBC) und schreibt
    das rohe Ergebnis als JSON-Datei in den MinIO-Landing-Bucket.

    ds (data_interval_start Date): wird als Partitionsschlüssel genutzt.
    """
    jconn = _get_oracle_connection()
    try:
        cursor = jconn.cursor()
        cursor.execute(SQL_QUERY)
        columns = [desc[0].lower() for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
    finally:
        jconn.close()

    print(f"Abfrage lieferte {len(rows)} Zeilen")

    payload = {
        "query_date": ds,
        "source":     "oracle_jdbc",
        "row_count":  len(rows),
        "data":       rows,
    }
    json_bytes = json.dumps(payload, indent=2, default=str).encode("utf-8")

    # In MinIO speichern
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
    Liest das JSON aus MinIO und schreibt die Oracle-Metadatenzeilen in
    iceberg.raw.oracle_tables (Iceberg-Tabelle via Trino).

    Idempotent: DELETE für den Tag, dann INSERT.
    """
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

    # Tabelle anlegen falls nicht vorhanden
    trino.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
            query_date       DATE,
            owner            VARCHAR,
            table_name       VARCHAR,
            tablespace_name  VARCHAR,
            num_rows         BIGINT,
            blocks           INTEGER,
            avg_row_kb       DOUBLE,
            last_analyzed    VARCHAR
        )
        WITH (
            format = 'PARQUET',
            partitioning = ARRAY['query_date']
        )
    """)

    # Idempotenz: bestehende Daten für diesen Tag löschen
    trino.run(f"DELETE FROM {RAW_TABLE} WHERE query_date = DATE '{ds}'")

    # Zeilen einfügen
    for row in rows:
        owner   = str(row.get("owner")   or "").replace("'", "''")
        tname   = str(row.get("table_name") or "").replace("'", "''")
        tspace  = str(row.get("tablespace_name") or "").replace("'", "''")
        lana    = str(row.get("last_analyzed") or "").replace("'", "''")
        nrows   = row.get("num_rows")  or "NULL"
        blocks  = row.get("blocks")    or "NULL"
        avg_kb  = row.get("avg_row_kb") or "NULL"
        trino.run(f"""
            INSERT INTO {RAW_TABLE}
                (query_date, owner, table_name, tablespace_name,
                 num_rows, blocks, avg_row_kb, last_analyzed)
            VALUES (
                DATE '{ds}', '{owner}', '{tname}', '{tspace}',
                {nrows}, {blocks}, {avg_kb}, '{lana}'
            )
        """)

    print(f"✅ {len(rows)} Zeilen in {RAW_TABLE} geschrieben (Tag: {ds})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="oracle_jdbc_query",
    description="Oracle DB → JDBC → Landing Zone → iceberg.raw (Template)",
    start_date=datetime(2026, 3, 20),
    schedule="@daily",
    catchup=False,
    # DAG ist standardmäßig pausiert bis die Oracle-Connection hinterlegt ist
    is_paused_upon_creation=True,
    default_args=DEFAULT_ARGS,
    tags=["oracle", "jdbc", "demo", "template"],
) as dag:

    t1_query = PythonOperator(
        task_id="query_and_store_landing",
        python_callable=query_and_store_landing,
        doc_md="""
        **query_and_store_landing**
        Verbindet per JayDeBeApi/JDBC mit Oracle (`oracle_jdbc` Connection)
        und speichert ALL_TABLES-Metadaten als JSON in MinIO.

        **Voraussetzung**: Airflow Connection `oracle_jdbc` muss hinterlegt sein.
        Extra-Feld-Format:
        ```json
        {
          "jdbc_url": "jdbc:oracle:thin:@//hostname:1521/SERVICENAME",
          "jdbc_driver_class": "oracle.jdbc.OracleDriver",
          "jdbc_driver_loc": "/opt/jdbc_drivers/ojdbc11.jar"
        }
        ```
        """,
    )

    t2_load = PythonOperator(
        task_id="landing_to_raw",
        python_callable=landing_to_raw,
        doc_md="""
        **landing_to_raw**
        Liest das Landing-JSON aus MinIO und schreibt es idempotent in
        `iceberg.raw.oracle_tables` (Partition: query_date).
        """,
    )

    t1_query >> t2_load
