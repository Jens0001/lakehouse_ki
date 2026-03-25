-- stg_energy_price.sql
-- Ephemeral: wird als CTE in downstream-Modelle eingefügt, keine physische Tabelle.
-- Aufgaben: Hash-Keys berechnen, Typen sichern, NULL-Behandlung, Felder umbenennen.
--
-- Quelle: iceberg.raw.energy_price_hourly (befüllt durch DAG energy_charts_to_raw)
-- Daten:  Day-Ahead-Spotpreise in EUR/MWh für Gebotszone DE-LU, ab 01.10.2018

with source as (
    select * from {{ source('raw', 'energy_price_hourly') }}
),

transformed as (
    select
        -- Hash-Keys für Data Vault
        -- price_zone_hk: Surrogatschlüssel für die Gebotszone (Business Key: bidding_zone)
        {{ dbt_utils.generate_surrogate_key(['bidding_zone']) }}
            as price_zone_hk,

        -- price_hashdiff: eindeutiger Bezeichner je Messung (Zone + Zeitstempel)
        {{ dbt_utils.generate_surrogate_key(['bidding_zone', 'timestamp']) }}
            as price_hashdiff,

        -- Zeitfelder
        timestamp                               as measured_at,
        date_key,
        hour,

        -- Gebotszone
        bidding_zone,

        -- Messwert: Day-Ahead-Preis in EUR/MWh
        -- Negative Preise sind im DE-LU Markt real möglich (Überproduktion aus Erneuerbaren)
        cast(price_eur_mwh as double)           as price_eur_mwh,

        -- Technische Felder
        _source_file,
        _loaded_at,
        'energy-charts'                         as record_source

    from source
    -- Ungültige Zeilen herausfiltern: Zeitstempel und Zone sind Pflicht
    where timestamp    is not null
      and bidding_zone is not null
)

select * from transformed
