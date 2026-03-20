{{
    config(
        materialized='incremental',
        unique_key='weather_hashdiff',
        on_schema_change='fail'
    )
}}

-- s_weather_hourly: Satellit mit stündlichen Messwerten
-- Jede Stunde eines Standorts ergibt eine Row (historisiert via Hashdiff)

with source as (
    select
        location_hk,
        weather_hashdiff,
        measured_at,
        date_key,
        hour,
        temperature_2m,
        apparent_temperature,
        precipitation,
        windspeed_10m,
        weathercode,
        relative_humidity_2m,
        _source_file,
        _loaded_at          as load_date,
        record_source
    from {{ ref('stg_weather') }}
)

select * from source

{% if is_incremental() %}
    where weather_hashdiff not in (select weather_hashdiff from {{ this }})
{% endif %}
