-- Test: Für jeden Tag und jede Gebotszone müssen eine plausible Anzahl Zeitpunkte vorliegen.
--
-- Die Energy-Charts API liefert DE-LU Day-Ahead-Preise in zwei Granularitäten:
--   Historische Daten (bis ca. 2025): stündlich → 24 Einträge/Tag (Stunden-Produkt)
--   Neuere Daten                    : 15-minutlich → 96 Einträge/Tag (Viertelstunden-Produkt)
--
-- DST-Ausnahmen (Europe/Berlin):
--   März-Umstellung (23h-Tag):  23 Stunden × 1  = 23  | 23 × 4 = 92 Viertelstunden
--   Oktober-Umstellung (25h-Tag): 25 Stunden × 1 = 25  | 25 × 4 = 100 Viertelstunden
--
-- Alle anderen Zählungen deuten auf echte Datenlücken oder Duplikate hin.
-- Ein fehlerhafter Tag taucht als Row auf und lässt den Test fehlschlagen.

select
    price_zone_hk,
    date_key,
    count(*) as interval_count
from {{ ref('fact_energy_price_hourly') }}
group by price_zone_hk, date_key
having count(*) not in (23, 24, 25, 92, 96, 100)
