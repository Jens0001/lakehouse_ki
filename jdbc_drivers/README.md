# JDBC Drivers

Dieses Verzeichnis enthält JDBC-Treiber für Oracle und IBM DB2.

Die JAR-Dateien sind **nicht im Git-Repository** (`.gitignore`).

## Treiber herunterladen

Beide Treiber sind kostenlos über Maven Central verfügbar (kein Account nötig):

```bash
# Oracle JDBC Treiber (ojdbc11 – kompatibel mit Oracle 19c, 21c, 23ai)
curl -L "https://repo1.maven.org/maven2/com/oracle/database/jdbc/ojdbc11/23.6.0.24.10/ojdbc11-23.6.0.24.10.jar" \
  -o jdbc_drivers/ojdbc11.jar

# IBM DB2 JDBC Treiber (jcc – kompatibel mit DB2 11.x und Db2 on Cloud)
curl -L "https://repo1.maven.org/maven2/com/ibm/db2/jcc/11.5.9.0/jcc-11.5.9.0.jar" \
  -o jdbc_drivers/db2jcc.jar
```

## Treiber-Versionen

| Datei         | Artifact                                    | Version       | Kompatibilität           |
|---------------|---------------------------------------------|---------------|--------------------------|
| `ojdbc11.jar` | `com.oracle.database.jdbc:ojdbc11`          | 23.6.0.24.10  | Oracle 19c, 21c, 23ai    |
| `db2jcc.jar`  | `com.ibm.db2:jcc`                           | 11.5.9.0      | DB2 11.5, Db2 on Cloud   |

## Verwendung in Airflow

Die JARs werden per Docker-Volume read-only in beide Airflow-Container gemountet:

```
Host: ./jdbc_drivers/
Container: /opt/jdbc_drivers/ (read-only)
```

Die Umgebungsvariable `CLASSPATH=/opt/jdbc_drivers/ojdbc11.jar:/opt/jdbc_drivers/db2jcc.jar`
wird in `docker-compose.yml` für beide Airflow-Services gesetzt.
JayDeBeApi (Python-JDBC-Bridge) und JPype1 (JVM-Integration) laden die Treiber automatisch.

## JDBC Connection Strings

### Oracle
```
JDBC URL:    jdbc:oracle:thin:@//hostname:1521/servicename
Driver:      oracle.jdbc.OracleDriver
JAR:         /opt/jdbc_drivers/ojdbc11.jar
```

### IBM DB2
```
JDBC URL:    jdbc:db2://hostname:50000/databasename
Driver:      com.ibm.db2.jcc.DB2Driver
JAR:         /opt/jdbc_drivers/db2jcc.jar
```
