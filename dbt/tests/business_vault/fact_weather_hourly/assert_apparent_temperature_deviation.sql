-- Test: Gefühlte Temperatur darf nicht mehr als 20°C von der Lufttemperatur abweichen.
--
-- apparent_temperature berücksichtigt Wind und Luftfeuchtigkeit (Windchill / Hitzeindex).
-- Eine Abweichung > 20°C deutet auf fehlerhafte Berechnungen oder Datenfehler hin.
--
-- Jede ungültige Row lässt den Test fehlschlagen.

select
    location_hk,
    measured_at,
    temperature_2m,
    apparent_temperature,
    abs(apparent_temperature - temperature_2m) as delta
from {{ ref('fact_weather_hourly') }}
where abs(apparent_temperature - temperature_2m) > 20
