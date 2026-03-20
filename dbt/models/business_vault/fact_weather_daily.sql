{{
    config(
        materialized='incremental',
        unique_key=['location_hk', 'date_key'],
        on_schema_change='fail'
    )
}}

-- fact_weather_daily: Tagesaggregation aus fact_weather_hourly

with hourly as (
    select * from {{ ref('fact_weather_hourly') }}

    {% if is_incremental() %}
        where date_key > (select max(date_key) from {{ this }})
    {% endif %}
)

select
    -- FKs
    location_hk,
    date_id,
    date_key,

    -- Temperatur
    min(temperature_2m)         as temperature_min,
    max(temperature_2m)         as temperature_max,
    avg(temperature_2m)         as temperature_avg,
    min(apparent_temperature)   as apparent_temp_min,
    max(apparent_temperature)   as apparent_temp_max,

    -- Niederschlag
    sum(precipitation)          as precipitation_sum,
    count_if(precipitation > 0) as precipitation_hours,

    -- Wind
    avg(windspeed_10m)          as windspeed_avg,
    max(windspeed_10m)          as windspeed_max,

    -- Luftfeuchtigkeit
    avg(relative_humidity_2m)   as humidity_avg,

    -- Technisch
    max(load_date)              as load_date

from hourly
group by location_hk, date_id, date_key
