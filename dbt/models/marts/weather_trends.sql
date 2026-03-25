{{
    config(materialized='table')
}}

-- weather_trends: Aggregierter Mart für Trend-Analysen
-- Liefert Wochen- und Monatsmittel pro Standort.
-- Joins über location_sk (Surrogate Key aus dim_location).

with daily as (
    select
        f.location_sk,
        f.date_key,
        f.temperature_min,
        f.temperature_max,
        f.temperature_avg,
        f.precipitation_sum,
        f.windspeed_avg,
        d.year,
        d.month,
        d.month_name,
        d.week,
        d.is_current_month,
        d.is_last_month,
        d.is_current_year,
        d.days_ago
    from {{ ref('fact_weather_daily') }} f
    inner join {{ ref('dim_date') }} d
        on f.date_id = d.date_id
),

weekly as (
    select
        location_sk,
        year,
        week,
        min(date_key)           as week_start,
        round(avg(temperature_avg), 1) as temp_avg,
        round(min(temperature_min), 1) as temp_min,
        round(max(temperature_max), 1) as temp_max,
        round(sum(precipitation_sum), 1) as precipitation_total,
        round(avg(windspeed_avg), 1)   as windspeed_avg
    from daily
    group by location_sk, year, week
),

monthly as (
    select
        location_sk,
        year,
        month,
        month_name,
        round(avg(temperature_avg), 1) as temp_avg,
        round(min(temperature_min), 1) as temp_min,
        round(max(temperature_max), 1) as temp_max,
        round(sum(precipitation_sum), 1) as precipitation_total,
        round(avg(windspeed_avg), 1)   as windspeed_avg
    from daily
    group by location_sk, year, month, month_name
)

-- letzten 90 Tage täglich + Wochenaggregation der letzten 52 Wochen
select
    l.location_key,
    'daily'         as granularity,
    cast(d.date_key as varchar)  as period_label,
    d.year,
    d.month,
    d.week,
    d.temperature_avg           as temp_avg,
    d.temperature_min           as temp_min,
    d.temperature_max           as temp_max,
    d.precipitation_sum         as precipitation_total,
    d.windspeed_avg
from daily d
inner join {{ ref('dim_location') }} l on d.location_sk = l.location_sk
where d.days_ago <= 90

union all

select
    l.location_key,
    'weekly'        as granularity,
    cast(w.week_start as varchar),
    w.year,
    null            as month,
    w.week,
    w.temp_avg,
    w.temp_min,
    w.temp_max,
    w.precipitation_total,
    w.windspeed_avg
from weekly w
inner join {{ ref('dim_location') }} l on w.location_sk = l.location_sk

union all

select
    l.location_key,
    'monthly'       as granularity,
    w.month_name || ' ' || cast(w.year as varchar),
    w.year,
    w.month,
    null            as week,
    w.temp_avg,
    w.temp_min,
    w.temp_max,
    w.precipitation_total,
    w.windspeed_avg
from monthly w
inner join {{ ref('dim_location') }} l on w.location_sk = l.location_sk
