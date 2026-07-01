# Data Inspection Notes

Inspection date: 2026-06-29

Command used:

```bash
python3 backend/scripts/inspect_data.py data
```

These notes describe the current demo package. They are observations, not an
ingestion contract; a later ingestion phase must validate every assumption.

## Package Layout

The package contains two company folders:

- `company_1`
- `company_2`

Each company has the same logical layout:

```text
company_N/
├── company.json
├── users.csv
├── api/
│   ├── GET_api_Plant.json
│   ├── request_manifest.json
│   └── plant_<plant_id>/
│       ├── GET_api_Plant_{plantId}_Element.json
│       ├── GET_api_Plant_{plantId}_Datasource.json
│       └── GET_api_DataList_v2__ds_<datasource_id>__<aggregation>.json
└── financial/
    ├── hourly_market_prices.csv
    └── monthly_costs.csv
```

There are two plants per company:

- `company_1`: plant IDs `1001` and `1002`
- `company_2`: plant IDs `2001` and `2002`

Each company has 16 files under `api/`: one plant list, one request manifest,
two element files, two datasource files, and ten time-series files. Each
company has two files under `financial/`.

## Company and User Data

`company.json` is a top-level object with:

- `company_id`
- `display_name`

`users.csv` columns are:

- `user_id`
- `email`
- `role`
- `access_scope`

Each inspected tenant has an admin with `energy+financial` access and an
operator with `energy` access. The source data has no password or other
authentication credential. Authentication therefore needs a separate demo
mechanism, and authorization must use the backend-loaded user record rather
than frontend claims.

## Operational JSON

All operational API exports use JSON arrays except `company.json`, which is an
object.

### Plants

`GET_api_Plant.json` contains two records per company. Observed record keys:

- `_alertIcon`
- `AlarmColor`
- `DatasourcesCount`
- `ElementCount`
- `Id`
- `Name`
- `Parameters`
- `UniqueID`

`Parameters` is a list of key/value objects. Observed parameters include
nominal power, region, and commissioning date. Values are encoded as strings,
so later ingestion must parse values according to the parameter key.

Observed nominal powers and regions:

- plant `1001`: 1200.0, North
- plant `1002`: 850.0, South
- plant `2001`: 1450.0, West
- plant `2002`: 980.0, East

### Elements

Each inspected element file contains three records. Record keys are:

- `Identifier`
- `Name`
- `ParentId`
- `Type`
- `TypeString`
- `UniqueID`

`ParentId` corresponds to the plant ID in the inspected samples. Observed
element types include totalizers and weather stations.

### Datasources

Each inspected datasource file contains five records. Record keys are:

- `DataSourceId`
- `DataSourceName`
- `ElementId`
- `Units`

The inspected samples include total meter energy in `kWh` and power in `kW`.
Datasource IDs are tenant/plant-specific; code must not infer authorization
from their numeric prefixes.

### Time Series

There are five time-series files per plant and 20 total. Every inspected file
contains 1,464 records with:

- `DataSourceId`
- `Date`
- `Value`

The sample timestamps are UTC ISO 8601 strings beginning
`2026-03-01T00:00:00Z` with hourly observations. Values are JSON numbers.
File names include either `sum` or `average`; aggregation semantics are
metadata and must be retained explicitly rather than inferred later from the
measurement value.

The 1,464-record count is consistent across the 20 files, but continuity,
duplicate timestamps, complete date range, and null/non-finite values have not
yet been validated.

### Request Manifest

Each `request_manifest.json` is a list of 15 records with:

- `file`
- `method`
- `params`
- `path`

The manifest maps each export to its original GET endpoint and parameters. It
can guide source traceability during ingestion, but paths and parameters are
source metadata, not authorization inputs.

## Financial CSVs

`hourly_market_prices.csv` columns:

- `company_id`
- `zone`
- `timestamp`
- `eur_per_mwh`

Samples show UTC hourly timestamps, `zone_1`, and decimal prices encoded as CSV
strings.

`monthly_costs.csv` columns:

- `company_id`
- `plant_id`
- `year`
- `month`
- `category`
- `amount_eur`
- `notes`

Observed categories include maintenance and cleaning. Amounts are decimal
currency values encoded as CSV strings. Empty notes are present.

Both financial files include `company_id`, but ingestion must verify it against
the backend-selected company directory. It must never use a row value to choose
the destination tenant.

## Security Classification

- Operational energy data: available to energy and energy+financial users
  within the same company.
- Market prices, costs, and any derived revenue/profit values: financial data,
  available only to users with explicit financial access within the same
  company.
- User records and generated documents: tenant-owned data requiring explicit
  user/ownership checks as applicable.

Financial fields must be excluded before data reaches an energy-only user's
API response, document, model context, tool result, or log payload.

## Open Data Questions

- Are the five datasource names and units stable across all future plants?
- What exact time interval and timezone guarantees apply to readings?
- Does `sum` mean interval sum, cumulative energy, or source API aggregation?
- Can readings be missing, duplicated, corrected, null, or non-numeric?
- Are currency values always EUR, and what rounding policy is required?
- Can a user have financial access without the admin role, or should access be
  derived solely from `access_scope`?
- Are plant IDs, element IDs, datasource IDs, and user IDs globally unique, or
  only unique within a company?
- Should market zones be associated with companies, plants, or both?

