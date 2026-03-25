{{
    config(materialized='table')
}}

-- dim_time: Viertelstunden-Dimension (96 Zeilen, statisch)
-- Granularität: 15 Minuten — entspricht der DE-LU EPEX SPOT Day-Ahead-Marktauflösung
-- time_id: 0..95 (= hour * 4 + quarter_of_hour)
-- is_peak kennzeichnet typische Peak-Stunden (Morgen + Abend)

with quarters as (
    select n as slot
    from unnest(sequence(0, 95)) as t(n)
)

select
    slot                                                 as time_id,
    slot / 4                                             as hour,
    (slot % 4) * 15                                      as minute_of_hour,
    date_format(
        date_add('minute', slot * 15, timestamp '2000-01-01 00:00:00'),
        '%H:%i'
    )                                                    as label,

    -- Tagesabschnitt (basierend auf der vollen Stunde)
    case
        when slot / 4 between 0 and 5  then 'Nacht'
        when slot / 4 between 6 and 9  then 'Morgen'
        when slot / 4 between 10 and 16 then 'Tag'
        when slot / 4 between 17 and 21 then 'Abend'
        else 'Nacht'
    end                                                  as period,

    -- Peak-Stunden: typische Hochlastzeiten (relevant für Stromtarif-Vergleich)
    slot / 4 between 7 and 9 or slot / 4 between 17 and 21  as is_peak

from quarters
order by slot
