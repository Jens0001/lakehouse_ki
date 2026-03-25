{{
    config(materialized='table')
}}

-- dim_track: aktuelle Track-Dimension (kein SCD2, immer letzter Stand)
--
-- Vereint Track-Stammdaten (s_track_details) mit Audio Features (s_track_audio_features).
-- Beide Satellites werden per track_hk gejoined; jeweils die aktuellste Version (rn=1).
--
-- track_sk: Surrogate Key = md5(track_hk || '|' || valid_from)
--   → deterministisch, idempotent, SCD2-fähig (falls später nötig)
--
-- Materialisierung: TABLE (Full Refresh, für Dremio-Kompatibilität)

with details_ranked as (
    -- Aktuellste Track-Stammdaten pro Track
    select
        h.track_hk,
        h.track_bk                           as track_id,
        s.track_name,
        s.album_name,
        s.duration_ms,
        s.explicit,
        s.popularity,
        s.track_genre,
        s.load_date                          as valid_from,
        row_number() over (
            partition by h.track_hk
            order by s.load_date desc
        ) as rn
    from {{ ref('h_track') }} h
    inner join {{ ref('s_track_details') }} s
        on h.track_hk = s.track_hk
),

audio_ranked as (
    -- Aktuellste Audio Features pro Track
    select
        track_hk,
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
        row_number() over (
            partition by track_hk
            order by load_date desc
        ) as rn
    from {{ ref('s_track_audio_features') }}
)

select
    -- Surrogate Key: eindeutiger Dimensionsschlüssel (PK)
    {{ generate_dimension_sk(['d.track_hk', 'd.valid_from']) }} as track_sk,

    -- Hub-Referenz
    d.track_hk,
    d.track_id,

    -- Stammdaten
    d.track_name,
    d.album_name,
    d.duration_ms,
    d.explicit,
    d.popularity,
    d.track_genre,

    -- Audio Features
    a.danceability,
    a.energy,
    a.musical_key,
    a.loudness,
    a.musical_mode,
    a.speechiness,
    a.acousticness,
    a.instrumentalness,
    a.liveness,
    a.valence,
    a.tempo,
    a.time_signature,

    d.valid_from

from details_ranked d
left join audio_ranked a
    on d.track_hk = a.track_hk
    and a.rn = 1
where d.rn = 1
