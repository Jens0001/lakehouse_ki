{{
    config(materialized='table')
}}

-- energy_price_trends: Aggregierter Mart für Preistrend-Analysen
-- Liefert Wochen- und Monatsmittel pro Gebotszone.
-- Struktur analog zu weather_trends für konsistentes Dashboard-Design.
-- Joins über price_zone_sk (Surrogate Key aus dim_price_zone).
--
-- Granularitäten:
--   daily:   letzte 90 Tage (Tageswerte aus fact_energy_price_daily)
--   weekly:  alle Wochen (Wochenmittel ab 2018-10-01)
--   monthly: alle Monate (Monatsmittel ab 2018-10-01)

with daily as (
    select
        f.price_zone_sk,
        f.date_key,
        f.price_min,
        f.price_max,
        f.price_avg,
        f.price_stddev,
        f.negative_price_hours,
        d.year,
        d.month,
        d.month_name,
        d.week,
        d.days_ago
    from {{ ref('fact_energy_price_daily') }} f
    inner join {{ ref('dim_date') }} d
        on f.date_id = d.date_id
),

weekly as (
    select
        price_zone_sk,
        year,
        week,
        min(date_key)                           as week_start,
        round(avg(price_avg), 2)                as price_avg,
        round(min(price_min), 2)                as price_min,
        round(max(price_max), 2)                as price_max,
        round(avg(price_stddev), 2)             as price_stddev,
        sum(negative_price_hours)               as negative_price_hours
    from daily
    group by price_zone_sk, year, week
),

monthly as (
    select
        price_zone_sk,
        year,
        month,
        month_name,
        round(avg(price_avg), 2)                as price_avg,
        round(min(price_min), 2)                as price_min,
        round(max(price_max), 2)                as price_max,
        round(avg(price_stddev), 2)             as price_stddev,
        sum(negative_price_hours)               as negative_price_hours
    from daily
    group by price_zone_sk, year, month, month_name
)

-- letzte 90 Tage täglich + gesamte Wochen- und Monatsaggregation
select
    z.bidding_zone,
    'daily'                             as granularity,
    cast(d.date_key as varchar)         as period_label,
    d.year,
    d.month,
    d.week,
    d.price_avg,
    d.price_min,
    d.price_max,
    d.price_stddev,
    d.negative_price_hours
from daily d
inner join {{ ref('dim_price_zone') }} z on d.price_zone_sk = z.price_zone_sk
where d.days_ago <= 90

union all

select
    z.bidding_zone,
    'weekly'                            as granularity,
    cast(w.year as varchar) || '-W' || lpad(cast(w.week as varchar), 2, '0')  as period_label,
    w.year,
    null                                as month,       -- Woche schneidet Monatsgrenzen
    w.week,
    w.price_avg,
    w.price_min,
    w.price_max,
    w.price_stddev,
    w.negative_price_hours
from weekly w
inner join {{ ref('dim_price_zone') }} z on w.price_zone_sk = z.price_zone_sk

union all

select
    z.bidding_zone,
    'monthly'                           as granularity,
    cast(m.year as varchar) || '-' || lpad(cast(m.month as varchar), 2, '0')  as period_label,
    m.year,
    m.month,
    null                                as week,
    m.price_avg,
    m.price_min,
    m.price_max,
    m.price_stddev,
    m.negative_price_hours
from monthly m
inner join {{ ref('dim_price_zone') }} z on m.price_zone_sk = z.price_zone_sk
