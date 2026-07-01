# Multi-Tenant Solar Data Agent

This repository is being developed phase by phase. The target is a
multi-tenant AI agent over solar plant operational and financial data, with
backend-enforced tenant isolation and role-level access.


## Deployed Demo

Demo UI: https://invertix-task-production-c848.up.railway.app/demo/

Health check: https://invertix-task-production-c848.up.railway.app/health

Config check: https://invertix-task-production-c848.up.railway.app/config

Demo notes:
- Select a demo user in the UI before asking questions.
- `company_1_operator` can query plant/energy data but cannot access financial data.
- `company_1_admin` can generate financial reports.
- The Railway deployment uses SQLite and local generated files for demo simplicity. State may reset on restart or redeploy.


## Current Status

The lightweight frontend demo over the secure API, scoped agent, and document
generation is complete.

Available now:

- a read-only dataset inspection script;
- findings from the current two-company demo dataset;
- an architecture and security design for later implementation;
- tenant-qualified SQLAlchemy models;
- a resettable ingestion script with source-reference and tenant validation;
- a generated local SQLite database after ingestion;
- a backend-derived `UserContext` loader;
- tenant-scoped plant and energy query services;
- permission-gated financial summary services;
- seven model-callable wrappers over those services, including owned report
  generation;
- a GPT-5 LangChain Deep Agents orchestration layer restricted to scoped tools;
- a single-question CLI;
- owned XLSX, PDF, and DOCX energy/financial reports;
- health, demo-user, chat, report, run, and download API routes;
- persisted owned chat/report run results;
- a build-free frontend demo with refresh recovery for chat, reports, and
  downloads.

Not implemented:

- production authentication and production-grade UI.

See [DATA_NOTES.md](DATA_NOTES.md) for observed formats and
[ARCHITECTURE.md](ARCHITECTURE.md) for the planned system boundaries.

## Planned Phases

1. **Discovery and architecture** — inspect the package and define tenant,
   authorization, storage, and agent boundaries.
2. **Validated ingestion and persistence** — define SQLite schema, validation,
   and resettable import. **Complete for the demo dataset.**
3. **Trusted context and scoped reads** — add backend-derived `UserContext`,
   tenant-scoped services, capability checks, and security tests.
   **Complete.**
4. **Secure API foundation** — add authentication and bounded FastAPI routes
   over the existing service layer. **Demo transport complete; production
   authentication remains.**
5. **Thin agent slice** — connect GPT-5 to five scoped service tools and add a
   CLI. **Complete.**
6. **Document generation** — create and securely store real reports under
   company/user/run ownership. **Complete.**
7. **Frontend demo** — add a minimal UI after backend security and core
   workflows are verified. **Complete.**
8. **Hardening and evaluation** — adversarial isolation tests, agent
   evaluations, auditability, observability, and production migration plan.

Each phase ends with changed files, completed work, remaining work, and manual
verification guidance.

## Run the Data Inspection

Prerequisite for the complete application: Python 3.11 or newer (Deep Agents
requires it). Use a Python 3.11 virtual environment consistently for
installation, application commands, and tests; the system `python3` may still
resolve to Python 3.9.

From the repository root:

```bash
python3.11 -m venv .venv311
source .venv311/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q
```

Confirm the active interpreter before running commands:

```bash
python --version
```

It must report Python 3.11 or newer. The inspection script itself uses only
the standard library.

From the repository root:

```bash
python3 backend/scripts/inspect_data.py data
```

An alternate data root can be passed as the first argument:

```bash
python3 backend/scripts/inspect_data.py /path/to/data
```

The script prints company folders, API and financial file paths, user columns
and samples, company keys, and bounded JSON/CSV structural samples. It is
read-only and does not ingest or modify source data.

## Run Ingestion

Create and activate the Python 3.11 environment if it is not already active,
then install dependencies:

```bash
python3.11 -m venv .venv311
source .venv311/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Then run:

```bash
python3 backend/scripts/ingest_data.py \
  --data-dir data \
  --database backend/solar_data.db
