# Multi-Tenant Solar Data Agent

A small multi-tenant AI agent demo for querying solar plant operational and financial data.

The system focuses on backend-enforced tenant isolation, role-based financial access, scoped agent tools, and downloadable PDF/DOCX/XLSX reports.

## Deployed Demo

Demo UI: https://invertix-task-production-c848.up.railway.app/demo/

Health check: https://invertix-task-production-c848.up.railway.app/health

Config check: https://invertix-task-production-c848.up.railway.app/config

## Demo Users

- `company_1_operator` — can query plant and energy data, but cannot access financial data
- `company_1_admin` — can query plant, energy, and financial data, and can generate financial reports
- `company_2_operator` — same role as operator, scoped to company 2
- `company_2_admin` — same role as admin, scoped to company 2

## What Is Implemented

- Ingestion of the provided two-company solar dataset into SQLite
- Tenant-qualified schema for companies, users, plants, elements, datasources, readings, market prices, and monthly costs
- Backend-derived `UserContext`; the frontend never sends `company_id`
- Tenant-scoped plant and energy query services
- Permission-gated financial query services
- LangChain Deep Agents + GPT-5 orchestration
- Restricted model-callable tools only
- No raw SQL, shell, broad filesystem tools, or company/user IDs exposed to the model
- Real PDF, DOCX, and XLSX report generation
- Ownership-checked document downloads by company, user, run, and document
- FastAPI routes for health, demo users, chat, reports, runs, and downloads
- Minimal frontend demo with chat, report generation, download buttons, and refresh recovery
- Sanitized JSONL trace logging for agent runs
- Railway deployment configuration

## Not Implemented / Limitations

- Production authentication is not implemented; `X-Demo-User` is used only for demo identity selection
- The UI is intentionally minimal and not production-grade
- True background worker queue for long-running jobs is not implemented
- Live streaming/progress events are not implemented
- Railway uses SQLite and local generated files for demo simplicity
- State may reset on restart or redeploy
- Production should use Postgres, object storage, real authentication, and a durable worker queue

## Architecture Summary

The main flow is:

```text
Frontend Demo
  -> FastAPI API
  -> Backend-derived UserContext
  -> LangChain Deep Agent
  -> Scoped service tools
  -> Tenant-filtered database queries / report generation
  -> Answer or owned document download
```

Important security boundary:

```text
The LLM is not trusted for isolation.
Tenant and role access are enforced in backend services and data access layers.
```

The model only receives scoped tools. It does not receive raw database access, company IDs, user IDs, SQL tools, shell tools, or broad filesystem tools.

## Run Locally

Requires Python 3.11 or newer.

Create and activate a virtual environment:

```bash
python3.11 -m venv .venv311
source .venv311/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Set the API key locally:

```bash
export OPENAI_API_KEY="your-key-here"
```

Alternatively, use a repository-root `.env` file. The `.env` file is ignored and must not be committed.

Ingest the demo data:

```bash
python -m backend.scripts.ingest_data
```

Run the API:

```bash
python -m uvicorn backend.api:app --reload
```

Open the frontend demo:

```text
http://127.0.0.1:8000/demo/
```

Run tests:

```bash
python -m pytest -q
```

## CLI Usage

After ingestion, ask the scoped agent from the command line:

```bash
python -m backend.scripts.ask_agent \
  --user company_1_admin \
  --question "List my plants"
```

The user may be a stored demo user ID or email. The CLI does not accept a company ID. Tenant identity and permissions are loaded from the database.

## API Usage

Protected routes use the stored demo user ID or email through the demo header:

```text
X-Demo-User: company_1_operator
```

The backend derives company and permissions from the stored user. Requests do not accept company IDs.

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

See `docs/API.md` for more details.

## Document Generation

The system can generate real reports in:

```text
PDF
DOCX
XLSX
```

Energy reports are available to energy-enabled users. Financial reports are only available to users with financial permission.

Generated documents are owned by company, user, run, and document metadata. Downloads are checked against this ownership before returning a file.

See `docs/DOCUMENTS.md` for details.

## Deployment

Railway configuration is committed in `railway.json`.

The deployment start command ingests the bundled demo data and starts FastAPI:

```bash
python -m backend.scripts.ingest_data && \
python -m uvicorn backend.api:app --host 0.0.0.0 --port ${PORT:-8000}
```

Railway service variable required:

```text
OPENAI_API_KEY=...
```

Do not commit `.env` or API keys.

Keep the Railway demo to one replica because SQLite and generated documents are local to the container filesystem.

## Suggested Smoke Test

After local run or Railway deployment:

```text
1. Open /health
   Expected: {"status":"ok"}

2. Open /demo/

3. Select company_1_operator
   Ask: "List my plants"
   Expected: only company 1 plants

4. Select company_1_operator
   Ask: "Show monthly costs"
   Expected: financial access denied

5. Select company_1_admin
   Ask: "Create a financial PDF report for March"
   Expected: report is created and download works

6. Refresh the page
   Expected: completed result/report can be recovered
```

## Additional Documentation

- `ARCHITECTURE.md` — system design, tenant isolation, role access, and tradeoffs
- `DATA_NOTES.md` — source data inspection and ingestion assumptions
- `docs/AGENT_SLICE.md` — Deep Agents setup, scoped tools, tracing, and limitations
- `docs/API.md` — FastAPI routes and demo identity header
- `docs/DOCUMENTS.md` — PDF/DOCX/XLSX generation and secure downloads
- `docs/EVALUATION.md` — manual agent decision evaluation matrix
- `frontend/SMOKE_TEST.md` — frontend smoke test checklist
- `coding-agent-sessions/` — coding-agent session history