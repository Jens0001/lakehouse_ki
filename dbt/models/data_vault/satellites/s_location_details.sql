{{
    config(
        materialized='incremental',
        unique_key='location_hk',
        on_schema_change='fail'
    )
}}

-- s_location_details: Satellit mit beschreibenden Attributen des Standorts
-- Historisiert: neue Row bei Änderung der Koordinaten oder des Namens
--
-- Hinweis: Die Rohdaten enthalten pro Ladevorgang unterschiedliche _loaded_at-Werte.
-- Daher wird per GROUP BY auf den Hashdiff (= eindeutige Attributkombination)
-- aggregiert und der früheste Ladezeitpunkt als load_date übernommen.

with source as (
    select
        location_hk,
        location_hashdiff,
        location_key,
        latitude,
        longitude,
        min(_loaded_at)     as load_date,
        'open-meteo'        as record_source
    from {{ ref('stg_weather') }}
    group by location_hk, location_hashdiff, location_key, latitude, longitude
)

select * from source

{% if is_incremental() %}
    where location_hashdiff not in (select location_hashdiff from {{ this }})
{% endif %}
