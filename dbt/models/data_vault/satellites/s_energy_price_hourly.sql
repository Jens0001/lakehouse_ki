{{
    config(
        materialized='incremental',
        unique_key='price_hashdiff',
        on_schema_change='fail'
    )
}}

-- s_energy_price_hourly: Satellit mit stündlichen Day-Ahead-Spotpreisen je Gebotszone
--
-- Jede eindeutige Kombination aus Gebotszone und Zeitstempel ergibt eine Row.
-- Historisiert via price_hashdiff – keine Updates, nur Inserts (append-only).
-- Negative Preise (Überproduktion) und NULL-Preise werden 1:1 übernommen.

with source as (
    select
        price_zone_hk,
        price_hashdiff,
        measured_at,
        date_key,
        hour,
        price_eur_mwh,
        _source_file,
        _loaded_at          as load_date,
        record_source
    from {{ ref('stg_energy_price') }}
)

select * from source

{% if is_incremental() %}
    -- Nur neue Stunden-Rows einfügen; bereits vorhandene via Hashdiff ausschließen
    where price_hashdiff not in (select price_hashdiff from {{ this }})
{% endif %}
