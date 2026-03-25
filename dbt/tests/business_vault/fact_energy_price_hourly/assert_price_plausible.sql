-- Test: Day-Ahead-Preise müssen in einem physikalisch und markttechnisch plausiblen Bereich liegen.
--
-- Untere Grenze -500 EUR/MWh: Negative Preise sind normal bei Überproduktion aus Erneuerbaren.
--   Historisch niedrigster bekannter Wert im DE-LU Markt: ca. -500 EUR/MWh (extreme Situationen)
-- Obere Grenze 3000 EUR/MWh: Höchstwert im DE-LU Markt während der Energiekrise 2022: ~870 EUR/MWh.
--   3000 als Sicherheitspuffer; alles darüber deutet auf API-Fehler oder Einheitenfehler hin.
--
-- NULL-Werte werden nicht geprüft – sie sind erlaubt (fehlende Stunden der API).
--
-- Jede ungültige Row lässt den Test fehlschlagen.

select
    price_zone_hk,
    measured_at,
    price_eur_mwh
from {{ ref('fact_energy_price_hourly') }}
where price_eur_mwh is not null
  and (price_eur_mwh < -500 or price_eur_mwh > 3000)
