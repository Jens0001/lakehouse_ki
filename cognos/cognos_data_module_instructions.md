# Cognos Analytics Data Module Generator – LLM Instructions

## Purpose
You generate valid IBM Cognos Analytics Data Module JSON files based on database schema information provided by the user.

## Important: Provenance of this format
This template and these instructions are **reverse-engineered** from actual Cognos Analytics Data Module JSON exports. IBM does **not** publish a formal JSON schema specification for this format. The official IBM Data Modeling Guide (available for versions 11.0, 11.1, 11.2, 12.0) only covers UI-based workflows and concepts. An IBM support article (TS016870211) describes how to export specs as JSON but is behind the IBM support login wall and does not document the JSON schema.

The `version` field in the JSON (e.g., `"22.0"`) is an **internal schema version counter** — it does NOT correspond to the Cognos Analytics product version (11.x, 12.x). This schema has been stable across multiple product releases.

### IBM terminology mapping
In Cognos documentation and UI:
- **Query subject** = a table or view in the data module
- **Query item** = a column within a query subject
- **Data module** = the overall model containing query subjects, relationships, and metadata
- **Package** = an older Framework Manager artifact; packages can also be used as sources for data modules

## Export & Import workflows
- **Export via UI**: In the Data Module editor, press `Ctrl + / + Q` to view/export the full JSON specification.
- **Export via REST API**: `GET /api/v1/content/{storeID}/items` returns module metadata.
- **Import via REST API**: The REST API supports importing metadata for schema objects (see IBM docs on REST API for Cognos Analytics 12.0.x).
- **Dashboard spec shortcut**: For dashboards, `Ctrl + .` (period) exports the dashboard specification.
- **Validation workflow**: After generating a module, import it, then re-export via `Ctrl+/+Q` and compare the re-exported JSON against your generated version to verify correctness.

## Input you receive
The user provides:
1. **Tables/Views**: name, columns with data types
2. **Relationships**: foreign key / join conditions between tables  
3. **Data source name**: the Cognos data source connection name and schema
4. **Optional**: custom labels, drill hierarchies, hidden columns

## Output you produce
A single JSON file conforming to the Cognos Data Module format. The template file `cognos_data_module_template.json` contains the full structure with documentation.

---

## Step-by-step generation process

### Step 1: Generate unique IDs
- Create one hex ID for `use` array: pattern `i` + 32 uppercase hex chars
- Create one `storeID` for the useSpec
- Create two `storeID` values for ancestors

### Step 2: Build `useSpec`
Fill in the data source connection details exactly like this:

  "useSpec": [
    {
      "identifier": "M1",
      "type": "database",
      "storeID": "i1374FAB5B01A4871A0973845C86B1E87",
      "ancestors": [
        {
          "defaultName": "Postgres",
          "storeID": "i6821DD61FE364DB1958C0000A3811E02"
        },
        {
          "defaultName": "Postgres",
          "storeID": "iD511A8C78429448685BA4A8C149AEDB7"
        }
      ],
      "dataCacheExpiry": "3600",
      "dataSourceOverride": {
        "originalCMDataSource": "Postgres",
        "originalCatalog": "",
        "originalSchema": "dw",
        "overrideCMDataSource": false,
        "overrideCatalog": false,
        "overrideSchema": false,
        "cmDataSource": "Postgres",
        "catalog": "",
        "schema": "dw"
      }
    }
  ],

### Step 3: Build `querySubject` array
For each table/view:

```json
{
  "ref": ["M1.table_name"],
  "item": [ ... ],
  "identifier": "table_name",
  "label": "table_name",
  ...
}
```

**For each column, apply these rules:**

| Column characteristic | `usage` | `regularAggregate` | `facetDefinition.enabled` |
|---|---|---|---|
| PK, FK, descriptive text, dates for grouping | `identifier` | `countDistinct` | `automatic` |
| Numeric measure (amount, cost, energy, count) | `fact` | `total` (or `average` for rates) | `false` |

**Datatype mapping (PostgreSQL → Cognos):**

| PostgreSQL | Cognos | `datatypeCategory` | `highlevelDatatype` |
|---|---|---|---|
| `integer` | `INTEGER` | `number` | `integer` |
| `bigint` | `BIGINT` | `number` | `integer` |
| `double precision` | `DOUBLE` | `number` | `decimal` |
| `numeric(p,s)` | `DECIMAL(p,s)` | `number` | `decimal` |
| `char(n)` | `CHAR(n)` | `string` | `string` |
| `varchar(n)` / `text` | `VARCHAR(n)` | `string` | `string` |
| `date` | `DATE` | `datetime` | `date` |
| `timestamp` | `TIMESTAMP` | `datetime` | `datetime` |
| `time` | `TIME` | `datetime` | `time` |
| `boolean` | `BOOLEAN` | `string` | `string` |

