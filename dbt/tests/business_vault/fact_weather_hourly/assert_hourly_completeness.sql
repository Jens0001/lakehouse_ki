-- Test: Für jeden Tag und Standort müssen genau 24 Stunden vorliegen.
--
-- Hintergrund: fact_weather_daily aggregiert stündliche Werte zu Tageskennzahlen
-- (avg Temperatur, sum Niederschlag etc.). Fehlen Stunden, sind diese Werte still
-- verzerrt – ohne dass ein not_null-Test das bemerken würde.
--
-- Ein fehlerhafter Tag taucht als Row auf und lässt den Test fehlschlagen.

select
    location_hk,
    date_key,
    count(*) as hour_count
from {{ ref('fact_weather_hourly') }}
group by location_hk, date_key
having count(*) != 24
