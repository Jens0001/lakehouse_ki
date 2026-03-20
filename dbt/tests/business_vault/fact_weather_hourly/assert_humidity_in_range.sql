-- Test: Relative Luftfeuchtigkeit muss zwischen 0% und 100% liegen.
--
-- Werte außerhalb dieses Bereichs sind physikalisch unmöglich und deuten
-- auf API-Fehler oder Typ-Konvertierungsprobleme hin.
--
-- Jede ungültige Row lässt den Test fehlschlagen.

select
    location_hk,
    measured_at,
    relative_humidity_2m
from {{ ref('fact_weather_hourly') }}
where relative_humidity_2m < 0
   or relative_humidity_2m > 100
