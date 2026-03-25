{{
    config(materialized='table')
}}

-- ⭐ dim_artist: Slowly Changing Dimension Type 2 für Spotify-Künstler
--
-- SCD2-Logik: Jede Änderung an artist_name, genres, popularity oder followers
-- erzeugt eine neue Dimensionsversion mit eigenem Surrogate Key (artist_sk).
-- Alte Versionen behalten ihren SK und werden via valid_to/is_current als
-- historisch markiert. Fakten-Joins verwenden immer den aktuell gültigen SK.
--
-- Quelle: h_artist (Business Key) + s_artist_profile (Attribute mit History)
--
-- artist_sk: Surrogate Key = md5(artist_hk || '|' || valid_from)
--   → deterministisch, idempotent, eindeutig pro Dimensionsversion
--   → Neuer SK bei jeder SCD2-Version (anderer valid_from)
--
-- Materialisierung: TABLE (Full Refresh), da SCD2-Logik alle Versionen
-- aus dem Satellite neu berechnen muss.

with versions as (
    -- Alle historischen Versionen eines Artists aus dem Satellite
    -- Sortiert nach load_date → älteste Version zuerst
    select
        h.artist_hk,
        h.artist_bk,
        s.artist_name,
        s.genres,
        s.popularity,
        s.followers,
        s.load_date                                         as valid_from,

        -- valid_to: Zeitpunkt der NÄCHSTEN Version (LEAD) → Ende der Gültigkeit
        -- NULL bei der aktuellsten Version (= aktuell gültig)
        lead(s.load_date) over (
            partition by h.artist_hk
            order by s.load_date
        )                                                   as valid_to,

        -- Versionsnummer: 1 = älteste, aufsteigend
        row_number() over (
            partition by h.artist_hk
            order by s.load_date
        )                                                   as version_number

    from {{ ref('h_artist') }} h
    inner join {{ ref('s_artist_profile') }} s
        on h.artist_hk = s.artist_hk
)

select
    -- Surrogate Key: eindeutiger Dimensionsschlüssel pro Version (PK)
    {{ generate_dimension_sk(['artist_hk', 'valid_from']) }} as artist_sk,

    -- Hub-Referenz (FK zurück zum Data Vault)
    artist_hk,
    artist_bk,

    -- Beschreibende Attribute (Stand zum Zeitpunkt valid_from bis valid_to)
    artist_name,
    genres,
    popularity,
    followers,

    -- SCD2-Felder
    valid_from,
    valid_to,
    version_number,

    -- is_current: TRUE für die aktuellste Version (valid_to IS NULL)
    -- Für Fakten-Joins auf den aktuellen Stand: WHERE is_current = TRUE
    valid_to is null                                        as is_current

from versions
