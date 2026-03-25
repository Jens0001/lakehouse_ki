{{
    config(
        materialized='incremental',
        unique_key='track_hk',
        on_schema_change='fail'
    )
}}

-- h_track: Hub für Spotify-Tracks
-- Business Key: track_id (Spotify Track ID aus dem Kaggle-Dataset)
--
-- Hinweis: Die Rohdaten enthalten pro Ladevorgang unterschiedliche _loaded_at-Werte.
-- Ein einfaches SELECT DISTINCT reicht daher nicht zur Deduplizierung.
-- Stattdessen wird per GROUP BY auf den Business Key aggregiert und
-- der früheste Ladezeitpunkt (MIN) als load_date übernommen.

with source as (
    select
        track_hk,
        track_id            as track_bk,
        min(_loaded_at)     as load_date,
        'spotify-kaggle'    as record_source
    from {{ ref('stg_spotify_track') }}
    group by track_hk, track_id
)

select * from source

{% if is_incremental() %}
    where track_hk not in (select track_hk from {{ this }})
{% endif %}
