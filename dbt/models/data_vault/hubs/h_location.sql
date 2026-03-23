{{
    config(
        materialized='incremental',
        unique_key='location_hk',
        on_schema_change='fail'
    )
}}

-- h_location: Hub für Messstandorte
-- Business Key: location_key (z.B. "berlin-reinickendorf")
--
-- Hinweis: Die Rohdaten enthalten pro Ladevorgang unterschiedliche _loaded_at-Werte.
-- Ein einfaches SELECT DISTINCT reicht daher nicht zur Deduplizierung.
-- Stattdessen wird per GROUP BY auf den Business Key aggregiert und
-- der früheste Ladezeitpunkt (MIN) als load_date übernommen.

with source as (
    select
        location_hk,
        location_key        as location_bk,
        min(_loaded_at)     as load_date,
        'open-meteo'        as record_source
    from {{ ref('stg_weather') }}
    group by location_hk, location_key
)

select * from source

{% if is_incremental() %}
    where location_hk not in (select location_hk from {{ this }})
{% endif %}
