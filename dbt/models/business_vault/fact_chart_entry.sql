{{
    config(
        materialized='incremental',
        unique_key=['chart_hashdiff'],
        on_schema_change='fail'
    )
}}

-- fact_chart_entry: Tägliche Spotify-Chart-Einträge mit FKs auf alle Dimensionen
--
-- Granularität: 1 Row pro Track, Region und Tag
-- Dimensionen: country_sk (→ dim_country), date_id (→ dim_date)
-- Degenerate Dimensions: track_name, artist_name (kein FK auf dim_track/dim_artist,
--   da Charts-Dataset nur Namen ohne Spotify-IDs enthält)
--
-- Measures: position (Chart-Platzierung), streams (Anzahl Plays)
--
-- Hinweis: Ein Join auf dim_artist (SCD2) wäre über artist_name als Text möglich,
-- aber unzuverlässig (Schreibweisen, Sonderzeichen). Daher werden track_name und
-- artist_name als degenerierte Dimensionen direkt in der Faktentabelle geführt.
-- Als Erweiterung könnte ein Fuzzy-Match oder ein Mapping via Spotify Search API
-- eingeführt werden, um fact_chart_entry mit dim_track/dim_artist zu verknüpfen.

with charts as (
    select * from {{ ref('s_chart_entry') }}

    {% if is_incremental() %}
        -- Nur neue Rows laden: alles nach dem letzten bereits bekannten Ladezeitpunkt
        where load_date > (select max(load_date) from {{ this }})
    {% endif %}
),

-- Join auf dim_country: liefert den Surrogate Key (country_sk)
dim_cty as (
    select country_sk, country_hk from {{ ref('dim_country') }}
)

select
    -- Primärschlüssel (Hashdiff aus dem Satellite)
    c.chart_hashdiff,

    -- Fremdschlüssel
    dc.country_sk,                                                              -- FK → dim_country (Surrogate Key)
    c.country_hk,                                                               -- Referenz → h_country (Hub Hash Key)
    cast(date_format(c.chart_date, '%Y%m%d') as integer)                       as date_id,   -- FK → dim_date

    -- Degenerate Dimensions (Track/Artist als Text, kein FK)
    c.track_name,
    c.artist_name,

    -- Chart-Felder
    c.chart_date,
    c.position,
    c.streams,
    c.region,
    c.chart_type,
    c.trend,
    c.url,

    -- Technisch
    c.load_date,
    c._source_file

from charts c
inner join dim_cty dc on c.country_hk = dc.country_hk
