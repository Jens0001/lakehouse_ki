-- PostgreSQL Init Script für Airflow & Keycloak
-- Note: airflow database und user werden automatisch von PostgreSQL erstellt
--       basierend auf POSTGRES_USER und POSTGRES_PASSWORD Environment-Variablen

-- Erstelle Keycloak Role
CREATE ROLE keycloak WITH LOGIN PASSWORD 'keycloak123';

-- Erstelle Keycloak Database
CREATE DATABASE keycloak OWNER keycloak;

-- Grant privileges on airflow database to airflow user
GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow;

-- Grant privileges on keycloak database to keycloak user
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;

-- Connect to airflow database and grant schema privileges
\c airflow;
GRANT ALL ON SCHEMA public TO airflow;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO airflow;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO airflow;

-- Connect to keycloak database and grant schema privileges
\c keycloak;
GRANT ALL ON SCHEMA public TO keycloak;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO keycloak;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO keycloak;