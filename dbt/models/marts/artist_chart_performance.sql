{{
    config(materialized='table')
}}

-- artist_chart_performance: Aggregierter Mart für Artist-Performance-Analysen
--
-- Zeigt pro Künstler und Zeitraum:
--   - Anzahl Chart-Einträge (wie oft in den Charts vertreten)
--   - Beste Position (niedrigster Wert = höchste Platzierung)
--   - Gesamtstreams im Zeitraum
--   - Anzahl verschiedener Tracks in den Charts
--   - Anzahl verschiedener Regionen, in denen der Artist chartet
--
-- Multi-granular (analog zu weather_trends / energy_price_trends):
--   - weekly:  Wochen-Aggregation
--   - monthly: Monats-Aggregation
--
-- Degenerate Dimension: artist_name wird direkt aus der Faktentabelle übernommen,
-- da kein zuverlässiger FK auf dim_artist existiert (Chart-Daten nur mit Namen).

with daily as (
    -- Basis: tägliche Chart-Einträge mit Kalender-Informationen
    select
        f.artist_name,
        f.chart_date,
        f.position,
        f.streams,
        f.track_name,
        f.region,
        f.chart_type,
        d.year,
        d.month,
        d.month_name,
        d.week
    from {{ ref('fact_chart_entry') }} f
    inner join {{ ref('dim_date') }} d
        on f.date_id = d.date_id
    -- Nur Top-200 Charts berücksichtigen (Viral-50 hat keine Streams)
    where f.chart_type = 'top200'
),

weekly as (
    select
        artist_name,
        year,
        week,
        min(chart_date)                             as week_start,
        count(*)                                    as chart_entries,
        min(position)                               as best_position,
        sum(streams)                                as total_streams,
        count(distinct track_name)                  as distinct_tracks,
        count(distinct region)                      as distinct_regions
    from daily
    group by artist_name, year, week
),

monthly as (
    select
        artist_name,
        year,
        month,
        month_name,
        count(*)                                    as chart_entries,
        min(position)                               as best_position,
        sum(streams)                                as total_streams,
        count(distinct track_name)                  as distinct_tracks,
        count(distinct region)                      as distinct_regions
    from daily
    group by artist_name, year, month, month_name
)

-- Wochen-Aggregation
select
    artist_name,
    'weekly'                                        as granularity,
    cast(w.year as varchar) || '-W' || lpad(cast(w.week as varchar), 2, '0')
                                                    as period_label,
    w.year,
    null                                            as month,
    w.week,
    w.chart_entries,
    w.best_position,
    w.total_streams,
    w.distinct_tracks,
    w.distinct_regions
from weekly w

union all

-- Monats-Aggregation
select
    artist_name,
    'monthly'                                       as granularity,
    cast(m.year as varchar) || '-' || lpad(cast(m.month as varchar), 2, '0')
                                                    as period_label,
    m.year,
    m.month,
    null                                            as week,
    m.chart_entries,
    m.best_position,
    m.total_streams,
    m.distinct_tracks,
    m.distinct_regions
from monthly m