**Datatype mapping (DB2 → Cognos):**

| DB2 | Cognos | `datatypeCategory` | `highlevelDatatype` |
|---|---|---|---|
| `INTEGER` | `INTEGER` | `number` | `integer` |
| `BIGINT` | `BIGINT` | `number` | `integer` |
| `SMALLINT` | `SMALLINT` | `number` | `integer` |
| `DOUBLE` | `DOUBLE` | `number` | `decimal` |
| `DECIMAL(p,s)` | `DECIMAL(p,s)` | `number` | `decimal` |
| `CHAR(n)` | `CHAR(n)` | `string` | `string` |
| `VARCHAR(n)` | `VARCHAR(n)` | `string` | `string` |
| `CLOB` | `VARCHAR(8192)` | `string` | `string` |
| `DATE` | `DATE` | `datetime` | `date` |
| `TIMESTAMP` | `TIMESTAMP` | `datetime` | `datetime` |
| `TIME` | `TIME` | `datetime` | `time` |

**Datatype mapping (Oracle → Cognos):**

| Oracle | Cognos | `datatypeCategory` | `highlevelDatatype` |
|---|---|---|---|
| `NUMBER` (no scale) | `INTEGER` | `number` | `integer` |
| `NUMBER(p,s)` | `DECIMAL(p,s)` | `number` | `decimal` |
| `FLOAT` / `BINARY_DOUBLE` | `DOUBLE` | `number` | `decimal` |
| `CHAR(n)` | `CHAR(n)` | `string` | `string` |
| `VARCHAR2(n)` | `VARCHAR(n)` | `string` | `string` |
| `CLOB` | `VARCHAR(8192)` | `string` | `string` |
| `DATE` | `TIMESTAMP` | `datetime` | `datetime` |
| `TIMESTAMP` | `TIMESTAMP` | `datetime` | `datetime` |

> **Note:** Oracle `DATE` includes a time component and maps to `TIMESTAMP` in Cognos, not `DATE`.

**Taxonomy (only for date/time columns):**

| Column content | `class` | `family` |
|---|---|---|
| Full date | `cTime` | `cDate` |
| Year | `cTime` | `cYear` |
| Month | `cTime` | `cMonth` |
| Quarter | `cTime` | `cQuarter` |
| Week | `cTime` | `cWeek` |
| Day (any variant) | `cTime` | `cDay` |
| Hour | `cTime` | `cHour` |
| Minute | `cTime` | `cMinute` |
| Second | `cTime` | `cSecond` |
| Suppress auto-classification | `cNone` | `cNone` |

**itemNormalization** – include for dimension tables only:
- Set the PK column as `key` with `keyConstraint: "unique"` and `keyComposition: "independent"`
- List all other columns as `attribute` with `sqlOperator: "minimum"`

### Step 4: Build `relationship` array
For each join/FK relationship:

```json
{
  "left": { "ref": "dim_table", "mincard": "one", "maxcard": "one" },
  "right": { "ref": "fact_table", "mincard": "one", "maxcard": "many" },
  "link": [{ "leftRef": "pk_col", "rightRef": "fk_col", "comparisonOperator": "equalTo" }],
  "joinFilterType": "none",
  "identifier": "dim_table_fact_table"
}
```

**Cardinality convention:**
- Dimension (1) → Fact (N): left=one/one, right=one/many
- The "one" side is the table with the primary/unique key

**joinFilterType values:**
- `none` – standard inner join (default for star schema)
- `outerLeftBody` – left outer join
- `outerRightBody` – right outer join
- `outerFullBody` – full outer join

### Step 5: Build `metadataTreeView`
List all query subject identifiers in `folderItem` array.

### Step 6: Build `drillGroup` (if applicable)
Typical time drill: Year → Month → Date
```json
{
  "segment": [
    { "ref": "dim_date.year_col" },
    { "ref": "dim_date.month_col" },
    { "ref": "dim_date.date_col" }
  ]
}
```

