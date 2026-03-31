"""
DAG: spotify_artist_update
Holt wöchentlich aktuelle Artist-Metadaten von der Spotify Web API und schreibt
Snapshots in die Raw-Schicht. Jeder Snapshot enthält den aktuellen Stand eines
Künstlers (name, genres, popularity, followers). dbt erkennt Änderungen via
Hashdiff und erzeugt SCD Type 2 History in dim_artist.

Ablauf:
  1. Distinct artist_name-Werte aus iceberg.raw.spotify_tracks via Trino lesen
  2. Spotify Web API: OAuth2 Token holen (Client Credentials Flow)
  3. Spotify Search API: Pro Artist-Name einen Search → artist_id, genres, popularity, followers
  4. JSON-Snapshot → MinIO Landing Zone (s3://lakehouse/landing/json/spotify_artists/YYYY-MM-DD.json)
  5. JSON → iceberg.raw.spotify_artist_snapshots (Trino INSERT, idempotent)

Airflow Variables:
  SPOTIFY_CLIENT_ID       Spotify Developer App Client ID
  SPOTIFY_CLIENT_SECRET   Spotify Developer App Client Secret
  MINIO_ENDPOINT          http://minio:9000
  MINIO_ACCESS_KEY        minioadmin
  MINIO_SECRET_KEY        minioadmin123

Spotify API Referenz:
  Auth:   https://developer.spotify.com/documentation/web-api/tutorials/client-credentials-flow
  Search: https://developer.spotify.com/documentation/web-api/reference/search

Rate Limits:
  - Spotify Web API erlaubt ca. 100 Requests/Minute bei Client Credentials
  - Bei HTTP 429 wird der Retry-After-Header beachtet (Exponential Backoff)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import requests
from airflow import DAG
from airflow.models import Variable
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook


# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
BUCKET = "lakehouse"
LANDING_PREFIX = "landing/json/spotify_artists"
RAW_TABLE = "iceberg.raw.spotify_artist_snapshots"
RAW_TRACKS_TABLE = "iceberg.raw.spotify_tracks"

# Spotify API Endpoints
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"

DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 2,
    "retry_delay": timedelta(minutes=10),
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


def _get_spotify_token() -> str:
    """
    Holt ein OAuth2 Access Token vom Spotify Accounts Service (Client Credentials Flow).
    Kein User-Login nötig – nur Client ID + Secret für öffentliche Metadaten.
    """
    client_id = Variable.get("SPOTIFY_CLIENT_ID")
    client_secret = Variable.get("SPOTIFY_CLIENT_SECRET")

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _search_artist(artist_name: str, token: str) -> dict | None:
    """
    Sucht einen Künstler über die Spotify Search API.
    Gibt das erste Ergebnis als Dict zurück oder None bei keinem Treffer.

    Rate-Limit-Handling: Bei HTTP 429 wird gewartet und erneut versucht.
    """
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "q": artist_name,
        "type": "artist",
        "limit": 1,
    }

    for attempt in range(3):
        resp = requests.get(
            SPOTIFY_SEARCH_URL, headers=headers, params=params, timeout=15
        )

        if resp.status_code == 429:
            # Rate Limit erreicht – auf Retry-After-Header warten
            retry_after = int(resp.headers.get("Retry-After", 5))
            print(f"⚠️  Rate Limit – warte {retry_after}s (Versuch {attempt + 1}/3)")
            time.sleep(retry_after)
            continue

        resp.raise_for_status()
        items = resp.json().get("artists", {}).get("items", [])
        if items:
            a = items[0]
            return {
                "artist_id": a["id"],
                "artist_name": a["name"],
                "genres": ", ".join(a.get("genres", [])),
                "popularity": a.get("popularity"),
                "followers": a.get("followers", {}).get("total"),
            }
        return None

    print(f"⚠️  Spotify Search nach '{artist_name}' fehlgeschlagen nach 3 Versuchen")
    return None


# ---------------------------------------------------------------------------
# Task 1: Distinct Artist-Namen aus Raw-Tabelle lesen
# ---------------------------------------------------------------------------
def fetch_artist_names(**context):
    """
    Liest alle eindeutigen Artist-Namen aus iceberg.raw.spotify_tracks via Trino.
    Das Kaggle-Dataset hat 'artist_name' als komma-separierte Künstlerliste.
    Wir splitten und deduplizieren, um jeden Künstler einzeln über die API abzufragen.

    Ergebnis wird via XCom an den nächsten Task übergeben.
    """
    hook = TrinoHook(trino_conn_id="trino_default")
    records = hook.get_records(
        f"SELECT DISTINCT artist_name FROM {RAW_TRACKS_TABLE} WHERE artist_name IS NOT NULL"
    )

    # Artist-Namen können komma-separiert sein (z.B. "Artist A;Artist B" oder "Artist A, Artist B")
    # Wir splitten auf ';' (häufiges Kaggle-Trennzeichen) und bereinigen
    unique_artists = set()
    for (raw_name,) in records:
        # Verschiedene Trennzeichen berücksichtigen
        for sep in [";", ","]:
            if sep in raw_name:
                for part in raw_name.split(sep):
                    cleaned = part.strip()
                    if cleaned:
                        unique_artists.add(cleaned)
                break
        else:
            unique_artists.add(raw_name.strip())

    artist_list = sorted(unique_artists)
    print(f"📊 {len(artist_list)} eindeutige Artist-Namen gefunden")
    return artist_list


# ---------------------------------------------------------------------------
# Task 2: Spotify API abfragen + JSON in Landing Zone speichern
# ---------------------------------------------------------------------------
def fetch_and_store_artists(**context):
    """
    Fragt für jeden Artist-Namen die Spotify Search API ab und speichert
    die Ergebnisse als JSON-Snapshot in der MinIO Landing Zone.

    Pro DAG-Run wird ein Snapshot mit dem aktuellen Datum erstellt.
    Der Snapshot enthält alle Künstler mit ihren aktuellen Metadaten –
    diese bilden die Grundlage für SCD2-Erkennung im dbt Data Vault.
    """
    # Artist-Namen aus Task 1 via XCom holen
    artist_names = context["ti"].xcom_pull(task_ids="fetch_artist_names")
    if not artist_names:
        print("⚠️  Keine Artist-Namen gefunden – überspringe API-Abfrage")
        return

    # Spotify OAuth2 Token holen
    token = _get_spotify_token()

    snapshot_date = context["ds"]  # Logical Date des DAG-Runs (YYYY-MM-DD)
    results = []
    skipped = 0

    for i, name in enumerate(artist_names):
        artist_data = _search_artist(name, token)

        if artist_data:
            artist_data["snapshot_date"] = snapshot_date
            results.append(artist_data)
        else:
            skipped += 1

        # Progress-Log alle 10 Artists + kurze Pause um Rate Limits zu vermeiden (ca. 80/min)
        if (i + 1) % 10 == 0:
            print(f"  ... {i + 1}/{len(artist_names)} Artists abgefragt ({len(results)} gefunden, {skipped} übersprungen)")
            time.sleep(1)

    print(f"✅ {len(results)} Artists von Spotify geholt, {skipped} übersprungen")

    # JSON-Snapshot in Landing Zone speichern
    snapshot = {
        "artists": results,
        "_meta": {
            "snapshot_date": snapshot_date,
            "source": "spotify-api",
            "total_queried": len(artist_names),
            "total_found": len(results),
            "total_skipped": skipped,
        },
    }

    s3 = _get_minio_client()
    key = f"{LANDING_PREFIX}/{snapshot_date}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"✅ Snapshot gespeichert: s3://{BUCKET}/{key}")


# ---------------------------------------------------------------------------
# Task 3: Landing Zone → iceberg.raw.spotify_artist_snapshots
# ---------------------------------------------------------------------------
def landing_to_raw(ds: str, **_):
    """
    Liest den Artist-Snapshot-JSON aus MinIO und schreibt Rows per Trino INSERT
    in die Raw-Tabelle. Idempotent: DELETE + INSERT pro Snapshot-Datum.

    Jeder wöchentliche Snapshot erzeugt neue Rows mit aktuellem Stand.
    Änderungen an popularity, followers oder genres werden im dbt Data Vault
    via Hashdiff erkannt und als neue Satellite-Rows historisiert (SCD2-Basis).
    """
    s3 = _get_minio_client()
    key = f"{LANDING_PREFIX}/{ds}.json"

    obj = s3.get_object(Bucket=BUCKET, Key=key)
    snapshot = json.loads(obj["Body"].read())
    artists = snapshot.get("artists", [])

    hook = TrinoHook(trino_conn_id="trino_default")

    # Tabelle anlegen, falls noch nicht vorhanden
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
            artist_id           VARCHAR,
            artist_name         VARCHAR,
            genres              VARCHAR,
            popularity          INTEGER,
            followers           BIGINT,
            snapshot_date       DATE,
            _source_file        VARCHAR,
            _loaded_at          TIMESTAMP
        )
        WITH (
            format       = 'PARQUET',
            partitioning = ARRAY['snapshot_date']
        )
    """)

    # Idempotenz: bestehende Rows für dieses Snapshot-Datum löschen
    hook.run(f"""
        DELETE FROM {RAW_TABLE}
        WHERE snapshot_date = DATE '{ds}'
    """)

    if not artists:
        print(f"⚠️  Keine Artists im Snapshot für {ds} – überspringe INSERT")
        return

    source_file = f"s3://{BUCKET}/{key}"
    loaded_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    rows = []
    for a in artists:
        # Werte sicher in SQL-Literale umwandeln
        artist_id = str(a["artist_id"]).replace("'", "''")
        artist_name = str(a["artist_name"]).replace("'", "''")
        genres = str(a.get("genres", "")).replace("'", "''")
        popularity = a.get("popularity")
        followers = a.get("followers")

        pop_val = str(int(popularity)) if popularity is not None else "NULL"
        fol_val = str(int(followers)) if followers is not None else "NULL"

        rows.append(
            f"('{artist_id}', "
            f"'{artist_name}', "
            f"'{genres}', "
            f"{pop_val}, "
            f"{fol_val}, "
            f"DATE '{ds}', "
            f"'{source_file}', "
            f"TIMESTAMP '{loaded_at}')"
        )

        # Batch-Insert in Blöcken von 500 Rows
        if len(rows) >= 500:
            insert_sql = f"INSERT INTO {RAW_TABLE} VALUES {', '.join(rows)}"
            hook.run(insert_sql)
            rows = []

    # Rest-Batch schreiben
    if rows:
        insert_sql = f"INSERT INTO {RAW_TABLE} VALUES {', '.join(rows)}"
        hook.run(insert_sql)

    print(f"✅ {len(artists)} Artist-Snapshots in {RAW_TABLE} geschrieben (Snapshot: {ds})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="spotify_artist_update",
    description="Wöchentlicher Spotify-Artist-Snapshot → Landing Zone → iceberg.raw (SCD2-Basis)",
    start_date=datetime(2026, 3, 24),
    schedule="0 5 * * 1",  # Jeden Montag um 05:00 UTC
    catchup=False,          # Kein Backfill nötig – erster Run liefert Baseline
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["spotify", "api", "raw", "scd2"],
) as dag:

    t1_names = PythonOperator(
        task_id="fetch_artist_names",
        python_callable=fetch_artist_names,
    )

    t2_api = PythonOperator(
        task_id="fetch_and_store_artists",
        python_callable=fetch_and_store_artists,
    )

    t3_raw = PythonOperator(
        task_id="landing_to_raw",
        python_callable=landing_to_raw,
    )

    t1_names >> t2_api >> t3_raw
