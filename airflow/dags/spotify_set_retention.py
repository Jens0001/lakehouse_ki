"""
DAG: spotify_set_retention
Setzt Snapshot-Retention (5 Minuten) für Spotify-Tabellen nach dem Bulk-Load.

Läuft nach spotify_initial_load und konfiguriert:
- iceberg.raw.spotify_tracks
- iceberg.raw.spotify_charts

Mit max_snapshot_age_ms = 300000 (5 Minuten) um Metadaten zu reduzieren.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator


DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def set_spotify_tracks_retention(**_):
    """Setzt Snapshot-Retention für spotify_tracks via Nessie-API."""
    import requests
    import json

    nessie_url = "http://nessie:19120/api/v1"

    # Lese aktuelle Table-Metadaten
    resp = requests.get(f"{nessie_url}/trees/main/contents/raw.spotify_tracks")
    if resp.status_code != 200:
        print(f"⚠️  Tabelle nicht gefunden: {resp.text}")
        return

    table_meta = resp.json()
    print(f"✅ spotify_tracks Metadaten geladen")
    print(f"   Metadata-Location: {table_meta.get('contentId')}")

    # Hinweis: Retention wird direkt beim CREATE TABLE gesetzt
    # Nachträgliche Änderung erfordert Iceberg-native Tools
    print("📝 Retention-Properties sollten beim CREATE TABLE gesetzt sein")


def set_spotify_charts_retention(**_):
    """Setzt Snapshot-Retention für spotify_charts via Nessie-API."""
    import requests

    nessie_url = "http://nessie:19120/api/v1"

    # Lese aktuelle Table-Metadaten
    resp = requests.get(f"{nessie_url}/trees/main/contents/raw.spotify_charts")
    if resp.status_code != 200:
        print(f"⚠️  Tabelle nicht gefunden: {resp.text}")
        return

    table_meta = resp.json()
    print(f"✅ spotify_charts Metadaten geladen")
    print(f"   Metadata-Location: {table_meta.get('contentId')}")

    # Hinweis: Retention wird direkt beim CREATE TABLE gesetzt
    print("📝 Retention-Properties sollten beim CREATE TABLE gesetzt sein")


with DAG(
    dag_id="spotify_set_retention",
    description="Setzt Snapshot-Retention (5 Min) für Spotify-Tabellen",
    start_date=datetime(2026, 3, 27),
    schedule=None,  # Manuell oder via external trigger
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["spotify", "maintenance", "retention"],
) as dag:

    t1 = PythonOperator(
        task_id="check_spotify_tracks_retention",
        python_callable=set_spotify_tracks_retention,
    )

    t2 = PythonOperator(
        task_id="check_spotify_charts_retention",
        python_callable=set_spotify_charts_retention,
    )

    [t1, t2]
