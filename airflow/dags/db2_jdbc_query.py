"""
DAG: db2_jdbc_query
====================
Demonstriert die Anbindung an eine IBM DB2-Datenbank via JDBC (JayDeBeApi).

Da kein öffentlich zugänglicher DB2-Server existiert, ist dieser DAG als
lauffähiges Template konzipiert. Er wird aktiv sobald die Connection
"db2_jdbc" in Airflow hinterlegt ist.

Unterstützte Systeme:
  - IBM Db2 on-premise (11.x)
  - IBM Db2 on Cloud (Lite/Standard)
  - IBM Db2 Warehouse on Cloud
  - IBM AS/400 (iSeries) via JT400-JDBC (anderer Treiber nötig)

JDBC-Treiber-JAR:  /opt/jdbc_drivers/db2jcc.jar  (im Container)
Treiber-Klasse:    com.ibm.db2.jcc.DB2Driver

JDBC-URL Formate:
  Db2 on-premise:    jdbc:db2://hostname:50000/DATABASENAME
  Db2 on Cloud:      jdbc:db2://hostname:50001/BLUDB:sslConnection=true;
  Db2 Warehouse:     jdbc:db2://hostname:50001/BLUDB:sslConnection=true;

Was dieser DAG tut:
  1. query_db2_sysibm     – fragt Tabellen-Metadaten aus SYSIBM.SYSTABLES ab
                            (Systemkatalog: Tabellen, Zeilen-Schätzungen, Spaces)
  2. store_to_landing     – schreibt das Ergebnis als JSON in MinIO
                            s3://lakehouse/landing/json/db2/YYYY-MM-DD.json
  3. landing_to_raw       – legt die Ergebnisse in iceberg.raw.db2_tables ab

Connection einrichten (Airflow UI → Admin → Connections):
  Conn Id:    db2_jdbc
  Conn Type:  Generic
  Host:       <db2-hostname>
  Schema:     <datenbankname>  z.B. BLUDB oder SAMPLE
  Login:      <db-user>
  Password:   <db-passwort>
  Port:       50000  (on-premise) oder 50001 (Db2 on Cloud mit SSL)
  Extra:      {"jdbc_url": "jdbc:db2://<host>:50000/<dbname>",
               "jdbc_driver_class": "com.ibm.db2.jcc.DB2Driver",
               "jdbc_driver_loc": "/opt/jdbc_drivers/db2jcc.jar"}

Db2 on Cloud (Lite-Plan, kostenlos):
  Registrierung: https://cloud.ibm.com/catalog/services/db2
  Credentials:   IBM Cloud → Service → Service Credentials → New Credential
  JDBC-URL:      aus "jdbc" Feld in den Service Credentials
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
DB2_CONN_ID    = "db2_jdbc"
BUCKET         = "lakehouse"
LANDING_PREFIX = "landing/json/db2"
RAW_TABLE      = "iceberg.raw.db2_tables"

# Treiber-JAR liegt via Docker-Volume im Container (./jdbc_drivers:/opt/jdbc_drivers)
DB2_JAR        = "/opt/jdbc_drivers/db2jcc.jar"
DB2_DRIVER     = "com.ibm.db2.jcc.DB2Driver"

DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

# Abfrage: Tabellen-Metadaten aus dem DB2-Systemkatalog SYSIBM.SYSTABLES
# SYSIBM.SYSTABLES ist auf allen DB2-Varianten verfügbar (Standardkatalog).
# CARD = geschätzte Zeilenanzahl (-1 wenn keine Statistiken vorhanden).
# NPAGES = Anzahl Datenseiten (Schätzung).
SQL_QUERY = """
SELECT
    TRIM(CREATOR)                   AS schema_name,
    TRIM(NAME)                      AS table_name,
    TRIM(TYPE)                      AS table_type,
    CARD                            AS estimated_rows,
    NPAGES                          AS data_pages,
    TRIM(COALESCE(REMARKS, ''))     AS remarks
FROM SYSIBM.SYSTABLES
WHERE TYPE IN ('T', 'V')
  AND CREATOR NOT IN (
    'SYSIBM', 'SYSCAT', 'SYSSTAT', 'SYSPROC', 'SYSIBMTS',
    'SYSTOOLS', 'NULLID', 'SQLJ'
  )
