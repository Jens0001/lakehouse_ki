"""
DAG: open_meteo_to_raw
Holt stündliche Wetterdaten von der Open-Meteo Archive API und schreibt sie in:
  1. Landing Zone: s3://lakehouse/landing/json/weather/YYYY-MM-DD.json
  2. iceberg.raw.weather_hourly (via Trino)

Betriebsmodi:
  - Backfill: einmaliger Run mit start_date=2020-01-01, max_active_runs=1, catchup=True
  - Täglich:  normaler Schedule, holt jeweils den Vortag

Airflow Variables (Airflow UI → Admin → Variables):
  WEATHER_LATITUDE      z.B. 52.59
  WEATHER_LONGITUDE     z.B. 13.35
  WEATHER_LOCATION_KEY  z.B. berlin-reinickendorf
  MINIO_ENDPOINT        http://minio:9000
  MINIO_ACCESS_KEY      minioadmin
  MINIO_SECRET_KEY      minioadmin123
"""

from __future__ import annotations

import json
import io
from datetime import datetime, timedelta, date

import requests
from airflow import DAG
from airflow.datasets import Dataset
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook

# URI-Format: trino://<host>:<port>/<catalog>.<schema>.<table>
# Die OpenLineage-Provider-Komponente extrahiert daraus namespace=trino://trino:8080
# und name=iceberg.raw.weather_hourly → OM matcht das auf den Service "lakehouse_trino".
OUTLET_WEATHER_HOURLY = Dataset("trino://trino:8080/iceberg.raw.weather_hourly")


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BUCKET = "lakehouse"
LANDING_PREFIX = "landing/json/weather"
RAW_TABLE = "iceberg.raw.weather_hourly"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
HOURLY_PARAMS = (
    "temperature_2m,precipitation,windspeed_10m,"
    "weathercode,apparent_temperature,relative_humidity_2m"
)

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

    endpoint = Variable.get("MINIO_ENDPOINT", default_var="http://minio:9000")
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


