{{
    config(
        materialized='incremental',
        unique_key=['price_zone_hk', 'date_key'],
        on_schema_change='fail'
    )
}}

-- fact_energy_price_monthly: Monatsaggregation der Day-Ahead-Spotpreise
--
-- Aggregiert aus fact_energy_price_daily.
-- Liefert Min, Max, Durchschnitt und Standardabweichung pro Monat und Gebotszone.
-- Nützlich für langfristige Trendanalysen und als Basis für den späteren Stromtarif-Vergleich.
--
-- price_zone_sk wird aus der täglichen Faktentabelle durchgereicht (dort per Join auf dim_price_zone gesetzt).

with daily as (
    select * from {{ ref('fact_energy_price_daily') }}

    {% if is_incremental() %}
        -- Nur neue Monate laden: alles nach dem letzten bereits bekannten Monat
        where date_key > (select max(date_key) from {{ this }})
    {% endif %}
)

select
    -- Fremdschlüssel
    price_zone_sk,                                              -- FK → dim_price_zone (Surrogate Key)
    price_zone_hk,                                              -- Referenz → h_price_zone (Hub Hash Key)
    date_id,
    date_key,

    -- Preiskennzahlen des Monats (NULL-Werte bleiben NULL, werden von agg ignoriert)
    min(price_min)                                          as price_min,
    max(price_max)                                          as price_max,
    round(avg(price_avg), 4)                                as price_avg,

    -- Standardabweichung: Maß für Preisvolatilität – hohe Werte = volatiler Monat
    round(stddev_pop(price_avg), 4)                         as price_stddev,

    -- Anzahl Tage mit negativem Preis (Überproduktion aus Erneuerbaren)
    sum(negative_price_hours)                               as negative_price_hours,

    -- Technisch
    max(load_date)                                          as load_date

from daily
group by price_zone_sk, price_zone_hk, date_id, date_key