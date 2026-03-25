{{
    config(
        materialized='incremental',
        unique_key=['location_hk', 'measured_at'],
        on_schema_change='fail'
    )
}}

-- fact_weather_hourly: stündliche Messwerte mit FKs auf alle Dimensionen
--
-- location_sk: Surrogate Key aus dim_location (FK, deterministischer Hash)
-- location_hk: bleibt als Referenz zum Data Vault Hub erhalten

with weather as (
    select * from {{ ref('s_weather_hourly') }}

    {% if is_incremental() %}
        where load_date > (select max(load_date) from {{ this }})
    {% endif %}
),

-- Join auf dim_location statt h_location: liefert den Surrogate Key (location_sk)
-- Aktuell kein SCD2 → einfacher Equi-Join (nur 1 Row pro HK in der Dimension)
dim_loc as (
    select location_sk, location_hk from {{ ref('dim_location') }}
)

select
    -- FKs
    dl.location_sk,                                          -- FK → dim_location (Surrogate Key)
    w.location_hk,                                           -- Referenz → h_location (Hub Hash Key)
    cast(date_format(w.date_key, '%Y%m%d') as integer)  as date_id,
    w.hour                                               as time_id,

    -- Degenerate Dimensions
    w.measured_at,
    w.date_key,

    -- Messwerte
    w.temperature_2m,
    w.apparent_temperature,
    w.precipitation,
    w.windspeed_10m,
    w.weathercode,
    w.relative_humidity_2m,

    -- Technisch
    w.load_date,
    w._source_file

from weather w
inner join dim_loc dl on w.location_hk = dl.location_hk
