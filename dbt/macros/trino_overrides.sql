-- Trino-spezifische Adapter-Overrides für automate_dv
-- Trino nutzt Standard-SQL-Syntax; diese Makros ergänzen fehlende Trino-Implementierungen

{%- macro trino__get_escape_characters() %}
    {%- do return (('"', '"')) -%}
{%- endmacro %}

{%- macro trino__cast_date(column_str, as_string=false, alias=none) -%}
    {%- if as_string -%}
        CAST('{{ column_str }}' AS DATE)
    {%- else -%}
        CAST({{ column_str }} AS DATE)
    {%- endif -%}
    {%- if alias %} AS {{ alias }} {%- endif %}
{%- endmacro -%}

{%- macro trino__cast_datetime(column_str, as_string=false, alias=none, date_type=none) -%}
    {%- if as_string -%}
        CAST('{{ column_str }}' AS TIMESTAMP)
    {%- else -%}
        CAST({{ column_str }} AS TIMESTAMP)
    {%- endif -%}
    {%- if alias %} AS {{ alias }} {%- endif %}
{%- endmacro -%}

{%- macro trino__type_timestamp() -%}
    TIMESTAMP
{%- endmacro -%}

{%- macro trino__type_binary(for_dbt_compare=false) -%}
    {%- set selected_hash = var('hash', 'MD5') | lower -%}
    {%- if for_dbt_compare -%}
        VARBINARY
    {%- elif selected_hash == 'md5' -%}
        VARBINARY
    {%- elif selected_hash == 'sha' -%}
        VARBINARY
    {%- else -%}
        VARBINARY
    {%- endif -%}
{%- endmacro -%}