ORDER BY schema_name, table_name
FETCH FIRST 50 ROWS ONLY
"""


# ---------------------------------------------------------------------------
# Hilfsfunktion: JDBC-Verbindung zu DB2 aufbauen
# ---------------------------------------------------------------------------
def _get_db2_connection():
    """
    Baut eine JDBC-Verbindung zu IBM DB2 auf via JayDeBeApi.
    Liest Verbindungsparameter aus der Airflow Connection 'db2_jdbc'.

    Rückgabe: jaydebeapi.Connection
    """
    # Verbindungsparameter aus Airflow Connection lesen
    conn = Connection.get_connection_from_secrets(DB2_CONN_ID)

    # JDBC-URL aus Extra-Feldern oder aus Host/Port/Schema zusammenbauen
    extra = conn.extra_dejson if conn.extra else {}
    jdbc_url = extra.get(
        "jdbc_url",
        f"jdbc:db2://{conn.host}:{conn.port or 50000}/{conn.schema}"
    )

    print(f"Verbinde mit DB2 via JDBC: {jdbc_url}")
    return jaydebeapi.connect(
        DB2_DRIVER,
        jdbc_url,
        [conn.login, conn.password],
        DB2_JAR,
    )


# ---------------------------------------------------------------------------
# Task 1: DB2 abfragen und Ergebnis in MinIO (Landing Zone) speichern
# ---------------------------------------------------------------------------
def query_and_store_landing(ds: str, **_) -> None:
    """
    Fragt DB2-Systemkatalog-Metadaten ab (JayDeBeApi/JDBC) und schreibt
    das rohe Ergebnis als JSON-Datei in den MinIO-Landing-Bucket.

    ds (data_interval_start Date): wird als Partitionsschlüssel genutzt.
    """
    jconn = _get_db2_connection()
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
        "source":     "db2_jdbc",
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
    Liest das JSON aus MinIO und schreibt die DB2-Metadatenzeilen in
    iceberg.raw.db2_tables (Iceberg-Tabelle via Trino).

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
            query_date      DATE,
            schema_name     VARCHAR,
            table_name      VARCHAR,
            table_type      VARCHAR,
            estimated_rows  BIGINT,
            data_pages      INTEGER,
            remarks         VARCHAR
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
        schema  = str(row.get("schema_name")  or "").replace("'", "''")
        tname   = str(row.get("table_name")   or "").replace("'", "''")
        ttype   = str(row.get("table_type")   or "").replace("'", "''")
        remarks = str(row.get("remarks")      or "").replace("'", "''")
        erows   = row.get("estimated_rows")   if row.get("estimated_rows") is not None else "NULL"
        pages   = row.get("data_pages")       if row.get("data_pages") is not None else "NULL"
        trino.run(f"""
            INSERT INTO {RAW_TABLE}
                (query_date, schema_name, table_name, table_type,
                 estimated_rows, data_pages, remarks)
            VALUES (
                DATE '{ds}', '{schema}', '{tname}', '{ttype}',
                {erows}, {pages}, '{remarks}'
            )
        """)

    print(f"✅ {len(rows)} Zeilen in {RAW_TABLE} geschrieben (Tag: {ds})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="db2_jdbc_query",
    description="IBM DB2 → JDBC → Landing Zone → iceberg.raw (Template)",
    start_date=datetime(2026, 3, 20),
    schedule="@daily",
    catchup=False,
    # DAG ist standardmäßig pausiert bis die DB2-Connection hinterlegt ist
    is_paused_upon_creation=True,
    default_args=DEFAULT_ARGS,
    tags=["db2", "jdbc", "demo", "template"],
) as dag:

    t1_query = PythonOperator(
        task_id="query_and_store_landing",
        python_callable=query_and_store_landing,
        doc_md="""
        **query_and_store_landing**
        Verbindet per JayDeBeApi/JDBC mit IBM DB2 (`db2_jdbc` Connection)
        und speichert SYSIBM.SYSTABLES-Metadaten als JSON in MinIO.

        **Voraussetzung**: Airflow Connection `db2_jdbc` muss hinterlegt sein.
        Extra-Feld-Format:
        ```json
        {
          "jdbc_url": "jdbc:db2://hostname:50000/DATABASENAME",
          "jdbc_driver_class": "com.ibm.db2.jcc.DB2Driver",
          "jdbc_driver_loc": "/opt/jdbc_drivers/db2jcc.jar"
        }
        ```

        **Db2 on Cloud (kostenloser Lite-Plan)**:
        https://cloud.ibm.com/catalog/services/db2
        JDBC-URL aus Service Credentials → `jdbc`-Feld kopieren.
        """,
    )

    t2_load = PythonOperator(
        task_id="landing_to_raw",
        python_callable=landing_to_raw,
        doc_md="""
        **landing_to_raw**
        Liest das Landing-JSON aus MinIO und schreibt es idempotent in
        `iceberg.raw.db2_tables` (Partition: query_date).
        """,
    )

    t1_query >> t2_load
