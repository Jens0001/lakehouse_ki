{{
    config(
        materialized='incremental',
        unique_key='chart_hashdiff',
        on_schema_change='fail'
    )
}}

-- s_chart_entry: Transaktionaler Satellit für Spotify-Chart-Einträge
-- Kein klassischer beschreibender Satellit, sondern ein "Effectivity Satellite":
-- Jeder Chart-Eintrag ist ein eigenständiges Geschäftsereignis (Transaktion).
--
-- Verknüpfung: country_hk → h_country
-- Degenerate Dimension: track_name, artist_name (kein FK auf h_track, da Charts-Dataset
--   nur Namen ohne IDs enthält und ein exaktes Matching nicht zuverlässig ist)
--
-- chart_hashdiff: Eindeutiger Bezeichner pro Chart-Eintrag
--   (track_name + artist_name + chart_date + region)
--
-- Append-only: Einmal geladene Chart-Einträge werden nie geändert.

with source as (
    select
        chart_hashdiff,
        country_hk,
        chart_date,
        position,
        track_name,
        artist_name,
        streams,
        region,
        chart_type,
        trend,
        url,
        _source_file,
        _loaded_at          as load_date,
        record_source
    from {{ ref('stg_spotify_chart') }}
)

select * from source

{% if is_incremental() %}
    -- Nur neue Chart-Einträge einfügen; bereits vorhandene via Hashdiff ausschließen
    where chart_hashdiff not in (select chart_hashdiff from {{ this }})
{% endif %}
