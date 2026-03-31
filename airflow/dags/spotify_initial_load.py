"""
DAG: spotify_initial_load
Einmaliger Bulk-Load der Spotify-Kaggle-Datasets (Tracks + Charts) in die Raw-Schicht.

Voraussetzungen:
  - CSV-Dateien müssen vorab in MinIO hochgeladen worden sein:
    s3://lakehouse/landing/csv/spotify/tracks/spotify_tracks.csv
    s3://lakehouse/landing/csv/spotify/charts/spotify_charts.csv
  - Upload z.B. via MinIO Console oder mc CLI:
    mc cp spotify_tracks.csv lakehouse/lakehouse/landing/csv/spotify/tracks/
    mc cp spotify_charts.csv lakehouse/lakehouse/landing/csv/spotify/charts/

Datenquellen:
  - Spotify Tracks Dataset (Kaggle): track_id, artist_id, audio features, popularity
    z.B. https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset
  - Spotify Charts Dataset (Kaggle): date, position, streams, region
    z.B. https://www.kaggle.com/datasets/dhruvildave/spotify-charts

Betriebsmodus:
  - Einmaliger Run (schedule=None, catchup=False)
  - Idempotent: DELETE + INSERT pro Quelldatei

Airflow Variables:
  MINIO_ENDPOINT     http://minio:9000
  MINIO_ACCESS_KEY   minioadmin
  MINIO_SECRET_KEY   minioadmin123
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BUCKET = "lakehouse"

# Landing-Pfade (CSVs müssen hier vorab liegen)
TRACKS_KEY = "landing/csv/spotify/tracks/spotify_tracks.csv"
CHARTS_KEY = "landing/csv/spotify/charts/spotify_charts.csv"

# Ziel-Tabellen in Iceberg
RAW_TRACKS_TABLE = "iceberg.raw.spotify_tracks"
RAW_CHARTS_TABLE = "iceberg.raw.spotify_charts"

DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 1,
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


def _sql_value(val, cast_type="varchar"):
    """
    Wandelt einen Python-Wert in ein Trino-SQL-Literal um.
    Leere Strings und None werden zu NULL.
    Strings werden mit einfachen Anführungszeichen escaped.
    """
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return "NULL"
    if cast_type == "varchar":
        # Einfache Anführungszeichen escapen, um SQL-Injection zu verhindern
        safe = str(val).replace("'", "''")
        return f"'{safe}'"
    if cast_type == "double":
        try:
            return str(float(val))
        except (ValueError, TypeError):
            return "NULL"
    if cast_type == "integer":
        try:
            return str(int(float(val)))
        except (ValueError, TypeError):
            return "NULL"
    if cast_type == "boolean":
        return "TRUE" if str(val).lower() in ("true", "1", "yes") else "FALSE"
    return f"'{str(val).replace(chr(39), chr(39)*2)}'"


# ---------------------------------------------------------------------------
# Task 1: Spotify Tracks CSV → iceberg.raw.spotify_tracks
# ---------------------------------------------------------------------------
def load_tracks_to_raw(**_):
    """
    Liest die Tracks-CSV aus MinIO und schreibt sie per Trino INSERT in die Raw-Tabelle.

    Erwartete CSV-Spalten (Kaggle Spotify Tracks Dataset):
      track_id, artists, album_name, track_name, popularity, duration_ms,
      explicit, danceability, energy, key, loudness, mode, speechiness,
      acousticness, instrumentalness, liveness, valence, tempo,
      time_signature, track_genre

    Hinweis: Das Kaggle-Dataset hat 'artists' (Plural), das kann mehrere
    Künstlernamen komma-separiert enthalten. Wir speichern den vollen String
    und extrahieren artist_id/artist_name nicht hier – das geschieht im Staging.
    """
    s3 = _get_minio_client()
    obj = s3.get_object(Bucket=BUCKET, Key=TRACKS_KEY)
    content = obj["Body"].read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(content))

    hook = TrinoHook(trino_conn_id="trino_default")

    # Tabelle anlegen, falls noch nicht vorhanden
    # Snapshot-Retention: 5 Minuten (300000ms) um Metadaten zu reduzieren
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_TRACKS_TABLE} (
            track_id            VARCHAR,
            track_name          VARCHAR,
            artist_name         VARCHAR,
            album_name          VARCHAR,
            popularity          INTEGER,
            duration_ms         INTEGER,
            explicit            BOOLEAN,
            danceability        DOUBLE,
            energy              DOUBLE,
            musical_key         INTEGER,
            loudness            DOUBLE,
            musical_mode        INTEGER,
            speechiness         DOUBLE,
            acousticness        DOUBLE,
            instrumentalness    DOUBLE,
            liveness            DOUBLE,
            valence             DOUBLE,
            tempo               DOUBLE,
            time_signature      INTEGER,
            track_genre         VARCHAR,
            _source_file        VARCHAR,
            _loaded_at          TIMESTAMP
        )
        WITH (
            format = 'PARQUET',
            write.metadata.previous-versions-max = 2,
            history.expire.min-snaps-to-keep = 1,
            history.expire.max-snapshot-age-ms = 300000
        )
    """)

    # Idempotenz: alle Rows dieser Quelldatei löschen
    source_file = f"s3://{BUCKET}/{TRACKS_KEY}"
    hook.run(f"""
        DELETE FROM {RAW_TRACKS_TABLE}
        WHERE _source_file = '{source_file}'
    """)

    loaded_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    # CSV-Zeilen einlesen und in Batches nach Trino schreiben
    rows = []
    row_count = 0

    for row in reader:
        # CSV-Spalten auf Raw-Schema mappen
        # Kaggle-Dataset nutzt 'artists' (Plural) und 'key'/'mode' als Spaltennamen
        values = (
            f"({_sql_value(row.get('track_id'))}, "
            f"{_sql_value(row.get('track_name'))}, "
            f"{_sql_value(row.get('artists'))}, "
            f"{_sql_value(row.get('album_name'))}, "
            f"{_sql_value(row.get('popularity'), 'integer')}, "
            f"{_sql_value(row.get('duration_ms'), 'integer')}, "
            f"{_sql_value(row.get('explicit'), 'boolean')}, "
            f"{_sql_value(row.get('danceability'), 'double')}, "
            f"{_sql_value(row.get('energy'), 'double')}, "
            f"{_sql_value(row.get('key'), 'integer')}, "
            f"{_sql_value(row.get('loudness'), 'double')}, "
            f"{_sql_value(row.get('mode'), 'integer')}, "
            f"{_sql_value(row.get('speechiness'), 'double')}, "
            f"{_sql_value(row.get('acousticness'), 'double')}, "
            f"{_sql_value(row.get('instrumentalness'), 'double')}, "
            f"{_sql_value(row.get('liveness'), 'double')}, "
            f"{_sql_value(row.get('valence'), 'double')}, "
            f"{_sql_value(row.get('tempo'), 'double')}, "
            f"{_sql_value(row.get('time_signature'), 'integer')}, "
            f"{_sql_value(row.get('track_genre'))}, "
            f"'{source_file}', "
            f"TIMESTAMP '{loaded_at}')"
        )
        rows.append(values)
        row_count += 1

        # Batch-Insert in Blöcken von 50 Rows (Trino hat Token-Limit von 10000)
        if len(rows) >= 100:
            insert_sql = f"INSERT INTO {RAW_TRACKS_TABLE} VALUES {', '.join(rows)}"
            hook.run(insert_sql)
            rows = []

    # Rest-Batch schreiben
    if rows:
        insert_sql = f"INSERT INTO {RAW_TRACKS_TABLE} VALUES {', '.join(rows)}"
        hook.run(insert_sql)

    print(f"✅ {row_count} Tracks in {RAW_TRACKS_TABLE} geschrieben (Quelle: {TRACKS_KEY})")


