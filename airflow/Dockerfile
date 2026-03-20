FROM apache/airflow:3.1.8-python3.11

# 1. Wechsel zu Root, um System-Pakete zu installieren
USER root

# 2. Installiere Compiler und Header-Dateien (nötig für duckdb & dbt-core)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    gcc \
    g++ \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 3. Zurück zum Airflow-User für die Python-Installation
USER airflow

# 4. Pip-Pakete installieren (Versionen gepinnt für Reproduzierbarkeit)
# Airflow 3.1.8 mit Airflow 3.x APIs
# Provider-Versionen für Airflow 3.x (mindestens Major-Version 6 für Trino/HTTP)
# dbt-trino 1.10.1 benötigt: dbt-core>=1.8.0, dbt-adapters>=1.16, trino~=0.331
RUN pip install --no-cache-dir \
    "apache-airflow==3.1.8" \
    authlib \
    boto3 \
    "dbt-core==1.10.9" \
    "dbt-trino==1.10.1" \
    duckdb \
    pandas \
    requests \
    "apache-airflow-providers-trino>=6,<7" \
    "apache-airflow-providers-http>=6,<7"

# 5. Patch: dbt-trino 1.10.1 connections.py — Null-Guard für behavior_flags
#    (cls.behavior_flags ist None beim ersten Aufruf via dbt debug/dbt run)
RUN CONN_FILE=$(python -c "import importlib.util; s=importlib.util.find_spec('dbt.adapters.trino.connections'); print(s.origin)") && \
    sed -i 's/req_cert_val_flag = cls\.behavior_flags\.require_certificate_validation\.setting$/req_cert_val_flag = cls.behavior_flags.require_certificate_validation.setting if cls.behavior_flags else False/' "$CONN_FILE"