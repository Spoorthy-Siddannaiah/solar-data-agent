# Multi-Tenant Solar Data Agent

A security-focused FastAPI application that lets users ask questions about solar plant operations, generate real reports, and access financial data only when their role allows it.

This project is designed as a compact portfolio demo of backend architecture for AI-assisted data products: tenant isolation is enforced in services and database queries, the model only receives narrow scoped tools, and generated documents are stored behind ownership checks.

## Highlights

- Built a multi-tenant backend with FastAPI, SQLite, SQLAlchemy, and explicit `UserContext` authorization.
- Integrated LangChain Deep Agents with GPT-backed orchestration while keeping SQL, filesystem, shell, and tenant identifiers away from the model.
- Implemented role-based access so energy-only users cannot query or receive financial data, including in tool results, API responses, documents, traces, or model context.
- Generated downloadable PDF, DOCX, and XLSX reports instead of UI-only previews.
- Protected document downloads with company, user, run, and document ownership checks.
- Added deterministic and mocked orchestration tests for tenant boundaries, tool visibility, financial restrictions, report creation, and sanitized tracing.
- Included architecture, API, agent, document, and evaluation notes for reviewability.

## Product Scope

The app supports two demo tenants, each with users, solar plants, plant telemetry, market prices, and monthly costs.

Demo roles:

- `company_1_operator`: plant and energy access only
- `company_1_admin`: plant, energy, financial, and report access
- `company_2_operator`: plant and energy access only, scoped to company 2
- `company_2_admin`: plant, energy, financial, and report access, scoped to company 2

The demo identity mechanism uses `X-Demo-User` so reviewers can exercise authorization flows without signing in. Production authentication is intentionally documented as future work.

## Architecture

```text
Browser demo
  -> FastAPI API
  -> Backend-created UserContext
  -> Authorization checks
  -> Scoped services
  -> Narrow agent tools
  -> Tenant-filtered SQLite queries / document generation
  -> API response or owned document download
```

Core security rule:

```text
The LLM is not trusted for isolation.
Tenant and role restrictions are enforced by backend code and database query scope.
```

The agent can call only curated application tools such as plant listing, energy summaries, financial summaries for authorized users, and report creation. It never receives raw SQL access, company IDs, user IDs, filesystem tools, or shell tools.

## What Is Implemented

- Data ingestion from the bundled demo dataset into SQLite
- Tenant-qualified schema for companies, users, plants, elements, datasources, readings, market prices, monthly costs, agent runs, and generated documents
- Backend-derived authentication context from stored demo users
- Tenant-scoped plant and energy services
- Permission-gated financial services
- LangChain Deep Agents orchestration with restricted model-callable tools
- Real PDF, DOCX, and XLSX report generation
- Ownership-checked report download endpoints
- Minimal browser demo for chat, report generation, downloads, and refresh recovery
- Sanitized JSONL trace logging for agent runs
- Railway-compatible deployment configuration
- Unit, API, service, deterministic agent, and mocked orchestration tests

## Not Implemented

- Production authentication and session management
- Durable background worker queue for long-running jobs
- Streaming progress events
- Production database and object storage
- Multi-replica deployment with shared generated document storage

For production, I would replace demo headers with OIDC or signed sessions, move SQLite to Postgres, store generated files in object storage, and run report/agent work through a durable queue.

## Tech Stack

- Python 3.11+
- FastAPI
- SQLAlchemy 2
- SQLite
- LangChain Deep Agents
- OpenAI chat models
- ReportLab, python-docx, and openpyxl
- Pytest
- Vanilla HTML/CSS/JavaScript frontend

## Run Locally

Create and activate a virtual environment:

```bash
python3.11 -m venv .venv311
source .venv311/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Configure the OpenAI API key:

```bash
cp .env.example .env
```

Then edit `.env`:

```text
OPENAI_API_KEY=your-key-here
```

Ingest the demo data:

```bash
python -m backend.scripts.ingest_data
```

Run the API:

```bash
python -m uvicorn backend.api:app --reload
```

Open the demo:

```text
http://127.0.0.1:8000/demo/
```

Run the tests:

```bash
python -m pytest -q
```

## CLI Example

After ingestion, ask the scoped agent from the command line:

```bash
python -m backend.scripts.ask_agent \
  --user company_1_admin \
  --question "Compare my plants for March"
```

The CLI accepts a stored demo user ID or email. It does not accept a company ID; tenant identity and permissions are loaded from the database.

## API Overview

Protected routes use the demo identity header:

```text
X-Demo-User: company_1_operator
```

Useful endpoints:

```text
GET  /health
GET  /config
GET  /demo-users
POST /chat
POST /reports/energy
POST /reports/financial
GET  /runs/{run_id}
GET  /documents/{document_id}/download
```

See `docs/API.md` for request and response details.

## Security Behaviors To Try

1. Select `company_1_operator` and ask: `List my plants`
   Expected: only company 1 plants are returned.

2. Select `company_1_operator` and ask: `Show monthly costs`
   Expected: financial access is denied and no financial values are returned.

3. Select `company_1_operator` and ask about plant `2001`
   Expected: the resource is treated as unavailable without revealing another tenant.

4. Select `company_1_admin` and ask: `Create a financial PDF report for March`
   Expected: the report is generated and the download works.

5. Try downloading a report with another user's run or document ID
   Expected: the API returns the same not-found response used for missing documents.

## Documentation

- `ARCHITECTURE.md`: system design, tenant isolation, role access, and tradeoffs
- `DATA_NOTES.md`: source data inspection and ingestion assumptions
- `docs/AGENT_SLICE.md`: agent setup, scoped tools, tracing, and limitations
- `docs/API.md`: FastAPI routes and demo identity header
- `docs/DOCUMENTS.md`: PDF/DOCX/XLSX generation and secure downloads
- `docs/EVALUATION.md`: deterministic, mocked, and manual evaluation strategy
- `docs/ARCHITECTURE_DIAGRAM.md`: architecture diagram source and rendered SVG
- `docs/DATA_STRUCTURE_DIAGRAM.md`: demo data and schema relationship diagram
- `frontend/SMOKE_TEST.md`: browser smoke test checklist

## Repository Safety

Secrets, local databases, generated documents, trace logs, virtual environments, and assignment/session notes are ignored. The committed `.env.example` contains only a placeholder key.
