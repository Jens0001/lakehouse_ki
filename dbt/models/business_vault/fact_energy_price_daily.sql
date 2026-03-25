{{
    config(
        materialized='incremental',
        unique_key=['price_zone_hk', 'date_key'],
        on_schema_change='fail'
    )
}}

-- fact_energy_price_daily: Tagesaggregation der Day-Ahead-Spotpreise
--
-- Aggregiert aus fact_energy_price_hourly.
-- Liefert Min, Max, Durchschnitt und Standardabweichung pro Tag und Gebotszone.
-- Nützlich für Trendanalysen und als Basis für den späteren Stromtarif-Vergleich.
--
-- price_zone_sk wird aus der stündlichen Faktentabelle durchgereicht (dort per Join auf dim_price_zone gesetzt).

with hourly as (
    select * from {{ ref('fact_energy_price_hourly') }}

    {% if is_incremental() %}
        -- Nur neue Tage laden: alles nach dem letzten bereits bekannten Tag
        where date_key > (select max(date_key) from {{ this }})
    {% endif %}
)

select
    -- Fremdschlüssel
    price_zone_sk,                                              -- FK → dim_price_zone (Surrogate Key)
    price_zone_hk,                                              -- Referenz → h_price_zone (Hub Hash Key)
    date_id,
    date_key,

    -- Preiskennzahlen des Tages (NULL-Werte bleiben NULL, werden von agg ignoriert)
    min(price_eur_mwh)                                      as price_min,
    max(price_eur_mwh)                                      as price_max,
    round(avg(price_eur_mwh), 4)                            as price_avg,

    -- Standardabweichung: Maß für Preisvolatilität – hohe Werte = volatiler Tag
    round(stddev_pop(price_eur_mwh), 4)                     as price_stddev,

    -- Anzahl Stunden mit negativem Preis (Überproduktion aus Erneuerbaren)
    count_if(price_eur_mwh < 0)                             as negative_price_hours,

    -- Technisch
    max(load_date)                                          as load_date

from hourly
group by price_zone_sk, price_zone_hk, date_id, date_key
