-- stg_spotify_chart.sql
-- Ephemeral: wird als CTE in downstream-Modelle eingefügt, keine physische Tabelle.
-- Aufgaben: Hash-Keys berechnen, Typen sichern, NULL-Behandlung, Felder umbenennen.
--
-- Quelle: iceberg.raw.spotify_charts (befüllt durch DAG spotify_initial_load)
-- Daten:  Tägliche Spotify-Charts aus Kaggle-Dataset (Top-200 + Viral-50 pro Region)
--
-- Hinweis: Das Charts-Dataset enthält track_name + artist_name als Text, keine IDs.
-- Ein exaktes Matching auf spotify_tracks ist nicht immer möglich (unterschiedliche
-- Schreibweisen, Sonderzeichen). Daher werden track_name und artist_name als
-- degenerierte Dimensionen direkt in der Faktentabelle geführt.

with source as (
    select * from {{ source('raw', 'spotify_charts') }}
),

transformed as (
    select
        -- Hash-Keys für Data Vault
        -- chart_hashdiff: Eindeutiger Bezeichner je Chart-Eintrag
        -- Kombination aus Track, Artist, Datum und Region = ein eindeutiger Eintrag
        {{ dbt_utils.generate_surrogate_key(['track_name', 'artist_name', 'chart_date', 'region']) }}
            as chart_hashdiff,

        -- country_hk: Surrogatschlüssel für das Land/die Region (Business Key: region)
        {{ dbt_utils.generate_surrogate_key(['region']) }}
            as country_hk,

        -- Chart-Felder
        chart_date,
        cast(position as integer)                    as position,
        track_name,
        artist_name,
        cast(streams as bigint)                      as streams,
        region,
        chart_type,
        trend,
        url,

        -- Technische Felder
        _source_file,
        _loaded_at,
        'spotify-kaggle'                             as record_source

    from source
    -- Ungültige Zeilen herausfiltern: Datum, Track und Region sind Pflicht
    where chart_date  is not null
      and track_name  is not null
      and region      is not null
)

select * from transformed
