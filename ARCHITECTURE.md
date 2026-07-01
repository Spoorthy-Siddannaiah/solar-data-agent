# Architecture Plan

## Scope and Priorities

The system is a multi-tenant AI-assisted interface over solar plant operational
and financial data. Tenant isolation, role-level authorization, and real
document generation are system invariants. The language model is not a trusted
security boundary.

Phase 1 contains discovery and design only. Ingestion, FastAPI endpoints, agent
integration, and UI are intentionally not implemented.

## High-Level Architecture

```text
Client
  |
  v
FastAPI API
  |-- authentication -> trusted UserContext
  |-- authorization policies
  |-- tenant-scoped application services
  |     |-- operational query service
  |     |-- financial query service
  |     |-- document service
  |     `-- LangChain Deep Agents orchestration (scoped tools only)
  |
  |-- scoped repositories -> SQLite
  |-- generated document storage
  `-- narrow agent tools -> application services
```

Requests enter through FastAPI. Authentication resolves a user from a trusted
server-side store and constructs `UserContext`. Endpoint handlers pass this
context to services; repositories require it or an already-authorized tenant
scope. No API payload, prompt, or model tool argument can set `company_id`.

The demo FastAPI layer now uses `X-Demo-User` as an identity selector and loads
the corresponding stored user before protected operations. This is deliberately
not presented as production authentication; a later phase should replace the
header with opaque authenticated sessions or OIDC while preserving the same
`UserContext` dependency.

The agent layer sits above application services. LangChain Deep Agents receives
narrow tools, never a database connection, SQL text tool, filesystem browser,
or arbitrary code execution. Its built-in filesystem, shell, planning, and
subagent tools are explicitly excluded by a model-specific harness profile.
The default subagent is disabled and the state-only backend has no host
filesystem or execution capability.

## Planned Ingestion Flow

Ingestion is a later phase. The intended flow is:

1. An operator selects a company directory through a trusted server-side job
   configuration.
2. Load `company.json` and establish the job's immutable `company_id`.
3. Validate the directory name, company object, and all row-level company IDs
   for consistency. A mismatch fails the job; it never changes tenant scope.
4. Validate users, plants, elements, datasources, readings, and financial rows
   against explicit schemas.
5. Normalize timestamps to UTC and parse numeric/currency values without
   floating-point currency arithmetic.
6. Write within a transaction using tenant-qualified keys and foreign keys.
7. Record source file, checksum, ingestion run, row counts, and validation
   errors for traceability and idempotency.
8. Commit only when the tenant's complete batch passes the chosen validation
   policy; otherwise roll back.

The importer should be a CLI/service module, not startup side effects in the
web application.

## SQLite Schema Plan

All tenant-owned tables include `company_id`, even where it could be inferred
through a join. This makes query scoping visible and supports composite foreign
keys that prevent cross-tenant relationships.

Planned core tables:

- `companies(company_id PK, display_name)`
- `users(company_id, user_id, email, role, access_scope, active, PK(company_id,
  user_id), UNIQUE(company_id, email))`
- `plants(company_id, plant_id, name, unique_id, nominal_power_kw, region,
  commissioning_date, PK(company_id, plant_id))`
- `elements(company_id, element_id, plant_id, name, type_code, type_name,
  unique_id, PK(company_id, element_id), FK(company_id, plant_id))`
- `datasources(company_id, datasource_id, element_id, name, unit,
  aggregation, PK(company_id, datasource_id), FK(company_id, element_id))`
- `readings(company_id, datasource_id, observed_at, value,
  PK(company_id, datasource_id, observed_at),
  FK(company_id, datasource_id))`
- `market_prices(company_id, zone, observed_at, eur_per_mwh_micros,
  PK(company_id, zone, observed_at))`
- `monthly_costs(cost_id PK, company_id, plant_id, year, month, category,
  amount_eur_minor, notes, FK(company_id, plant_id))`
- `agent_runs(run_id PK, company_id, user_id, status, created_at, completed_at,
  FK(company_id, user_id))`
- `generated_documents(document_id PK, company_id, user_id, run_id,
  storage_key, media_type, filename, byte_size, checksum, created_at,
  FK(company_id, user_id), FK(run_id))`
- `ingestion_runs(ingestion_run_id PK, company_id, source_checksum, status,
  started_at, completed_at, summary)`

SQLite must enable `PRAGMA foreign_keys = ON`. Repository queries must include
`company_id = ?` explicitly. Indexes should start with `company_id` for common
tenant-scoped access paths. Currency uses integer minor units
(`amount_eur_minor`) and market prices use millionths of EUR/MWh
(`eur_per_mwh_micros`), avoiding binary floating point.

The final DDL should make the `agent_runs`/`generated_documents` relationship
tenant-safe with a composite uniqueness constraint such as
`UNIQUE(company_id, user_id, run_id)` and a matching composite foreign key.

## UserContext

`UserContext` is an immutable backend-created value:

```python
UserContext(
    user_id: str,
    company_id: str,
    role: Role,
    capabilities: frozenset[Capability],
)
```

Authentication accepts only a credential or opaque demo identity token. The
backend looks up the user and derives `company_id`, role, and capabilities from
stored data. Client-supplied company IDs are not accepted for authorization.

Initial capabilities should be explicit, for example:

