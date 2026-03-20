#!/bin/bash
# MinIO Bucket- und Ordner-Initialisierung
# Wird vom minio-init Container beim Stack-Start ausgeführt.

set -e

echo "⏳ Warte auf MinIO..."
until mc alias set lakehouse http://minio:9000 "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" --api S3v4 2>/dev/null; do
  echo "  MinIO noch nicht bereit, warte 2s..."
  sleep 2
done
echo "✅ MinIO erreichbar"

# Bucket anlegen
echo "📦 Erstelle Bucket..."
mc mb --ignore-existing lakehouse/lakehouse

# Landing-Zone Prefixes anlegen (leere Marker-Objekte)
echo "📁 Erstelle Landing-Zone Struktur..."
echo "" | mc pipe lakehouse/lakehouse/landing/.keep
echo "" | mc pipe lakehouse/lakehouse/landing/csv/.keep
echo "" | mc pipe lakehouse/lakehouse/landing/json/.keep
echo "" | mc pipe lakehouse/lakehouse/landing/excel/.keep
echo "" | mc pipe lakehouse/lakehouse/landing/parquet/.keep

# Airflow-Logs Bucket
mc mb --ignore-existing lakehouse/airflow-logs

echo "✅ Buckets und Prefixes erstellt:"
mc ls lakehouse/lakehouse/
mc ls lakehouse/lakehouse/landing/