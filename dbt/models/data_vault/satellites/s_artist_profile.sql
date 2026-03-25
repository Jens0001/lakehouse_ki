{{
    config(
        materialized='incremental',
        unique_key='artist_profile_hashdiff',
        on_schema_change='fail'
    )
}}

-- s_artist_profile: Satellit mit beschreibenden Attributen eines Spotify-Künstlers
-- Parent: h_artist (artist_hk)
--
-- ⭐ ZENTRAL FÜR SCD2: Dieser Satellite ist die Basis für dim_artist im Business Vault.
-- Jeder wöchentliche Snapshot (aus spotify_artist_update DAG) liefert den aktuellen Stand.
-- Wenn sich popularity, followers, genres oder artist_name ändert, entsteht ein neuer
-- artist_profile_hashdiff → neue Row hier → neue SCD2-Version in dim_artist.
--
-- Attribute: artist_name, genres, popularity, followers
-- Änderungshäufigkeit: wöchentlich (popularity + followers ändern sich regelmäßig)
--
-- Hinweis: Historisierung via Hashdiff – keine Updates, nur Inserts (append-only).
-- Duplikate werden durch den Hashdiff-Check beim inkrementellen Load verhindert.

with source as (
    select
        artist_hk,
        artist_profile_hashdiff,
        artist_name,
        genres,
        popularity,
        followers,
        snapshot_date,
        _source_file,
        _loaded_at          as load_date,
        record_source
    from {{ ref('stg_spotify_artist_snapshot') }}
)

select * from source

{% if is_incremental() %}
    -- Nur neue Profile-Versionen einfügen; bereits vorhandene via Hashdiff ausschließen
    -- Wenn sich nichts geändert hat (gleicher Hashdiff), wird die Row übersprungen
    where artist_profile_hashdiff not in (select artist_profile_hashdiff from {{ this }})
{% endif %}
