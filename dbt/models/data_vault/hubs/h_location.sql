{{
    config(
        materialized='incremental',
        unique_key='location_hk',
        on_schema_change='fail'
    )
}}

-- h_location: Hub für Messstandorte
-- Business Key: location_key (z.B. "berlin-reinickendorf")

with source as (
    select distinct
        location_hk,
        location_key        as location_bk,
        _loaded_at          as load_date,
        record_source
    from {{ ref('stg_weather') }}
)

select * from source

{% if is_incremental() %}
    where location_hk not in (select location_hk from {{ this }})
{% endif %}
