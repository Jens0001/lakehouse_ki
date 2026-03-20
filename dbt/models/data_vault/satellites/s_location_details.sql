{{
    config(
        materialized='incremental',
        unique_key='location_hk',
        on_schema_change='fail'
    )
}}

-- s_location_details: Satellit mit beschreibenden Attributen des Standorts
-- Historisiert: neue Row bei Änderung der Koordinaten oder des Namens

with source as (
    select distinct
        location_hk,
        location_hashdiff,
        location_key,
        latitude,
        longitude,
        _loaded_at          as load_date,
        record_source
    from {{ ref('stg_weather') }}
)

select * from source

{% if is_incremental() %}
    where location_hashdiff not in (select location_hashdiff from {{ this }})
{% endif %}
