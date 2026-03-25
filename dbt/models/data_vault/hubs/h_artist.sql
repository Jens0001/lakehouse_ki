{{
    config(
        materialized='incremental',
        unique_key='artist_hk',
        on_schema_change='fail'
    )
}}

-- h_artist: Hub für Spotify-Künstler
-- Business Key: artist_id (Spotify Artist ID aus der API) ODER artist_name (aus Kaggle)
--
-- Zwei Quellen speisen diesen Hub:
--   1. stg_spotify_track (Kaggle): artist_hk basiert auf lower(trim(artist_name))
--   2. stg_spotify_artist_snapshot (API): artist_hk basiert auf artist_id
--
-- Da Kaggle keine artist_id liefert, verwenden wir UNION ALL aus beiden Quellen.
-- Sobald ein Artist über die API geladen wurde, hat er einen zuverlässigen artist_id BK.
-- Der früheste Ladezeitpunkt (MIN) wird als load_date übernommen.
--
-- Hinweis: Zwischen Kaggle-artist_name und API-artist_id gibt es kein automatisches
-- Mapping. Für das Star Schema werden Charts (aus Kaggle) degeneriert geführt,
-- dim_artist basiert auf API-Snapshots (mit artist_id als BK).

with kaggle_artists as (
    -- Artists aus dem Kaggle-Tracks-Dataset (BK: bereinigter artist_name)
    select
        artist_hk,
        artist_bk               as artist_bk,
        min(_loaded_at)         as load_date,
        'spotify-kaggle'        as record_source
    from {{ ref('stg_spotify_track') }}
    group by artist_hk, artist_bk
),

api_artists as (
    -- Artists aus der Spotify API (BK: artist_id – zuverlässiger)
    select
        artist_hk,
        artist_id               as artist_bk,
        min(_loaded_at)         as load_date,
        'spotify-api'           as record_source
    from {{ ref('stg_spotify_artist_snapshot') }}
    group by artist_hk, artist_id
),

combined as (
    select * from kaggle_artists
    union all
    select * from api_artists
),

-- Deduplizierung: gleicher artist_hk kann aus beiden Quellen kommen
-- API-Quelle hat Vorrang (record_source = 'spotify-api' kommt nach 'spotify-kaggle' alphabetisch)
-- Daher: GROUP BY mit MIN(load_date) und MAX(record_source) für API-Bevorzugung
deduplicated as (
    select
        artist_hk,
        -- Falls es einen API-BK gibt, diesen bevorzugen; sonst Kaggle-BK
        max(case when record_source = 'spotify-api' then artist_bk end)
            as api_bk,
        min(artist_bk)          as fallback_bk,
        min(load_date)          as load_date,
        max(record_source)      as record_source
    from combined
    group by artist_hk
)

select
    artist_hk,
    coalesce(api_bk, fallback_bk) as artist_bk,
    load_date,
    record_source
from deduplicated

{% if is_incremental() %}
    where artist_hk not in (select artist_hk from {{ this }})
{% endif %}
