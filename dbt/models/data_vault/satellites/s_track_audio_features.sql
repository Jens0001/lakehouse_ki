{{
    config(
        materialized='incremental',
        unique_key='audio_hashdiff',
        on_schema_change='fail'
    )
}}

-- s_track_audio_features: Satellit mit Audio-Analyse-Features eines Tracks
-- Parent: h_track (track_hk)
--
-- Separater Satellite, da Audio Features von Spotify algorithmisch berechnet werden
-- und sich praktisch nie ändern – anders als Track-Stammdaten (Name, Popularity).
-- Diese Trennung folgt dem Data Vault 2.0 Prinzip "Split by rate of change".
--
-- Attribute: danceability, energy, key, loudness, mode, speechiness, acousticness,
--            instrumentalness, liveness, valence, tempo, time_signature

with source as (
    select
        track_hk,
        audio_hashdiff,
        danceability,
        energy,
        musical_key,
        loudness,
        musical_mode,
        speechiness,
        acousticness,
        instrumentalness,
        liveness,
        valence,
        tempo,
        time_signature,
        min(_loaded_at)     as load_date,
        'spotify-kaggle'    as record_source
    from {{ ref('stg_spotify_track') }}
    group by track_hk, audio_hashdiff, danceability, energy, musical_key,
             loudness, musical_mode, speechiness, acousticness, instrumentalness,
             liveness, valence, tempo, time_signature
)

select * from source

{% if is_incremental() %}
    where audio_hashdiff not in (select audio_hashdiff from {{ this }})
{% endif %}
