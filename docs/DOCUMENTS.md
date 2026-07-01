# Document Service

`DocumentService` generates real XLSX, PDF, and DOCX reports from the existing
scoped plant and finance services.

## Ownership

Every generated file has an opaque `document_id` and `run_id`. Metadata stores:

- `company_id`
- `user_id`
- `run_id`
- internal storage key
- media type and filename
- byte size and SHA-256 checksum
- energy or financial classification
- UTC creation time

The associated run also records its run type, synchronous completion state, and
a bounded result message. Chat runs use the same owner-qualified run table and
store their final answer without document metadata.

Downloads require an exact match on document ID, company, user, and run.
Missing and inaccessible documents return the same error. Admin status does not
grant access to another user's files.

Storage keys are server-generated from hashed company/user segments and UUIDs.
They are never returned in public document metadata. The service resolves and
checks the storage path before reading, then verifies byte size and checksum.

Financial reports call `FinanceService` before creating a run, file, or
document row. Financial documents also recheck the current user's financial
permission at download time.

## Demo Storage

The default intended local storage root is `backend/generated_documents/`,
which is ignored by Git. The service accepts its storage root through backend
construction; callers cannot supply paths through report inputs.

For production, replace local storage with encrypted object storage and
short-lived authenticated downloads.

## Transaction Tradeoff

File creation and SQLite metadata cannot form one atomic transaction. The
service removes the file if its own database flush fails. A caller that rolls
back after a successful service return can still leave an orphaned file.
Production work should add an outbox/finalization workflow and periodic orphan
cleanup.
