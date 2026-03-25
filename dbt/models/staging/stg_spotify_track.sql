-- stg_spotify_track.sql
-- Ephemeral: wird als CTE in downstream-Modelle eingefügt, keine physische Tabelle.
-- Aufgaben: Hash-Keys berechnen, Typen sichern, NULL-Behandlung, Felder umbenennen.
--
-- Quelle: iceberg.raw.spotify_tracks (befüllt durch DAG spotify_initial_load)
-- Daten:  Spotify-Tracks aus Kaggle-Dataset mit Audio Features
--
-- Hinweis: Das Kaggle-Dataset hat KEINE artist_id – nur artist_name.
-- Der artist_hk wird daher aus dem bereinigten artist_name gebildet.
-- Falls später Spotify-API-Daten dazukommen (mit artist_id), muss ein Mapping
-- über den s_artist_profile Satellite sichergestellt werden.

with source as (
    select * from {{ source('raw', 'spotify_tracks') }}
),

transformed as (
    select
        -- Hash-Keys für Data Vault
        -- track_hk: Surrogatschlüssel für den Track (Business Key: track_id)
        {{ dbt_utils.generate_surrogate_key(['track_id']) }}
            as track_hk,

        -- artist_hk: Surrogatschlüssel für den Künstler (Business Key: artist_name)
        -- Wird aus dem bereinigten artist_name gebildet (lower + trim)
        {{ dbt_utils.generate_surrogate_key(['lower(trim(artist_name))']) }}
            as artist_hk,

        -- track_hashdiff: Änderungserkennung für Track-Stammdaten
        {{ dbt_utils.generate_surrogate_key(['track_id', 'track_name', 'album_name', 'duration_ms', 'explicit']) }}
            as track_hashdiff,

        -- audio_hashdiff: Änderungserkennung für Audio Features (eigener Satellite)
        {{ dbt_utils.generate_surrogate_key(['track_id', 'danceability', 'energy', 'musical_key', 'loudness', 'musical_mode', 'speechiness', 'acousticness', 'instrumentalness', 'liveness', 'valence', 'tempo']) }}
            as audio_hashdiff,

        -- Business Keys
        track_id,
        lower(trim(artist_name))                    as artist_bk,

        -- Track-Stammdaten
        track_name,
        artist_name,
        album_name,
        cast(popularity as integer)                  as popularity,
        cast(duration_ms as integer)                 as duration_ms,
        explicit,
        track_genre,

        -- Audio Features (alle als DOUBLE für konsistente Typisierung)
        cast(danceability as double)                 as danceability,
        cast(energy as double)                       as energy,
        cast(musical_key as integer)                 as musical_key,
        cast(loudness as double)                     as loudness,
        cast(musical_mode as integer)                as musical_mode,
        cast(speechiness as double)                  as speechiness,
        cast(acousticness as double)                 as acousticness,
        cast(instrumentalness as double)             as instrumentalness,
        cast(liveness as double)                     as liveness,
        cast(valence as double)                      as valence,
        cast(tempo as double)                        as tempo,
        cast(time_signature as integer)              as time_signature,

        -- Technische Felder
        _source_file,
        _loaded_at,
        'spotify-kaggle'                             as record_source

    from source
    -- Ungültige Zeilen herausfiltern: Track-ID und Artist-Name sind Pflicht
    where track_id     is not null
      and artist_name  is not null
)

select * from transformed