# ---------------------------------------------------------------------------
# Task 2: Spotify Charts CSV → iceberg.raw.spotify_charts
# ---------------------------------------------------------------------------
def load_charts_to_raw(**_):
    """
    Liest die Charts-CSV aus MinIO (in Chunks) und schreibt sie per Trino INSERT in die Raw-Tabelle.

    Erwartete CSV-Spalten (Kaggle Spotify Charts Dataset):
      title, rank, date, artist, url, region, chart, trend, streams

    Hinweis: 'streams' kann leer sein (vor allem bei Viral-50-Charts).
    'chart' ist 'top200' oder 'viral50'.

    Chunks: Datei wird in 10MB-Blöcken gelesen, um OOM zu vermeiden
    """
    s3 = _get_minio_client()
    obj = s3.get_object(Bucket=BUCKET, Key=CHARTS_KEY)

    hook = TrinoHook(trino_conn_id="trino_default")

    # Tabelle anlegen, falls noch nicht vorhanden
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_CHARTS_TABLE} (
            chart_date          DATE,
            position            INTEGER,
            track_name          VARCHAR,
            artist_name         VARCHAR,
            streams             BIGINT,
            region              VARCHAR,
            chart_type          VARCHAR,
            trend               VARCHAR,
            url                 VARCHAR,
            _source_file        VARCHAR,
            _loaded_at          TIMESTAMP
        )
        WITH (
            format       = 'PARQUET',
            partitioning = ARRAY['region'],
            write.metadata.previous-versions-max = 2,
            history.expire.min-snaps-to-keep = 1,
            history.expire.max-snapshot-age-ms = 300000
        )
    """)

    # Idempotenz: alle Rows dieser Quelldatei löschen
    source_file = f"s3://{BUCKET}/{CHARTS_KEY}"
    hook.run(f"""
        DELETE FROM {RAW_CHARTS_TABLE}
        WHERE _source_file = '{source_file}'
    """)

    loaded_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    # Lese CSV in Chunks (10MB) um OOM zu vermeiden
    chunk_size = 10 * 1024 * 1024  # 10MB
    buffer = ""
    row_count = 0
    rows = []

    for chunk in iter(lambda: obj["Body"].read(chunk_size), b''):
        buffer += chunk.decode("utf-8", errors="replace")

        # Teile Buffer in Zeilen auf
        lines = buffer.split('\n')
        # Behälte die letzte unvollständige Zeile für die nächste Iteration
        buffer = lines[-1]

        # Verarbeite alle vollständigen Zeilen
        reader = csv.DictReader(lines[:-1])
        for row in reader:
            chart_date = row.get("date", "")
            if not chart_date:
                continue

            values = (
                f"(DATE '{chart_date}', "
                f"{_sql_value(row.get('rank'), 'integer')}, "
                f"{_sql_value(row.get('title'))}, "
                f"{_sql_value(row.get('artist'))}, "
                f"{_sql_value(row.get('streams'), 'integer')}, "
                f"{_sql_value(row.get('region'))}, "
                f"{_sql_value(row.get('chart'))}, "
                f"{_sql_value(row.get('trend'))}, "
                f"{_sql_value(row.get('url'))}, "
                f"'{source_file}', "
                f"TIMESTAMP '{loaded_at}')"
            )
            rows.append(values)
            row_count += 1

            # Batch-Insert in Blöcken von 100 Rows
            if len(rows) >= 100:
                insert_sql = f"INSERT INTO {RAW_CHARTS_TABLE} VALUES {', '.join(rows)}"
                hook.run(insert_sql)
                rows = []

    # Verarbeite letzte Zeile, falls vorhanden
    if buffer.strip():
        reader = csv.DictReader([buffer])
        for row in reader:
            chart_date = row.get("date", "")
            if not chart_date:
                continue

            values = (
                f"(DATE '{chart_date}', "
                f"{_sql_value(row.get('rank'), 'integer')}, "
                f"{_sql_value(row.get('title'))}, "
                f"{_sql_value(row.get('artist'))}, "
                f"{_sql_value(row.get('streams'), 'integer')}, "
                f"{_sql_value(row.get('region'))}, "
                f"{_sql_value(row.get('chart'))}, "
                f"{_sql_value(row.get('trend'))}, "
                f"{_sql_value(row.get('url'))}, "
                f"'{source_file}', "
                f"TIMESTAMP '{loaded_at}')"
            )
            rows.append(values)
            row_count += 1

    # Rest-Batch schreiben
    if rows:
        insert_sql = f"INSERT INTO {RAW_CHARTS_TABLE} VALUES {', '.join(rows)}"
        hook.run(insert_sql)

    print(f"✅ {row_count} Chart-Einträge in {RAW_CHARTS_TABLE} geschrieben (Quelle: {CHARTS_KEY})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="spotify_initial_load",
    description="Einmaliger Bulk-Load: Kaggle Spotify CSVs (Tracks + Charts) → iceberg.raw",
    start_date=datetime(2026, 3, 24),
    schedule=None,          # Nur manuell auslösen – einmaliger Load
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["spotify", "kaggle", "raw", "initial-load"],
) as dag:

#    t1_tracks = PythonOperator(
#        task_id="load_tracks_to_raw",
#        python_callable=load_tracks_to_raw,
#    )

    t2_charts = PythonOperator(
        task_id="load_charts_to_raw",
        python_callable=load_charts_to_raw,
    )

    # Tracks und Charts können parallel geladen werden – keine Abhängigkeit
    #[t1_tracks, t2_charts]
    [t2_charts]