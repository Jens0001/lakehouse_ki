"""
DAG: energy_charts_to_raw
Holt stündliche Day-Ahead-Spotpreise von der Energy-Charts API und schreibt sie in:
  1. Landing Zone: s3://lakehouse/landing/json/energy_prices/YYYY-MM-DD.json
  2. iceberg.raw.energy_price_hourly (via Trino)

Wichtiger Hinweis zur Gebotszone DE-LU:
  Die Gebotszone DE-LU existiert erst ab dem 01.10.2018. Zu diesem Datum wurde
  die bisherige gemeinsame Zone DE-AT-LU aufgetrennt. Anfragen vor dem 01.10.2018
  liefern HTTP 404 – daher ist start_date auf 2018-10-01 gesetzt.

Betriebsmodi:
  - Backfill: einmaliger Run mit start_date=2018-10-01, catchup=True
  - Täglich:  normaler Schedule, holt jeweils den Vortag

Airflow Variables (Airflow UI → Admin → Variables):
  ENERGY_CHARTS_BIDDING_ZONE   z.B. DE-LU  (default: DE-LU)
  MINIO_ENDPOINT               http://minio:9000
  MINIO_ACCESS_KEY             minioadmin
  MINIO_SECRET_KEY             minioadmin123

API-Referenz: https://api.energy-charts.info/
  Endpoint: GET /price?bzn=DE-LU&start=YYYY-MM-DD&end=YYYY-MM-DD
  Lizenz:   CC BY 4.0, Quelle: Bundesnetzagentur | SMARD.de
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, date

import requests
from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BUCKET          = "lakehouse"
LANDING_PREFIX  = "landing/json/energy_prices"
RAW_TABLE       = "iceberg.raw.energy_price_hourly"
API_BASE        = "https://api.energy-charts.info/price"

DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _get_minio_client():
    """Gibt einen MinIO-Client (boto3) zurück."""
    import boto3
    from botocore.client import Config

    endpoint   = Variable.get("MINIO_ENDPOINT",   default_var="http://minio:9000")
    access_key = Variable.get("MINIO_ACCESS_KEY", default_var="minioadmin")
    secret_key = Variable.get("MINIO_SECRET_KEY", default_var="minioadmin123")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


# ---------------------------------------------------------------------------
# Task 1: API → MinIO Landing Zone
# ---------------------------------------------------------------------------
def fetch_and_store_landing(ds: str, **_):
    """
    Holt Day-Ahead-Spotpreise für ds (YYYY-MM-DD) und speichert JSON in MinIO Landing Zone.
    ds ist der Logical Date des DAG-Runs – wir holen den entsprechenden Tag.

    Die API liefert unix_seconds[] und price[] als parallele Arrays.
    NULL-Werte in price[] sind möglich (z.B. fehlende Stunden bei Umstellung).
    Negative Preise sind normal und werden 1:1 übernommen.
    """
    bidding_zone = Variable.get("ENERGY_CHARTS_BIDDING_ZONE", default_var="DE-LU")

    params = {
        "bzn":   bidding_zone,
        "start": ds,
        "end":   ds,
    }

    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Metadaten ergänzen, damit die Landing-Datei selbsterklärend ist
    data["_meta"] = {
        "bidding_zone": bidding_zone,
        "fetch_date":   ds,
        "source":       "energy-charts",
        "license":      data.get("license_info", ""),
    }

    s3  = _get_minio_client()
    key = f"{LANDING_PREFIX}/{ds}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"✅ Gespeichert: s3://{BUCKET}/{key}")


# ---------------------------------------------------------------------------
# Task 2: Landing Zone → iceberg.raw.energy_price_hourly
# ---------------------------------------------------------------------------
def landing_to_raw(ds: str, **_):
    """
    Liest JSON aus MinIO und schreibt Stundenwerte per Trino INSERT in raw-Tabelle.
    Idempotent: löscht zuerst alle Rows für diesen Tag und Bidding-Zone, dann INSERT.

    Tabellenschema:
      timestamp    – Messzeitpunkt (aus unix_seconds, lokale Zeitzone des API-Responses)
      date_key     – Partitionierungsspalte
      hour         – Stunde des Tages (0–23), aus timestamp abgeleitet
      bidding_zone – Gebotszone (z.B. "DE-LU")
      price_eur_mwh – Day-Ahead-Preis in EUR/MWh (kann NULL oder negativ sein)
      _source_file – Pfad zur Landing-Datei in MinIO
      _loaded_at   – Ladezeitpunkt in UTC
    """
    s3  = _get_minio_client()
    key = f"{LANDING_PREFIX}/{ds}.json"

    obj  = s3.get_object(Bucket=BUCKET, Key=key)
    data = json.loads(obj["Body"].read())

    unix_seconds = data.get("unix_seconds", [])
    prices       = data.get("price", [])
    bidding_zone = data["_meta"]["bidding_zone"]

    hook = TrinoHook(trino_conn_id="trino_default")

    # Tabelle anlegen, falls noch nicht vorhanden
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

    # Idempotenz: bestehende Rows für diesen Tag und Gebotszone löschen
    hook.run(f"""
        DELETE FROM {RAW_TABLE}
        WHERE date_key    = DATE '{ds}'
          AND bidding_zone = '{bidding_zone}'
    """)

    if not unix_seconds:
        print(f"⚠️  Keine Daten für {ds} / {bidding_zone} – leere API-Response.")
        return

    source_file = f"s3://{BUCKET}/{key}"
    loaded_at   = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    rows = []
    for i, unix_ts in enumerate(unix_seconds):
        # Unix-Timestamp → UTC Datetime (die API liefert Timestamps in Lokalzeit des BZN,
        # aber unix_seconds sind immer UTC-basiert; Trino TIMESTAMP ist timezone-naive)
        dt    = datetime.utcfromtimestamp(unix_ts)
        price = prices[i] if i < len(prices) and prices[i] is not None else "NULL"

        rows.append(
            f"(TIMESTAMP '{dt.strftime('%Y-%m-%d %H:%M:%S')}', "
            f"DATE '{ds}', "
            f"{dt.hour}, "
            f"'{bidding_zone}', "
            f"{price}, "
            f"'{source_file}', "
            f"TIMESTAMP '{loaded_at}')"
        )

    # Batch-Insert in Blöcken von 100 Rows (Trino-Limit beachten)
    batch_size = 100
    for i in range(0, len(rows), batch_size):
        batch      = rows[i : i + batch_size]
        insert_sql = f"INSERT INTO {RAW_TABLE} VALUES {', '.join(batch)}"
        hook.run(insert_sql)

    print(f"✅ {len(rows)} Stunden-Rows in {RAW_TABLE} geschrieben (Tag: {ds}, Zone: {bidding_zone})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="energy_charts_to_raw",
    description="Energy-Charts Day-Ahead-Spotpreise (DE-LU) → Landing Zone → iceberg.raw",
    # Ältestes verfügbares Datum für DE-LU: 01.10.2018 (Gebotszonentrennung DE-AT-LU → DE-LU + AT)
    start_date=datetime(2018, 10, 1),
    schedule="0 7 * * *",   # täglich 07:00 UTC – holt den Vortag (nach open_meteo_to_raw um 06:00)
    catchup=True,            # für Backfill ab 2018-10-01 – einmalig, dann deaktivieren
    max_active_runs=3,       # parallele Backfill-Runs begrenzen (API-Schonung)
    default_args=DEFAULT_ARGS,
    tags=["energy-charts", "prices", "raw"],
) as dag:

    t1_fetch = PythonOperator(
        task_id="fetch_to_landing",
        python_callable=fetch_and_store_landing,
    )

    t2_load = PythonOperator(
        task_id="landing_to_raw",
        python_callable=landing_to_raw,
    )

    t1_fetch >> t2_load
