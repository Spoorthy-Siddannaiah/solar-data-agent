# FastAPI Layer

The API is a thin transport over the existing trusted context loader, agent
runner, and `DocumentService`.

## Demo Identity

Protected routes require:

```text
X-Demo-User: company_1_operator
```

The value may be a stored demo user ID or email. The backend loads the user and
derives company, role, and permissions from SQLite. Requests never accept a
company ID.

This header is a demo identity selector, not production authentication.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Process health |
| GET | `/demo-users` | List selectable demo identities without company IDs |
| POST | `/chat` | Invoke the existing scoped agent runner |
| POST | `/reports/energy` | Generate an owned XLSX/PDF/DOCX energy report |
| POST | `/reports/financial` | Generate an owned financial report |
| GET | `/runs/{run_id}` | Recover an owned chat or report run |
| GET | `/documents/{document_id}/download?run_id=...` | Download an owned file |

Financial report creation returns `403` for users without financial access.
Run and download lookups filter by company, user, run, and document ownership.
Inaccessible resources return the same `404` response as missing resources.

Successful synchronous chat requests persist their final answer in
`agent_runs` and return `run_id`, `status`, and `answer`. Report creation
persists its completed run and document metadata. `GET /runs/{run_id}` returns
the saved result and owned documents, allowing browser refresh recovery without
putting tenant context in browser storage.

API schemas and responses do not expose database paths, filesystem paths,
storage keys, raw SQL, or tenant-selection fields.

## Run Locally

After ingestion:

```bash
python3 -m uvicorn backend.api:app --reload
```

OpenAPI documentation is available at `/docs`.
