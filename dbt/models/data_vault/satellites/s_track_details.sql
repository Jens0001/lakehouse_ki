{{
    config(
        materialized='incremental',
        unique_key='track_hashdiff',
        on_schema_change='fail'
    )
}}

-- s_track_details: Satellit mit Track-Stammdaten (Name, Album, Dauer, Explicit-Flag)
-- Parent: h_track (track_hk)
--
-- Historisiert: neue Row bei Änderung der Stammdaten (über track_hashdiff).
-- Audio Features werden in einem separaten Satelliten geführt (s_track_audio_features),
-- da sie sich nicht ändern und eine andere Änderungsfrequenz haben.
--
-- Hinweis: Die Rohdaten enthalten pro Ladevorgang unterschiedliche _loaded_at-Werte.
-- Daher wird per GROUP BY auf den Hashdiff aggregiert und der früheste Ladezeitpunkt
-- als load_date übernommen.

with source as (
    select
        track_hk,
        track_hashdiff,
        track_name,
        album_name,
        duration_ms,
        explicit,
        popularity,
        track_genre,
        min(_loaded_at)     as load_date,
        'spotify-kaggle'    as record_source
    from {{ ref('stg_spotify_track') }}
    group by track_hk, track_hashdiff, track_name, album_name,
             duration_ms, explicit, popularity, track_genre
)

select * from source

{% if is_incremental() %}
    where track_hashdiff not in (select track_hashdiff from {{ this }})
{% endif %}
