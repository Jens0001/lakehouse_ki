FROM apache/airflow:3.1.8-python3.11

# 1. Wechsel zu Root, um System-Pakete zu installieren
USER root

# 2. Installiere Compiler, Header-Dateien (nötig für dbt-core) und Java JRE (nötig für JDBC-Provider)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    gcc \
    g++ \
    default-jre-headless \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# JAVA_HOME für JDBC-Provider setzen (jaydebeapi sucht JVM via JAVA_HOME)
ENV JAVA_HOME=/usr/lib/jvm/default-java
ENV PATH="$JAVA_HOME/bin:$PATH"

# 3. Zurück zum Airflow-User für die Python-Installation
USER airflow

# 4. Pip-Pakete installieren (Versionen gepinnt für Reproduzierbarkeit)
# Airflow 3.1.8, dbt-trino 1.10.1, Datenbankprovider für Trino/HTTP/PostgreSQL
# Oracle und DB2 werden BEIDE per JDBC angebunden (JayDeBeApi + JPype1 = Python-JDBC-Bridge)
# Hinweis: Kommentare NICHT innerhalb des pip-install-Befehls – das bricht Zeilenfortsetzung in sh
RUN pip install --no-cache-dir \
    "apache-airflow==3.1.8" \
    authlib \
    boto3 \
    "dbt-core==1.10.9" \
    "dbt-trino==1.10.1" \
    duckdb \
    kaggle \
    pandas \
    "pyiceberg[pyarrow,s3fs]" \
    requests \
    "apache-airflow-providers-trino>=6,<7" \
    "apache-airflow-providers-http>=6,<7" \
    "apache-airflow-providers-postgres>=5,<6" \
    "psycopg2-binary" \
    "apache-airflow-providers-jdbc>=4,<5" \
    "apache-airflow-providers-openlineage>=2,<3" \
    "JayDeBeApi" \
    "JPype1"

# 5. Patch: dbt-trino 1.10.1 connections.py — Null-Guard für behavior_flags
#    (cls.behavior_flags ist None beim ersten Aufruf via dbt debug/dbt run)
RUN CONN_FILE=$(python -c "import importlib.util; s=importlib.util.find_spec('dbt.adapters.trino.connections'); print(s.origin)") && \
    sed -i 's/req_cert_val_flag = cls\.behavior_flags\.require_certificate_validation\.setting$/req_cert_val_flag = cls.behavior_flags.require_certificate_validation.setting if cls.behavior_flags else False/' "$CONN_FILE"