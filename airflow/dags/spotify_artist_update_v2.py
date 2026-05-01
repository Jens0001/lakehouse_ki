"""
DAG: spotify_artist_update_v2
Optimierte Version des Spotify Artist Updates.
- Parallelisierung der API-Abfragen via ThreadPoolExecutor.
- XCom-Offloading der Artist-Liste via S3 (Landing Zone).
- Optimierter Bulk-Insert in Trino.
"""

from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

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
TMP_PREFIX = "landing/tmp/spotify_artist_names"
RAW_TABLE = "iceberg.raw.spotify_artist_snapshots"
RAW_TRACKS_TABLE = "iceberg.raw.spotify_tracks"

# Spotify API Endpoints
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"

# Performance Settings
MAX_WORKERS = 1        # Parallelität der API-Abfragen
INSERT_BATCH_SIZE = 250
CHECKPOINT_SIZE = 5000  # Nach je N gefundenen Artists: S3-Batch + Raw-Insert

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
    """Holt ein OAuth2 Access Token vom Spotify Accounts Service."""
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
    """Sucht einen Künstler über die Spotify Search API mit Retry-Logik."""
    tid = threading.current_thread().name
    params = {
        "q": artist_name,
        "type": "artist",
        "limit": 1,
    }

    for attempt in range(3):
        try:
            print(f"  [{tid}] GET '{artist_name}' (Versuch {attempt + 1}) ...")
            resp = requests.get(
                SPOTIFY_SEARCH_URL,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=15,
            )
            print(f"  [{tid}] '{artist_name}' → HTTP {resp.status_code}")

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                print(f"  [{tid}] Rate-limited (Retry-After: {retry_after}s) – warte...")
                time.sleep(retry_after)
                continue

            if resp.status_code == 401:
                print(f"  [{tid}] Token abgelaufen, hole neuen Token")
                token = _get_spotify_token()
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
            time.sleep(0.5)
            return None
        except requests.exceptions.Timeout:
            print(f"  [{tid}] TIMEOUT nach 15s für '{artist_name}' (Versuch {attempt + 1})")
        except requests.exceptions.ConnectionError as e:
            print(f"  [{tid}] VERBINDUNGSFEHLER für '{artist_name}': {e}")
        except Exception as e:
            print(f"  [{tid}] Fehler bei '{artist_name}' (Versuch {attempt + 1}): {type(e).__name__}: {e}")
        time.sleep(1)

    return None


def _insert_artists_to_raw(hook, artists: list, snapshot_date: str, source_file: str) -> None:
    """Schreibt eine Liste von Artist-Dicts in Bulk-Batches nach Trino."""
    loaded_at = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    rows = []
    for a in artists:
        artist_id   = str(a["artist_id"]).replace("'", "''")
        artist_name = str(a["artist_name"]).replace("'", "''")
        genres      = str(a.get("genres", "")).replace("'", "''")
        pop_val     = str(int(a["popularity"])) if a.get("popularity") is not None else "NULL"
        fol_val     = str(int(a["followers"]))  if a.get("followers")  is not None else "NULL"
        rows.append(
            f"('{artist_id}', '{artist_name}', '{genres}', {pop_val}, {fol_val}, "
            f"DATE '{snapshot_date}', '{source_file}', TIMESTAMP '{loaded_at}')"
        )
        if len(rows) >= INSERT_BATCH_SIZE:
            hook.run(f"INSERT INTO {RAW_TABLE} VALUES {', '.join(rows)}")
            rows = []
    if rows:
        hook.run(f"INSERT INTO {RAW_TABLE} VALUES {', '.join(rows)}")


