{{
    config(materialized='table')
}}

-- dim_country: Dimension für Länder/Regionen aus den Spotify-Charts
--
-- Enthält alle bekannten Regionscodes aus h_country.
-- Als TABLE materialisiert (kein SCD – Ländercodes ändern sich nicht).
--
-- country_sk: Surrogate Key = md5(country_hk || '|' || valid_from)
--   → deterministisch, idempotent
--
-- Das CASE-Mapping bildet die häufigsten Regionscodes auf lesbare Ländernamen ab.
-- Unbekannte Codes werden als Originalwert beibehalten.

select
    -- Surrogate Key: eindeutiger Dimensionsschlüssel (PK)
    {{ generate_dimension_sk(['h.country_hk', 'h.load_date']) }} as country_sk,

    -- Hub-Referenz (FK zurück zum Data Vault)
    h.country_hk,
    h.country_bk                                          as region_code,

    -- Ländername: Mapping der gängigsten Spotify-Regionscodes
    case h.country_bk
        when 'global' then 'Global'
        when 'de'     then 'Deutschland'
        when 'at'     then 'Österreich'
        when 'ch'     then 'Schweiz'
        when 'us'     then 'United States'
        when 'gb'     then 'United Kingdom'
        when 'fr'     then 'Frankreich'
        when 'es'     then 'Spanien'
        when 'it'     then 'Italien'
        when 'nl'     then 'Niederlande'
        when 'be'     then 'Belgien'
        when 'se'     then 'Schweden'
        when 'no'     then 'Norwegen'
        when 'dk'     then 'Dänemark'
        when 'fi'     then 'Finnland'
        when 'pl'     then 'Polen'
        when 'pt'     then 'Portugal'
        when 'br'     then 'Brasilien'
        when 'mx'     then 'Mexiko'
        when 'ar'     then 'Argentinien'
        when 'co'     then 'Kolumbien'
        when 'cl'     then 'Chile'
        when 'jp'     then 'Japan'
        when 'kr'     then 'Südkorea'
        when 'au'     then 'Australien'
        when 'nz'     then 'Neuseeland'
        when 'in'     then 'Indien'
        when 'ca'     then 'Kanada'
        when 'ie'     then 'Irland'
        when 'za'     then 'Südafrika'
        when 'tr'     then 'Türkei'
        when 'ru'     then 'Russland'
        when 'ph'     then 'Philippinen'
        when 'id'     then 'Indonesien'
        when 'th'     then 'Thailand'
        when 'tw'     then 'Taiwan'
        when 'hk'     then 'Hongkong'
        when 'sg'     then 'Singapur'
        when 'my'     then 'Malaysia'
        else upper(h.country_bk)
    end                                                     as country_name,

    h.load_date                                             as valid_from

from {{ ref('h_country') }} h
