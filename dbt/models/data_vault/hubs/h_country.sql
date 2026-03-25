{{
    config(
        materialized='incremental',
        unique_key='country_hk',
        on_schema_change='fail'
    )
}}

-- h_country: Hub für Länder/Regionen aus den Spotify-Charts
-- Business Key: region (Ländercode z.B. 'de', 'us', 'global')
--
-- Da eine Region unveränderlich ist (kein SCD nötig), genügt der Hub
-- als reiner Business-Key-Anker. Neue Regionen werden inkrementell ergänzt.
-- Eine Dimensionstabelle (dim_country) wird im Business Vault aus diesem Hub
-- erzeugt, mit einem CASE-Mapping von region → Ländername.

with source as (
    select
        country_hk,
        region              as country_bk,
        min(_loaded_at)     as load_date,
        'spotify-kaggle'    as record_source
    from {{ ref('stg_spotify_chart') }}
    group by country_hk, region
)

select * from source

{% if is_incremental() %}
    -- Nur neue Regionen einfügen; bestehende nie überschreiben
    where country_hk not in (select country_hk from {{ this }})
{% endif %}
