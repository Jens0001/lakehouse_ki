{{
    config(materialized='table')
}}

-- dim_date: Kalender-Dimension 2018-2030
-- Beginnt am 2018-01-01 um die Energy-Charts-Preisdaten ab 01.10.2018 abzudecken.
-- (Vorher begann sie 2020-01-01, was zu relationship-Test-Fehlern führte.)
-- Als TABLE materialisiert (business_vault-Default), täglicher Full Refresh um 01:00 Uhr.
-- Relative Felder (is_yesterday etc.) bleiben max. 1h veralten (00:00-01:00 Uhr), was akzeptabel ist.
-- Grund: Dremio OSS kann Iceberg Views (Trino) nicht lesen – physische Tabelle ist Pflicht.

with dates as (
    select
        date_add('day', n, date '2018-01-01') as full_date
    from unnest(sequence(0, 4747)) as t(n)
    where date_add('day', n, date '2018-01-01') <= date '2030-12-31'
)

select
    -- Primärschlüssel (YYYYMMDD als Integer für schnelle Joins)
    cast(date_format(full_date, '%Y%m%d') as integer)   as date_id,
    full_date,

    -- Kalenderfelder
    year(full_date)                                      as year,
    quarter(full_date)                                   as quarter,
    month(full_date)                                     as month,
    date_format(full_date, '%M')                         as month_name,
    week(full_date)                                      as week,
    day(full_date)                                       as day,
    day_of_week(full_date)                               as weekday_num,   -- 1=Mo, 7=So
    date_format(full_date, '%W')                         as weekday_name,
    day_of_week(full_date) >= 6                          as is_weekend,

    -- Relative Felder (täglich aktuell durch täglichen Full Refresh um 01:00 Uhr)
    full_date = current_date                             as is_today,
    full_date = current_date - interval '1' day          as is_yesterday,
    full_date >= date_trunc('week', current_date)        as is_current_week,
    full_date >= date_trunc('week', current_date)
        - interval '7' day
        and full_date < date_trunc('week', current_date) as is_last_week,
    full_date >= date_trunc('month', current_date)       as is_current_month,
    full_date >= date_trunc('month', current_date)
        - interval '1' month
        and full_date < date_trunc('month', current_date) as is_last_month,
    full_date >= date_trunc('year', current_date)        as is_current_year,
    full_date >= date_trunc('year', current_date)
        - interval '1' year
        and full_date < date_trunc('year', current_date) as is_last_year,
    date_diff('day', full_date, current_date)            as days_ago

from dates