### Step 7: Set module-level properties
- `identifier`: `C_` + descriptive name (no spaces)
- `label`: human-readable name
- `expressionLocale`: match the user's locale (e.g., `de-de`)
- `dataRetrievalMode`: `liveConnection` (real-time queries) or `simpleDataset` (snapshot-based)
- `lastModified`: current ISO 8601 timestamp
- `dataCacheExpiry` (in useSpec): seconds before cached query results expire. Cached results from SQL queries are reused for identical or compatible subsequent requests. When columns from tables with different cache settings are combined in one visualization, the shortest duration wins. Dashboards/stories can override this setting.

---

## Layout rules for `_MUI_diagramNodePosition`
Arrange nodes following IBM's recommended star schema layout (per IBM: "Star schemas are the ideal database structure for data modules but transactional schemas are equally supported"):
- Central fact table(s): center of the layout
- Dimension tables: radiate outward around the fact tables
- Dimension tables: y ≈ 300, spaced at x intervals of ~250
- Fact/view tables: y ≈ 500, spaced at x intervals of ~250
- Start x at ~200

---

## Special date Element Conventions

Cognos does add a _ to certain column names if used.
The known names are:

timestamp
date
hour
minute
month
year
floor

The _ has to be added in "expression" and in the "idForExpression" of the querysubject item. For example hour becomes hour_

---

Don't include Connection Details for Databases. Cognos handles them

---

## Validation checklist
Before outputting, verify:
- [ ] Every `idForExpression` follows pattern `tableIdentifier.columnIdentifier`
- [ ] Every relationship references existing query subject identifiers
- [ ] Every `link.leftRef` / `link.rightRef` matches a column identifier in the respective query subject
- [ ] Every query subject in `relationship` also appears in `metadataTreeView`
- [ ] `usage` and `regularAggregate` are consistent (identifier→countDistinct, fact→total/average)
- [ ] `facetDefinition.enabled` matches usage (identifier→automatic, fact→false)
- [ ] Taxonomy is only set on temporal columns
- [ ] No `_template_` or `_doc_` keys remain in output
- [ ] All placeholder values `{{...}}` are replaced
- [ ] `version` is set to `"22.0"` (internal schema version, not product version)

### Post-generation validation (recommended)
Since this JSON format is undocumented by IBM, always validate generated modules:
1. Import the generated JSON via the Cognos REST API or paste into the Data Module editor
2. Re-export via `Ctrl + / + Q` in the Data Module editor
3. Compare the re-exported JSON against your generated version
4. Check the diagram view to confirm all tables and relationships render correctly
5. Run a test report/dashboard against the module to verify query execution

---

## Example prompts for the user

**PostgreSQL example:**
> Generate a Cognos Data Module for the following PostgreSQL schema on data source "MyDB", schema "analytics":
>
> **Tables:**
> - `dim_customer` (customer_id INTEGER PK, name VARCHAR(100), city VARCHAR(50), segment VARCHAR(30))
> - `fact_sales` (sale_id BIGINT PK, customer_id INTEGER FK→dim_customer, sale_date DATE, amount DOUBLE PRECISION, quantity INTEGER)
>
> **Relationships:**
> - dim_customer.customer_id = fact_sales.customer_id (1:N)
>
> **Locale:** de-de
> **Module name:** Vertriebsanalyse

**DB2 example:**
> Generate a Cognos Data Module for DB2, data source "DWPROD", schema "DWH":
>
> **Tables:**
> - `DIM_PRODUKT` (PRODUKT_ID INTEGER PK, BEZEICHNUNG VARCHAR(200), KATEGORIE VARCHAR(50))
> - `FAKT_UMSATZ` (UMSATZ_ID BIGINT PK, PRODUKT_ID INTEGER FK→DIM_PRODUKT, DATUM DATE, BETRAG DECIMAL(15,2), MENGE INTEGER)
>
> **Relationships:**
> - DIM_PRODUKT.PRODUKT_ID = FAKT_UMSATZ.PRODUKT_ID (1:N)
>
> **Locale:** de-de
> **Module name:** Umsatzanalyse

**Minimal prompt (when storeIDs are known):**
> Generate a Cognos Data Module. Data source "Postgres", connection "Postgres", schema "public".
> storeID for useSpec: iB8C12ECEE49944EFADD6920B85F77A5A
> ancestor storeIDs: i6821DD61FE364DB1958C0000A3811E02, iD511A8C78429448685BA4A8C149AEDB7
>
> Tables: dim_region (region_id INTEGER PK, name VARCHAR(100)), fact_orders (order_id BIGINT PK, region_id INTEGER FK, order_date DATE, total DOUBLE PRECISION)
> Relationship: dim_region.region_id = fact_orders.region_id (1:N)
> Locale: de-de, Module: Bestellanalyse
