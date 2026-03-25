{{
    config(
        materialized='incremental',
        unique_key=['price_zone_hk', 'measured_at'],
        on_schema_change='fail'
    )
}}

-- fact_energy_price_hourly: stündliche Day-Ahead-Spotpreise mit FKs auf alle Dimensionen
--
-- Granularität: 1 Row pro Gebotszone und Stunde
-- Dimensionen: price_zone_sk (→ dim_price_zone), date_id (→ dim_date), time_id (→ dim_time)
--
-- price_zone_sk: Surrogate Key aus dim_price_zone (FK, deterministischer Hash)
-- price_zone_hk: bleibt als Referenz zum Data Vault Hub erhalten

with prices as (
    select * from {{ ref('s_energy_price_hourly') }}

    {% if is_incremental() %}
        -- Nur neue Rows laden: alles nach dem letzten bereits bekannten Ladezeitpunkt
        where load_date > (select max(load_date) from {{ this }})
    {% endif %}
),

-- Join auf dim_price_zone statt h_price_zone: liefert den Surrogate Key (price_zone_sk)
-- Aktuell kein SCD2 → einfacher Equi-Join (nur 1 Row pro HK in der Dimension)
dim_zone as (
    select price_zone_sk, price_zone_hk from {{ ref('dim_price_zone') }}
)

select
    -- Fremdschlüssel
    dz.price_zone_sk,                                                            -- FK → dim_price_zone (Surrogate Key)
    p.price_zone_hk,                                                             -- Referenz → h_price_zone (Hub Hash Key)
    cast(date_format(p.date_key, '%Y%m%d') as integer)                          as date_id,   -- FK → dim_date
    cast(hour(p.measured_at) * 4 + minute(p.measured_at) / 15 as integer)       as time_id,   -- FK → dim_time (0..95)

    -- Degenerate Dimensions
    p.measured_at,
    p.date_key,

    -- Messwert
    p.price_eur_mwh,    -- EUR/MWh; kann NULL (fehlende Stunden) oder negativ (Überproduktion) sein

    -- Technisch
    p.load_date,
    p._source_file

from prices p
inner join dim_zone dz on p.price_zone_hk = dz.price_zone_hk