def _fetch_date(target_date: date) -> dict:
    """Holt Stundendaten für ein Datum von der Open-Meteo API."""
    lat = float(Variable.get("WEATHER_LATITUDE", default_var="52.59"))
    lon = float(Variable.get("WEATHER_LONGITUDE", default_var="13.35"))

    date_str = target_date.isoformat()

    # Archive API für Vergangenheit; Forecast API für heute/morgen
    if target_date < date.today():
        api_url = ARCHIVE_API
    else:
        api_url = FORECAST_API

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date_str,
        "end_date": date_str,
        "hourly": HOURLY_PARAMS,
        "timezone": "Europe/Berlin",
    }

    resp = requests.get(api_url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Task 1: API → MinIO Landing Zone
# ---------------------------------------------------------------------------
def fetch_and_store_landing(ds: str, **_):
    """
    Holt Wetterdaten für ds (YYYY-MM-DD) und speichert JSON in MinIO Landing Zone.
    ds ist der Logical Date des DAG-Runs – wir holen den entsprechenden Tag.
    """
    target_date = date.fromisoformat(ds)
    data = _fetch_date(target_date)

    location_key = Variable.get(
        "WEATHER_LOCATION_KEY", default_var="berlin-reinickendorf"
    )
    data["_meta"] = {
        "location_key": location_key,
        "fetch_date": ds,
        "source": "open-meteo",
    }

    s3 = _get_minio_client()
    key = f"{LANDING_PREFIX}/{ds}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"✅ Gespeichert: s3://{BUCKET}/{key}")


# ---------------------------------------------------------------------------
# Task 2: Landing Zone → iceberg.raw.weather_hourly
# ---------------------------------------------------------------------------
def landing_to_raw(ds: str, **_):
    """
    Liest JSON aus MinIO und schreibt Stundenwerte per Trino INSERT in raw-Tabelle.
    Idempotent: löscht zuerst alle Rows für diesen Tag/Standort.
    """
    s3 = _get_minio_client()
    key = f"{LANDING_PREFIX}/{ds}.json"

    obj = s3.get_object(Bucket=BUCKET, Key=key)
    data = json.loads(obj["Body"].read())

    hourly = data["hourly"]
    location_key = data["_meta"]["location_key"]
    lat = data["latitude"]
    lon = data["longitude"]

    # Sicherstellen, dass Tabelle existiert
    hook = TrinoHook(trino_conn_id="trino_default")
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
            timestamp           TIMESTAMP,
            date_key            DATE,
            hour                INTEGER,
            location_key        VARCHAR,
            latitude            DOUBLE,
            longitude           DOUBLE,
            temperature_2m      DOUBLE,
            apparent_temperature DOUBLE,
            precipitation       DOUBLE,
            windspeed_10m       DOUBLE,
            weathercode         INTEGER,
            relative_humidity_2m DOUBLE,
            _source_file        VARCHAR,
            _loaded_at          TIMESTAMP
        )
        WITH (
            format = 'PARQUET',
            partitioning = ARRAY['date_key']
        )
    """)

    # Idempotenz: bestehende Rows für diesen Tag + Standort löschen
    hook.run(f"""
        DELETE FROM {RAW_TABLE}
        WHERE date_key = DATE '{ds}'
        AND location_key = '{location_key}'
    """)

    # Neue Rows einfügen
    timestamps = hourly["time"]
    temperatures = hourly.get("temperature_2m", [None] * len(timestamps))
    apparent_temps = hourly.get("apparent_temperature", [None] * len(timestamps))
    precipitations = hourly.get("precipitation", [None] * len(timestamps))
    windspeeds = hourly.get("windspeed_10m", [None] * len(timestamps))
    weathercodes = hourly.get("weathercode", [None] * len(timestamps))
    humidities = hourly.get("relative_humidity_2m", [None] * len(timestamps))

    source_file = f"s3://{BUCKET}/{key}"
    loaded_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    rows = []
    for i, ts in enumerate(timestamps):
        dt = datetime.fromisoformat(ts)
        temp = temperatures[i] if temperatures[i] is not None else "NULL"
        app_temp = apparent_temps[i] if apparent_temps[i] is not None else "NULL"
        precip = precipitations[i] if precipitations[i] is not None else "NULL"
        wind = windspeeds[i] if windspeeds[i] is not None else "NULL"
        wcode = weathercodes[i] if weathercodes[i] is not None else "NULL"
        humidity = humidities[i] if humidities[i] is not None else "NULL"

        rows.append(
            f"(TIMESTAMP '{dt.strftime('%Y-%m-%d %H:%M:%S')}', "
            f"DATE '{ds}', "
            f"{dt.hour}, "
            f"'{location_key}', "
            f"{lat}, "
            f"{lon}, "
            f"{temp}, "
            f"{app_temp}, "
            f"{precip}, "
            f"{wind}, "
            f"{wcode}, "
            f"{humidity}, "
            f"'{source_file}', "
            f"TIMESTAMP '{loaded_at}')"
        )

    # Batch-Insert in Blöcken von 100 Rows
    batch_size = 100
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        insert_sql = f"INSERT INTO {RAW_TABLE} VALUES {', '.join(batch)}"
        hook.run(insert_sql)

    print(f"✅ {len(rows)} Stunden-Rows in {RAW_TABLE} geschrieben (Tag: {ds})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="open_meteo_to_raw",
    description="Open-Meteo Wetterdaten → Landing Zone → iceberg.raw",
    start_date=datetime(2020, 1, 1),
    schedule="0 6 * * *",  # täglich 06:00 Uhr – holt den Vortag (Airflow 3.x: schedule statt schedule_interval)
    catchup=True,                   # für Backfill: einmalig mit catchup=True starten
    max_active_runs=3,              # parallele Backfill-Runs begrenzen
    default_args=DEFAULT_ARGS,
    tags=["open-meteo", "weather", "raw"],
) as dag:

    t1_fetch = PythonOperator(
        task_id="fetch_to_landing",
        python_callable=fetch_and_store_landing,
    )

    t2_load = PythonOperator(
        task_id="landing_to_raw",
        python_callable=landing_to_raw,
        outlets=[OUTLET_WEATHER_HOURLY],
    )

    t1_fetch >> t2_load
