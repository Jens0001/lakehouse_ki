-- Test: Temperaturwerte müssen physikalisch plausibel sein.
--
-- Werte außerhalb von -40°C bis +50°C sind für mitteleuropäische Stationen
-- ein klares Indiz für API-Fehler, Einheitenfehler oder Datencorruption.
--
-- Jede ungültige Row lässt den Test fehlschlagen.

select
    location_hk,
    measured_at,
    temperature_2m
from {{ ref('fact_weather_hourly') }}
where temperature_2m < -40
   or temperature_2m > 50
