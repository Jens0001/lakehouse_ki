-- generate_dimension_sk: Erzeugt einen deterministischen Surrogate Key für Dimensionen.
--
-- Berechnet MD5 über die Konkatenation aller übergebenen Spalten, getrennt durch '|'.
-- Pipe-Separator verhindert Hash-Kollisionen bei Wert-Konkatenation
-- (z.B. 'A' || '|' || 'BC' ≠ 'AB' || '|' || 'C').
--
-- Verwendung: {{ generate_dimension_sk(['location_hk', 'valid_from']) }} as location_sk
--
-- Typischer Einsatz: SK = md5(hub_hash_key || '|' || valid_from)
-- → eindeutig pro Dimensionsversion, deterministisch, idempotent, SCD2-fähig.
-- → Kein Integer-Surrogate nötig (Trino/Iceberg hat keine Sequenzen).

{% macro generate_dimension_sk(columns) -%}
    -- Trino: md5() erwartet varbinary, nicht varchar.
    -- Ablauf: Spalten als varchar konkatenieren → to_utf8() → md5() → to_hex() → varchar
    lower(to_hex(md5(to_utf8(
        {%- for col in columns %}
            cast({{ col }} as varchar)
            {%- if not loop.last %} || '|' || {% endif %}
        {%- endfor %}
    ))))
{%- endmacro %}
