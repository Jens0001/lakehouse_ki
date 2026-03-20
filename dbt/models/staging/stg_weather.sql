-- stg_weather.sql
-- Ephemeral: wird als CTE in downstream-Modelle eingefügt, keine physische Tabelle.
-- Aufgaben: Hash-Keys berechnen, Typen prüfen, NULL-Behandlung, Felder umbenennen.

with source as (
    select * from {{ source('raw', 'weather_hourly') }}
),

transformed as (
    select
        -- Hash-Keys für Data Vault
        {{ dbt_utils.generate_surrogate_key(['location_key']) }}
            as location_hk,
        {{ dbt_utils.generate_surrogate_key(['location_key', 'latitude', 'longitude']) }}
            as location_hashdiff,
        {{ dbt_utils.generate_surrogate_key(['location_key', 'timestamp']) }}
            as weather_hashdiff,

        -- Zeitfelder
        timestamp                                   as measured_at,
        date_key,
        hour,

        -- Standort
        location_key,
        cast(latitude  as double)                   as latitude,
        cast(longitude as double)                   as longitude,

        -- Messwerte
        cast(temperature_2m      as double)         as temperature_2m,
        cast(apparent_temperature as double)        as apparent_temperature,
        cast(precipitation        as double)        as precipitation,
        cast(windspeed_10m        as double)        as windspeed_10m,
        cast(weathercode          as integer)       as weathercode,
        cast(relative_humidity_2m as double)        as relative_humidity_2m,

        -- Technische Felder
        _source_file,
        _loaded_at,
        'open-meteo'                                as record_source

    from source
    where timestamp is not null
      and location_key is not null
)

select * from transformed
