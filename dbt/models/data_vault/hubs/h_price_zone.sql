{{
    config(
        materialized='incremental',
        unique_key='price_zone_hk',
        on_schema_change='fail'
    )
}}

-- h_price_zone: Hub für Gebotszonen (z.B. DE-LU)
-- Business Key: bidding_zone
--
-- Da eine Gebotszone unveränderlich ist (kein SCD nötig), genügt der Hub
-- als reiner Business-Key-Anker. Neue Zonen werden inkrementell ergänzt.
-- Eine Dimensionstabelle (dim_price_zone) wird im Business Vault aus diesem Hub
-- und einer direkten Group-By auf die Staging-Quelle erzeugt.

with source as (
    select
        price_zone_hk,
        bidding_zone        as zone_bk,
        min(_loaded_at)     as load_date,    -- frühester Ladezeitpunkt als Ersterfassung
        'energy-charts'     as record_source
    from {{ ref('stg_energy_price') }}
    group by price_zone_hk, bidding_zone
)

select * from source

{% if is_incremental() %}
    -- Nur neue Gebotszonen einfügen; bestehende nie überschreiben
    where price_zone_hk not in (select price_zone_hk from {{ this }})
{% endif %}