# ---------------------------------------------------------------------------
# Task 1: Distinct Artist-Namen lesen & in S3 speichern (XCom-Offloading)
# ---------------------------------------------------------------------------
def fetch_artist_names(**context):
    """
    Liest Artist-Namen aus Trino und speichert sie als JSON-Datei in S3,
    um das Airflow XCom-Limit zu umgehen.
    """
    hook = TrinoHook(trino_conn_id="trino_default")
    records = hook.get_records(
        f"SELECT DISTINCT artist_name FROM {RAW_TRACKS_TABLE} WHERE artist_name IS NOT NULL"
    )

    unique_artists = set()
    for (raw_name,) in records:
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

    # Speichern in Landing Zone statt XCom-Liste
    s3 = _get_minio_client()
    snapshot_date = context["ds"]
    key = f"{TMP_PREFIX}/{snapshot_date}.json"
    
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(artist_list, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    
    print(f"✅ Artist-Liste gespeichert: s3://{BUCKET}/{key}")
    return key


# ---------------------------------------------------------------------------
# Task 2: Spotify API parallel abfragen + JSON Snapshot
# ---------------------------------------------------------------------------
def fetch_and_store_artists(**context):
    """
    Liest Artist-Liste aus S3 und nutzt ThreadPoolExecutor für parallele API-Calls.
    """
    # S3 Key aus Task 1 holen
    s3_key = context["ti"].xcom_pull(task_ids="fetch_artist_names")
    if not s3_key:
        print("⚠️  Kein S3-Key für Artist-Namen gefunden")
        return

    s3 = _get_minio_client()
    obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
    artist_names = json.loads(obj["Body"].read())
    
    if not artist_names:
        print("⚠️  Keine Artist-Namen in Datei gefunden")
        return

    token = _get_spotify_token()
    snapshot_date = context["ds"]
    results = []
    batch = []
    batch_num = 0
    skipped = 0

    # Connectivity-Check: schlägt er fehl, sofort mit klarem Fehler abbrechen
    print("Prüfe Spotify API Erreichbarkeit...")
    try:
        check = requests.get(SPOTIFY_SEARCH_URL, headers={"Authorization": f"Bearer {token}"},
                             params={"q": "test", "type": "artist", "limit": 1}, timeout=10)
        print(f"Spotify API erreichbar: HTTP {check.status_code}")
        if check.status_code == 429:
            retry_after = check.headers.get("Retry-After", "unbekannt")
            raise RuntimeError(f"Spotify API rate-limited (429, Retry-After: {retry_after}s) – später erneut versuchen")
    except requests.exceptions.Timeout:
        raise RuntimeError("Spotify API antwortet nicht (Timeout nach 10s) – Netzwerk prüfen")
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Spotify API nicht erreichbar (ConnectionError): {e}")

    # Raw-Tabelle einmalig anlegen + alte Rows für diesen Snapshot löschen (Idempotenz)
    hook = TrinoHook(trino_conn_id="trino_default")
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
    hook.run(f"DELETE FROM {RAW_TABLE} WHERE snapshot_date = DATE '{snapshot_date}'")

    def _flush_batch():
        nonlocal batch, batch_num
        if not batch:
            return
        batch_num += 1
        batch_key = f"{LANDING_PREFIX}/{snapshot_date}_batch_{batch_num:03d}.json"
        s3.put_object(
            Bucket=BUCKET,
            Key=batch_key,
            Body=json.dumps(batch, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        _insert_artists_to_raw(hook, batch, snapshot_date, f"s3://{BUCKET}/{batch_key}")
        print(f"  ✅ Checkpoint {batch_num}: {len(batch)} Artists geschrieben "
              f"(gesamt: {len(results) + len(batch)}/{len(artist_names)})")
        results.extend(batch)
        batch.clear()

    print(f"🚀 Starte parallele Abfrage von {len(artist_names)} Artists mit {MAX_WORKERS} Workern...")

    # Kein 'with'-Block: shutdown(wait=False) bei Timeout verhindert endloses Hängen
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    future_to_artist = {executor.submit(_search_artist, name, token): name for name in artist_names}

    count = 0
    try:
        for future in as_completed(future_to_artist):
            count += 1
            try:
                artist_data = future.result(timeout=30)
            except TimeoutError:
                print(f"  TIMEOUT für '{future_to_artist[future]}', wird übersprungen")
                skipped += 1
                continue
            except Exception as e:
                print(f"  FEHLER für '{future_to_artist[future]}': {e}, wird übersprungen")
                skipped += 1
                continue
            if artist_data:
                artist_data["snapshot_date"] = snapshot_date
                batch.append(artist_data)
                if len(batch) >= CHECKPOINT_SIZE:
                    _flush_batch()
            else:
                skipped += 1

            if count % 100 == 0:
                print(f"  ... {count}/{len(artist_names)} verarbeitet ({len(results) + len(batch)} gefunden)")
        executor.shutdown(wait=True)
    except Exception as exc:
        remaining = len(artist_names) - count
        print(f"  ABBRUCH: {type(exc).__name__}: {exc} – {count}/{len(artist_names)} verarbeitet, {remaining} noch offen")
        executor.shutdown(wait=False, cancel_futures=True)
        print("  Executor beendet – hängende Threads laufen im Hintergrund bis Worker-Neustart")

    _flush_batch()  # Restmenge < CHECKPOINT_SIZE schreiben

    print(f"✅ {len(results)} Artists geholt, {skipped} übersprungen")

    # Finales komplettes JSON für Audit / manuelle Nutzung
    final_key = f"{LANDING_PREFIX}/{snapshot_date}.json"
    s3.put_object(
        Bucket=BUCKET,
        Key=final_key,
        Body=json.dumps(
            {
                "artists": results,
                "_meta": {
                    "snapshot_date": snapshot_date,
                    "source": "spotify-api",
                    "total_queried": len(artist_names),
                    "total_found": len(results),
                    "total_skipped": skipped,
                    "checkpoints": batch_num,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"✅ Finaler Snapshot gespeichert: s3://{BUCKET}/{final_key}")


# ---------------------------------------------------------------------------
# Task 3: Manueller Reload Landing Zone → Raw (nicht mehr in der DAG-Pipeline)
# ---------------------------------------------------------------------------
def landing_to_raw(ds: str, **_):
    """Lädt finalen JSON-Snapshot manuell in die Raw-Tabelle (Fallback/Reparatur)."""
    s3 = _get_minio_client()
    key = f"{LANDING_PREFIX}/{ds}.json"
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    artists = json.loads(obj["Body"].read()).get("artists", [])

    if not artists:
        print(f"⚠️  Keine Artists im Snapshot für {ds}")
        return

    hook = TrinoHook(trino_conn_id="trino_default")
    hook.run(f"DELETE FROM {RAW_TABLE} WHERE snapshot_date = DATE '{ds}'")
    _insert_artists_to_raw(hook, artists, ds, f"s3://{BUCKET}/{key}")
    print(f"✅ {len(artists)} Artist-Snapshots in {RAW_TABLE} geschrieben (Snapshot: {ds})")


# ---------------------------------------------------------------------------
# DAG-Definition
# ---------------------------------------------------------------------------
with DAG(
    dag_id="spotify_artist_update_v2",
    description="OPTIMIERT: Wöchentlicher Spotify-Artist-Snapshot → Landing Zone → iceberg.raw",
    start_date=datetime(2026, 3, 24),
    schedule="0 5 * * 1",
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["spotify", "api", "raw", "scd2", "optimized"],
) as dag:

    t1_names = PythonOperator(
        task_id="fetch_artist_names",
        python_callable=fetch_artist_names,
    )

    t2_api = PythonOperator(
        task_id="fetch_and_store_artists",
        python_callable=fetch_and_store_artists,
    )

    t1_names >> t2_api