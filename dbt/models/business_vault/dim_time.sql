{{
    config(materialized='table')
}}

-- dim_time: Stunden-Dimension (24 Zeilen, statisch)
-- is_peak kennzeichnet typische Peak-Stunden (Morgen + Abend)
-- und ist für den späteren Stromtarif-Vergleich vorbereitet.

with hours as (
    select n as hour
    from unnest(sequence(0, 23)) as t(n)
)

select
    hour                                                 as time_id,
    hour,
    date_format(
        date_add('hour', hour, timestamp '2000-01-01 00:00:00'),
        '%H:00'
    )                                                    as label,

    -- Tagesabschnitt
    case
        when hour between 0 and 5  then 'Nacht'
        when hour between 6 and 9  then 'Morgen'
        when hour between 10 and 16 then 'Tag'
        when hour between 17 and 21 then 'Abend'
        else 'Nacht'
    end                                                  as period,

    -- Peak-Stunden: typische Hochlastzeiten (relevant für Stromtarif-Vergleich)
    hour between 7 and 9 or hour between 17 and 21      as is_peak

from hours
order by hour
