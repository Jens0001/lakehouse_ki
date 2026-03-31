"""
DAG: spotify_kaggle_download
Lädt Spotify-Datasets direkt von Kaggle herunter und speichert sie in MinIO.

Ablauf:
  1. Kaggle-API authentifizieren (Username + API-Key aus Airflow Variables)
  2. Dataset 1: maharshipandya/-spotify-tracks-dataset → spotify_tracks.csv
  3. Dataset 2: dhruvildave/spotify-charts → spotify_charts.csv
  4. Beide CSVs nach MinIO: s3://lakehouse/landing/csv/spotify/{tracks,charts}/
  5. Trigger DAG: spotify_initial_load (automatischer Bulk-Load)

Airflow Variables (erforderlich):
  KAGGLE_USERNAME = dein Kaggle-Username
  KAGGLE_API_KEY  = dein Kaggle-API-Key (aus kaggle.json)
  MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import boto3
from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.standard.operators.bash import BashOperator
from botocore.client import Config


DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

BUCKET = "lakehouse"
TRACKS_DATASET = "maharshipandya/-spotify-tracks-dataset"
CHARTS_DATASET = "dhruvildave/spotify-charts"


def _get_minio_client():
    """Gibt einen MinIO-Client (boto3) zurück."""
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


def _setup_kaggle_auth():
    """
    Setzt Kaggle-API-Authentifizierung ein.
    Schreibt ~/.kaggle/kaggle.json mit Credentials aus Airflow Variables.
    """
    username = Variable.get("KAGGLE_USERNAME")
    api_key = Variable.get("KAGGLE_API_KEY")

    kaggle_dir = os.path.expanduser("~/.kaggle")
    os.makedirs(kaggle_dir, exist_ok=True)

    kaggle_json = os.path.join(kaggle_dir, "kaggle.json")
    with open(kaggle_json, "w") as f:
        f.write(f'{{"username": "{username}", "key": "{api_key}"}}')

    # Kaggle verlangt chmod 600
    os.chmod(kaggle_json, 0o600)
    print(f"✅ Kaggle-Auth konfiguriert: {kaggle_json}")


def download_and_upload_tracks(**_):
    """
    Ladet Spotify Tracks Dataset von Kaggle herunter und schreibt es nach MinIO.
    """
    import zipfile

    _setup_kaggle_auth()

    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"📥 Lade Tracks-Dataset herunter: {TRACKS_DATASET}")
        api.dataset_download_files(TRACKS_DATASET, path=tmpdir, unzip=True)

        # Finde die CSV-Datei (das Kaggle-Dataset nennt es 'dataset.csv')
        csv_file = None
        files = os.listdir(tmpdir)

        # Versuche zuerst, eine Datei mit "track" im Namen zu finden
        for f in files:
            if f.endswith(".csv") and "track" in f.lower():
                csv_file = os.path.join(tmpdir, f)
                print(f"   Gefunden (track-Name): {f}")
                break

        # Falls nicht gefunden, nimm einfach die erste CSV (meist 'dataset.csv')
        if not csv_file:
            for f in files:
                if f.endswith(".csv"):
                    csv_file = os.path.join(tmpdir, f)
                    print(f"   Gefunden (erste CSV): {f}")
                    break

        if not csv_file:
            raise FileNotFoundError(
                f"Keine Track-CSV im Dataset gefunden. Inhalt: {files}"
            )

        # Upload zu MinIO
        s3 = _get_minio_client()
        with open(csv_file, "rb") as f:
            key = f"landing/csv/spotify/tracks/spotify_tracks.csv"
            s3.upload_fileobj(f, BUCKET, key)
            print(f"✅ Hochgeladen: s3://{BUCKET}/{key}")


def download_and_upload_charts(**_):
    """
    Lädt Spotify Charts Dataset von Kaggle herunter und schreibt es nach MinIO.
    """
    _setup_kaggle_auth()

    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"📥 Lade Charts-Dataset herunter: {CHARTS_DATASET}")
        api.dataset_download_files(CHARTS_DATASET, path=tmpdir, unzip=True)

        # Finde die CSV-Datei (das Kaggle-Dataset nennt es 'spotify_charts.csv' oder 'dataset.csv')
        csv_file = None
        files = os.listdir(tmpdir)

        # Versuche zuerst, eine Datei mit "chart" im Namen zu finden
        for f in files:
            if f.endswith(".csv") and "chart" in f.lower():
                csv_file = os.path.join(tmpdir, f)
                print(f"   Gefunden (chart-Name): {f}")
                break

        # Falls nicht gefunden, nimm einfach die erste CSV
        if not csv_file:
            for f in files:
                if f.endswith(".csv"):
                    csv_file = os.path.join(tmpdir, f)
                    print(f"   Gefunden (erste CSV): {f}")
                    break

        if not csv_file:
            raise FileNotFoundError(
                f"Keine Charts-CSV im Dataset gefunden. Inhalt: {files}"
            )

        # Upload zu MinIO
        s3 = _get_minio_client()
        with open(csv_file, "rb") as f:
            key = f"landing/csv/spotify/charts/spotify_charts.csv"
            s3.upload_fileobj(f, BUCKET, key)
            print(f"✅ Hochgeladen: s3://{BUCKET}/{key}")


with DAG(
    dag_id="spotify_kaggle_download",
    description="Lädt Spotify-Datasets von Kaggle → MinIO Landing Zone",
    start_date=datetime(2026, 3, 27),
    schedule=None,  # Nur manuell triggern (kann später auf @weekly geändert werden)
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["spotify", "kaggle", "download", "landing"],
) as dag:

    t1_tracks = PythonOperator(
        task_id="download_tracks",
        python_callable=download_and_upload_tracks,
    )

    t2_charts = PythonOperator(
        task_id="download_charts",
        python_callable=download_and_upload_charts,
    )

    # Trigger spotify_initial_load nach erfolgreichen Downloads
    t3_trigger = BashOperator(
        task_id="trigger_spotify_initial_load",
        bash_command="""
        curl -X POST http://airflow:8080/api/v2/dags/spotify_initial_load/dagRuns \
          -H "Content-Type: application/json" \
          -d '{"conf": {}}' \
          -u admin:admin \
          --silent \
          -w "\nStatus: %{http_code}\n"
        """,
    )

    # Parallele Downloads, dann Trigger
    [t1_tracks, t2_charts] >> t3_trigger
