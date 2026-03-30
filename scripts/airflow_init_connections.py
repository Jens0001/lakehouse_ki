#!/usr/bin/env python3
"""
Airflow Connections Initialization Script

Creates default connections needed for DAGs to run.
Run this after Airflow DB is initialized.
(Airflow 3.x compatible)
"""

from airflow.models import Connection
from airflow.settings import engine
from sqlalchemy.orm import Session

def init_connections():
    """Create default Airflow connections"""

    with Session(engine) as session:
        # Trino Connection
        trino_conn = session.query(Connection).filter(
            Connection.conn_id == "trino_default"
        ).first()

        if not trino_conn:
            trino_conn = Connection(
                conn_id="trino_default",
                conn_type="trino",
                host="trino",
                port=8080,
                schema="default",  # Default schema
                extra={
                    "catalog": "iceberg",  # Iceberg Catalog (mit Nessie)
                }
            )
            session.add(trino_conn)
            print("✓ Created connection: trino_default")
        else:
            print("✓ Connection trino_default already exists")

        session.commit()

if __name__ == "__main__":
    try:
        init_connections()
        print("\nConnection initialization completed.")
    except Exception as e:
        print(f"\n❌ Error during connection initialization: {e}")
        import traceback
        traceback.print_exc()
        # Don't exit with error – let Airflow continue even if connections fail
        # (they can be created manually later)
        exit(0)
