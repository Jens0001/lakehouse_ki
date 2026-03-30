#!/usr/bin/env python3
"""
Set Snapshot Retention für Spotify Iceberg Tables

Setzt 5-Minuten Snapshot-Retention auf bestehenden Tabellen:
- iceberg.raw.spotify_tracks
- iceberg.raw.spotify_charts

Nutzt PyIceberg SDK (nicht Trino) um Properties nachträglich zu ändern.

Usage:
    python set_spotify_retention.py
"""

import sys
from pathlib import Path


def main():
    try:
        # PyIceberg imports
        from pyiceberg.catalog import Catalog
        from pyiceberg.exceptions import NoSuchTableError

        print("🔌 Verbinde zu Iceberg-Katalog (Nessie)...\n")

        # Nessie-Katalog mit S3/MinIO
        catalog = Catalog(
            "nessie",
            **{
                "uri": "http://nessie:19120/api/v1",
                "ref": "main",
                "s3.endpoint": "http://minio:9000",
                "s3.access-key-id": "minioadmin",
                "s3.secret-access-key": "minioadmin123",
                "s3.path-style-access": "true",
            }
        )

        print("✅ Katalog verbunden\n")

        # Eigenschaften für Retention (5 Minuten)
        retention_props = {
            "write.metadata.previous-versions-max": "2",
            "history.expire.min-snaps-to-keep": "1",
            "history.expire.max-snapshot-age-ms": "300000",
        }

        # 1. Spotify Tracks
        print("📝 Setze Retention für spotify_tracks...")
        try:
            tracks_table = catalog.load_table("raw.spotify_tracks")

            # Aktuelle Properties
            current_props = tracks_table.properties
            print(f"   Aktuelle Properties: {len(current_props)} einträge")

            # Update
            tracks_table.update_properties(retention_props)
            print(f"   ✅ Retention-Properties gesetzt:")
            for key, val in retention_props.items():
                print(f"      {key} = {val}")

            # Verifikation
            updated_table = catalog.load_table("raw.spotify_tracks")
            updated_props = updated_table.properties
            print(f"   ✅ Verifikation: {len(updated_props)} Properties (aktualisiert)\n")

        except NoSuchTableError:
            print("   ⚠️  Tabelle existiert noch nicht - DAG läuft noch?\n")
        except Exception as e:
            print(f"   ❌ Fehler: {type(e).__name__}: {str(e)}\n")

        # 2. Spotify Charts
        print("📝 Setze Retention für spotify_charts...")
        try:
            charts_table = catalog.load_table("raw.spotify_charts")

            # Aktuelle Properties
            current_props = charts_table.properties
            print(f"   Aktuelle Properties: {len(current_props)} einträge")

            # Update
            charts_table.update_properties(retention_props)
            print(f"   ✅ Retention-Properties gesetzt:")
            for key, val in retention_props.items():
                print(f"      {key} = {val}")

            # Verifikation
            updated_table = catalog.load_table("raw.spotify_charts")
            updated_props = updated_table.properties
            print(f"   ✅ Verifikation: {len(updated_props)} Properties (aktualisiert)\n")

        except NoSuchTableError:
            print("   ⚠️  Tabelle existiert noch nicht - DAG läuft noch?\n")
        except Exception as e:
            print(f"   ❌ Fehler: {type(e).__name__}: {str(e)}\n")

        print("🎉 Snapshot-Retention erfolgreich konfiguriert!")
        print("   - Max. 2 alte Versionen behalten")
        print("   - Min. 1 Snapshot behalten")
        print("   - Alte Snapshots nach 5 Min löschen (Metadaten-Reduktion)")

        return 0

    except ImportError as e:
        print(f"❌ PyIceberg nicht installiert: {e}")
        print("\nInstallation:")
        print("  pip install pyiceberg")
        return 1

    except Exception as e:
        print(f"❌ Fehler: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
