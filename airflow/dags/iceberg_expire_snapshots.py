"""
DAG: iceberg_expire_snapshots
Löscht abgelaufene Snapshots auf ALLEN Iceberg-Tabellen nach Retention-Policy.

Läuft täglich und entfernt Snapshots gemäß der jeweiligen Tabellen-Eigenschaften:
- history.expire.max-snapshot-age-ms
- write.metadata.previous-versions-max
- history.expire.min-snaps-to-keep

Reduziert Metadaten-Bloat kontinuierlich für alle Iceberg-Tabellen.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.trino.hooks.trino import TrinoHook


DEFAULT_ARGS = {
    "owner": "airflow",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def expire_all_iceberg_snapshots(**_):
    """
    Löscht abgelaufene Snapshots auf ALLEN Iceberg-Tabellen.

    Iteriert durch alle Namespaces (raw, data_vault, business_vault, marts)
    und führt EXECUTE expire_snapshots() auf jeder Tabelle aus.

    Retention Policy:
    - raw: 5 Minuten, max. 2 Snapshots
    - andere: Default-Werte (7 Tage, min. 1 Snapshot)
    """
    hook = TrinoHook(trino_conn_id="trino_default")

    # Liste aller Iceberg-Namespaces
    namespaces = ["raw", "data_vault", "business_vault", "marts"]

    total_tables = 0
    total_expired = 0

    for namespace in namespaces:
        print(f"\n📂 Verarbeite Namespace: {namespace}")

        # Bestimme Policy-Notiz basierend auf Namespace
        if namespace == "raw":
            policy_note = "(5 Min, max 2 Snapshots)"
        else:
            # Andere Namespaces: Default (7 Tage)
            policy_note = "(Default: 7 Tage)"

        try:
            # Alle Tabellen im Namespace auflisten
            tables = hook.get_records(
                f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{namespace}'"
            )

            if not tables:
                print(f"   (keine Tabellen)")
                continue

            for (table_name,) in tables:
                total_tables += 1
                full_name = f"iceberg.{namespace}.{table_name}"

                try:
                    # Führe expire_snapshots via CALL auf (Trino Iceberg Syntax)
                    if namespace == "raw":
                        # raw: mit Parametern (5 Min, max 2 Snapshots)
                        sql = f"CALL iceberg.system.expire_snapshots(table => '{full_name}', older_than_ms => 300000, retain_last => 2)"
                    else:
                        # Andere: Default (nur alte Snapshots löschen)
                        sql = f"CALL iceberg.system.expire_snapshots(table => '{full_name}')"

                    hook.run(sql)
                    print(f"   ✅ {table_name} {policy_note}")
                    total_expired += 1

                except Exception as e:
                    # Fehler bei einzelner Tabelle → skip und weitermachen
                    error_msg = str(e).lower()
                    if "does not exist" in error_msg or "not found" in error_msg:
                        print(f"   ⚠️  {table_name} (nicht gefunden)")
                    elif "cannot be executed" in error_msg or "cannot execute" in error_msg:
                        # Tabelle existiert aber expire_snapshots fehlgeschlagen (z.B. wegen Locks)
                        print(f"   ⚠️  {table_name} (expire_snapshots nicht möglich: {type(e).__name__})")
                    else:
                        print(f"   ⚠️  {table_name} (Fehler: {type(e).__name__}: {str(e)[:80]})")

        except Exception as e:
            print(f"   ❌ Fehler beim Abrufen von Tabellen: {type(e).__name__}")
            continue

    print(f"\n🎉 Fertig!")
    print(f"   Tabellen verarbeitet: {total_expired}/{total_tables}")
    print(f"   Abgelaufene Snapshots auf allen Tabellen gelöscht")


with DAG(
    dag_id="iceberg_expire_snapshots",
    description="Löscht alte Snapshots auf ALLEN Iceberg-Tabellen (Metadaten-Reduktion)",
    start_date=datetime(2026, 3, 28),
    schedule="0 2 * * *",  # Täglich 02:00 UTC
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["iceberg", "maintenance", "snapshots"],
) as dag:

    expire_all = PythonOperator(
        task_id="expire_all_iceberg_snapshots",
        python_callable=expire_all_iceberg_snapshots,
    )

    expire_all
