{{
    config(
        materialized='incremental',
        unique_key=['location_hk', 'measured_at'],
        on_schema_change='fail'
    )
}}

-- fact_weather_hourly: stündliche Messwerte mit FKs auf alle Dimensionen

with weather as (
    select * from {{ ref('s_weather_hourly') }}

    {% if is_incremental() %}
        where load_date > (select max(load_date) from {{ this }})
    {% endif %}
),

locations as (
    select location_hk from {{ ref('h_location') }}
)

select
    -- FKs
    w.location_hk,
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
inner join locations l on w.location_hk = l.location_hk
