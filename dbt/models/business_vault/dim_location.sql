{{
    config(materialized='table')
}}

-- dim_location: aktuelle Standort-Dimension (kein SCD2, immer letzter Stand)

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
    location_hk,
    location_key,
    latitude,
    longitude,
    valid_from
from ranked
where rn = 1