- `ENERGY_READ`
- `FINANCIAL_READ`
- `DOCUMENT_CREATE`
- `DOCUMENT_READ_OWN`

Services check capabilities rather than spreading string comparisons across
handlers. Role-to-capability mapping is server-controlled and deny-by-default.

For a one-day demo, authentication may use a clearly documented opaque
server-configured token mapped to a stored user. An email or `user_id` header
alone is not adequate for production.

## Tenant Isolation

Isolation is applied in layers:

1. Authentication creates the trusted `UserContext`.
2. API routes do not expose `company_id` as a tenant-selection parameter.
3. Services authorize the requested operation and pass `context.company_id`.
4. Repositories always use tenant-qualified predicates and composite keys.
5. Database foreign keys prevent cross-tenant child relationships.
6. Documents use server-generated storage keys and ownership metadata.
7. Tests attempt cross-company reads, writes, joins, tool calls, and downloads.

Numeric entity IDs are not security boundaries. A plant ID from another tenant
must return not found under the caller's tenant scope, without revealing that
the entity exists.

SQLite has no row-level security, so application scoping and tests are critical
for the demo. Production should add database-enforced row-level security or
separate tenant databases/schemas where justified.

## Role-Level Financial Access

Financial authorization is a separate check from tenant authorization.
`FINANCIAL_READ` is required for:

- hourly market prices;
- monthly costs;
- revenue, cost, margin, or profit calculations;
- documents containing financial data;
- future tool results or model context containing those values.

Energy-only services and response models should not select financial columns at
all. Filtering a combined response after the query is too fragile. Document
templates and tools must be classified by required capability, and the check
must occur before querying or generating content.

Derived data inherits the strictest classification of its inputs. Revenue
computed from energy and market prices is financial data.

## Safe Future Agent Tools

Candidate narrow tools:

- `list_plants()`
- `get_plant_summary(plant_id)`
- `get_energy_series(plant_id, metric, start, end, granularity)`
- `compare_plant_energy(plant_ids, metric, start, end)`
- `get_market_price_summary(start, end)` — requires `FINANCIAL_READ`
- `get_cost_summary(plant_id, year, month)` — requires `FINANCIAL_READ`
- `create_energy_report(plant_ids, start, end)`
- `create_financial_report(plant_ids, start, end)` — requires
  `FINANCIAL_READ`
- `get_run_status(run_id)`

Tools receive `UserContext` from orchestration runtime dependency injection,
not from model arguments. Their public schemas must not contain `company_id`,
`user_id`, SQL, arbitrary table/column names, filesystem paths, or unrestricted
URLs. Each tool validates entity ownership, date bounds, result-size limits,
and capability requirements in its service.

## Document Ownership

A document is created under the authenticated
`(company_id, user_id, run_id)` tuple. The server generates both `run_id` and an
opaque `document_id`. Storage paths/keys are generated internally, for example:

```text
<company-hash>/<user-hash>/<run-id>/<document-id>.pdf
```

Download lookup is effectively:

```sql
WHERE document_id = ?
  AND company_id = context.company_id
  AND user_id = context.user_id
  AND run_id = ?
```

Only after the ownership query succeeds may the service resolve the internal
storage key. Filenames supplied for download headers are sanitized and never
used as storage paths. Report metadata records the authorization class used at
creation so energy-only users cannot access previously generated financial
reports if roles change.

Whether admins may read other users' documents is intentionally not assumed.
If required later, it should be a distinct audited capability and endpoint.

The demo implementation now follows this design through `DocumentService`.
It generates XLSX, PDF, and DOCX files, stores SHA-256 and size metadata, uses
hashed owner path segments, and rechecks financial permission when financial
documents are downloaded. Local file writes and SQLite metadata are not fully
atomic; see `docs/DOCUMENTS.md` for the cleanup tradeoff.

## Demo vs Production Tradeoffs

| Concern | One-day demo | Production direction |
|---|---|---|
| Database | SQLite, foreign keys, explicit tenant predicates | PostgreSQL with row-level security and migrations |
| Authentication | Opaque configured demo tokens mapped server-side | OIDC/OAuth2, signed tokens, session revocation |
| Authorization | Central capability policy in process | Central policy plus database enforcement and audit |
| Documents | Local non-public storage with metadata in SQLite | Encrypted object storage, signed short-lived downloads |
| Jobs | In-process or synchronous bounded work | Durable queue, workers, retries, idempotency |
| Agent | Later, narrow server-side tools | Sandboxed orchestration, budgets, tracing, evaluations |
| Secrets | Environment variables outside version control | Managed secret store and rotation |
| Observability | Structured local logs without sensitive payloads | Central logs, metrics, traces, security audit trail |
| Scale | Single process | Stateless API replicas and managed services |

The demo may simplify infrastructure, but it must not simplify tenant scoping,
financial authorization, or document ownership.

## Required Security Tests in Later Phases

- A company 1 user cannot read or mutate company 2 entities even with known IDs.
- An energy-only user cannot call financial services or receive direct/derived
  financial fields.
- Model/tool arguments containing another `company_id` have no effect.
- Repositories cannot create cross-company relationships.
- Documents cannot be listed or downloaded across company, user, or run
  ownership.
- Authorization failures do not reveal whether another tenant's record exists.
- Role changes invalidate access to newly restricted data and documents.