```

The default paths are `data/` and `backend/solar_data.db`, so this is also
valid:

```bash
python3 backend/scripts/ingest_data.py
```

Every run drops and recreates the database schema. This is intentional for the
demo. The script derives each trusted tenant ID from `company.json`, validates
tenant IDs present in financial CSV rows, validates all source references, and
prints verified row counts after commit.

Expected counts for the current package:

| Table | company_1 | company_2 | Total |
|---|---:|---:|---:|
| companies | 1 | 1 | 2 |
| users | 2 | 2 | 4 |
| plants | 2 | 2 | 4 |
| elements | 6 | 6 | 12 |
| datasources | 10 | 10 | 20 |
| readings | 14,640 | 14,640 | 29,280 |
| market_prices | 2,928 | 2,928 | 5,856 |
| monthly_costs | 28 | 28 | 56 |

Financial values are loaded but no query/API layer exposes them. Monthly costs
are stored as integer euro cents; market prices are stored as integer
millionths of EUR/MWh. Timestamps are parsed as timezone-aware values,
normalized to UTC, and persisted as UTC ISO 8601 text.

## Ingestion Assumptions

- Each company directory name must equal its trusted `company.json`
  `company_id`.
- Plant, element, datasource, and user IDs are resolved only within the current
  company.
- Every datasource has exactly one reading file and therefore one source
  aggregation (`sum` or `average`) in this demo package.
- All listed source files are required; malformed or dangling references fail
  the complete transaction.
- User access scopes are limited to `energy` and `energy+financial`.
- Monthly costs support two decimal places; market prices support six.

Run the full test suite from the active Python 3.11 environment with:

```bash
python -m pytest -q
```

## Ask the Scoped Agent

Set `OPENAI_API_KEY` in the environment or in the repository-root `.env` file.
The `.env` file is ignored and must not be committed.

Ensure the demo database has been ingested, then run:

```bash
python3 -m backend.scripts.ask_agent \
  --user company_1_admin \
  --question "List my plants"
```

The user may be a stored demo user ID or email. Tenant identity and permissions
are loaded from the database; the CLI does not accept a company ID. The
synchronous runner uses LangChain Deep Agents with only the seven scoped
application tools (four for energy-only users).

See [docs/AGENT_SLICE.md](docs/AGENT_SLICE.md) for the Deep Agents
configuration, security boundary, and current limitations.

## Document Generation

`DocumentService` creates energy reports for energy-enabled users and financial
reports only for users with financial permission. It uses the existing scoped
services and accepts no company ID or storage path from callers.

Generated metadata is stored in `agent_runs` and `generated_documents`.
Downloads require matching company, user, run, and document ownership.

See [docs/DOCUMENTS.md](docs/DOCUMENTS.md) for storage and transaction details.

## Run the API

After ingestion:

```bash
python3 -m uvicorn backend.api:app --reload
```

Protected routes use the stored demo user ID or email:

```text
X-Demo-User: company_1_operator
```

The backend derives company and permissions from that stored user. Requests do
not accept company IDs. See [docs/API.md](docs/API.md) for routes and security
behavior.

## Run the Frontend Demo

Start the API, then open:

```text
http://127.0.0.1:8000/demo/
```

The frontend submits only the selected stored demo user identifier. It never
stores or sends a company ID. It stores only opaque run IDs locally and
recovers results through the ownership-checked run endpoint. See
[frontend/SMOKE_TEST.md](frontend/SMOKE_TEST.md) for the manual verification
checklist.

## Deploy the Demo to Railway

Railway configuration is committed in `railway.json`. Railpack uses Python
3.11 from `.python-version`, installs `requirements.txt`, ingests the bundled
demo data at startup, and starts FastAPI with:

```bash
python -m backend.scripts.ingest_data && \
python -m uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000}
```

The port is supplied by Railway. Configure this required service variable in
the Railway dashboard:

```text
OPENAI_API_KEY=...
```

Do not upload or commit `.env`. After deployment, verify:

```text
https://<service-domain>/health
https://<service-domain>/demo/
```

This remains a single-instance demo deployment. SQLite, generated documents,
runs, and JSONL traces use the container filesystem and are not durable across
restarts or redeployments. Startup ingestion intentionally recreates the demo
database. Do not scale this configuration to multiple replicas; production
should use shared durable database and object storage services.
