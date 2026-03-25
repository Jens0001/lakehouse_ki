{{
    config(materialized='table')
}}

-- dim_location: aktuelle Standort-Dimension (kein SCD2, immer letzter Stand)
--
-- location_sk: Surrogate Key = md5(location_hk || '|' || valid_from)
--   → deterministisch, idempotent, eindeutig pro Dimensionsversion
--   → SCD2-fähig: bei Einführung von SCD2 entsteht pro Version ein neuer SK
-- location_hk: bleibt als Referenz zum Data Vault Hub (h_location) erhalten

with ranked as (
    select
        h.location_hk,
        h.location_bk                           as location_key,
        s.latitude,
        s.longitude,
        s.load_date                             as valid_from,
        row_number() over (
            partition by h.location_hk
            order by s.load_date desc
        ) as rn
    from {{ ref('h_location') }} h
    inner join {{ ref('s_location_details') }} s
        on h.location_hk = s.location_hk
)

select
    -- Surrogate Key: eindeutiger Dimensionsschlüssel (PK)
    {{ generate_dimension_sk(['location_hk', 'valid_from']) }} as location_sk,

    -- Hub-Referenz (FK zurück zum Data Vault)
    location_hk,
    location_key,
    latitude,
    longitude,
    valid_from
from ranked
where rn = 1
