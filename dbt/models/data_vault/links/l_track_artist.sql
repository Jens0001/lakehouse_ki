{{
    config(
        materialized='incremental',
        unique_key='track_artist_hk',
        on_schema_change='fail'
    )
}}

-- l_track_artist: Link zwischen Track und Artist
-- Verknüpft h_track (track_hk) mit h_artist (artist_hk) – N:M-Beziehung,
-- da ein Track mehrere Artists haben kann (Kollaborationen) und ein Artist
-- an mehreren Tracks beteiligt ist.
--
-- track_artist_hk: eindeutiger Link-Hash-Key = md5(track_id || artist_bk)
-- Append-only: einmal geladene Links werden nie geändert.
--
-- Erster Link im Projekt – erweitert das Data Vault Modell um eine neue Strukturkomponente.

with source as (
    select
        {{ dbt_utils.generate_surrogate_key(['track_id', 'artist_bk']) }}
            as track_artist_hk,
        track_hk,
        artist_hk,
        min(_loaded_at)     as load_date,
        'spotify-kaggle'    as record_source
    from {{ ref('stg_spotify_track') }}
    group by
        {{ dbt_utils.generate_surrogate_key(['track_id', 'artist_bk']) }},
        track_hk,
        artist_hk
)

select * from source

{% if is_incremental() %}
    where track_artist_hk not in (select track_artist_hk from {{ this }})
{% endif %}
