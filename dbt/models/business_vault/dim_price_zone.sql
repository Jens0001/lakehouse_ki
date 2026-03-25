{{
    config(materialized='table')
}}

-- dim_price_zone: aktuelle Gebotszone-Dimension
--
-- Enthält alle bekannten Gebotszonen aus h_price_zone.
-- Als TABLE materialisiert (kein SCD2 – eine Gebotszone ändert sich nie).
-- Grund: Dremio OSS kann Iceberg Views (Trino) nicht lesen – physische Tabelle ist Pflicht.
--
-- price_zone_sk: Surrogate Key = md5(price_zone_hk || '|' || valid_from)
--   → deterministisch, idempotent, eindeutig pro Dimensionsversion
--   → SCD2-fähig: bei Einführung von SCD2 entsteht pro Version ein neuer SK
-- price_zone_hk: bleibt als Referenz zum Data Vault Hub (h_price_zone) erhalten

select
    -- Surrogate Key: eindeutiger Dimensionsschlüssel (PK)
    {{ generate_dimension_sk(['h.price_zone_hk', 'h.load_date']) }} as price_zone_sk,

    -- Hub-Referenz (FK zurück zum Data Vault)
    h.price_zone_hk,
    h.zone_bk           as bidding_zone,
    h.load_date         as valid_from     -- Zeitpunkt der Ersterfassung dieser Zone
from {{ ref('h_price_zone') }} h
