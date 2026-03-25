-- stg_spotify_artist_snapshot.sql
-- Ephemeral: wird als CTE in downstream-Modelle eingefügt, keine physische Tabelle.
-- Aufgaben: Hash-Keys berechnen, Typen sichern, NULL-Behandlung, Felder umbenennen.
--
-- Quelle: iceberg.raw.spotify_artist_snapshots (befüllt durch DAG spotify_artist_update)
-- Daten:  Wöchentliche Snapshots der Artist-Metadaten von der Spotify Web API
--
-- SCD2-Logik: Jeder Snapshot enthält den aktuellen Stand eines Künstlers.
-- Der artist_profile_hashdiff erkennt Änderungen an name, genres, popularity, followers.
-- Neue Hashdiff-Werte erzeugen neue Satellite-Rows → History im Data Vault.
-- Im Business Vault wird daraus dim_artist mit valid_from/valid_to/is_current (SCD2).

with source as (
    select * from {{ source('raw', 'spotify_artist_snapshots') }}
),

transformed as (
    select
        -- Hash-Keys für Data Vault
        -- artist_hk: Surrogatschlüssel für den Künstler (Business Key: artist_id)
        -- Verwendet artist_id von der Spotify API (zuverlässiger als artist_name)
        {{ dbt_utils.generate_surrogate_key(['artist_id']) }}
            as artist_hk,

        -- artist_profile_hashdiff: Änderungserkennung über ALLE beschreibenden Attribute
        -- Wenn sich popularity, followers, genres oder name ändert → neuer Hashdiff
        -- → neuer Satellite-Record → neue SCD2-Version in dim_artist
        {{ dbt_utils.generate_surrogate_key(['artist_id', 'artist_name', 'genres', 'popularity', 'followers']) }}
            as artist_profile_hashdiff,

        -- Business Key
        artist_id,

        -- Beschreibende Attribute (SCD2-Kandidaten)
        artist_name,
        genres,
        cast(popularity as integer)                  as popularity,
        cast(followers as bigint)                    as followers,

        -- Snapshot-Zeitstempel (wann dieser Stand aufgenommen wurde)
        snapshot_date,

        -- Technische Felder
        _source_file,
        _loaded_at,
        'spotify-api'                                as record_source

    from source
    -- Ungültige Zeilen herausfiltern: Artist-ID ist Pflicht
    where artist_id is not null
)

select * from transformed
