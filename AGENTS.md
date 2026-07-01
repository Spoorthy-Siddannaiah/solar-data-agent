# Project Instructions

## Project

Build a multi-tenant AI agent over solar plant data.

## Delivery Process

- Work phase by phase. Do not build the whole application at once.
- Keep each phase small, testable, and reviewable.
- Do not begin a later phase until the current phase has been summarized and is ready for review.
- At the end of every phase, report:
  - files changed;
  - functionality completed;
  - work remaining;
  - manual verification steps.
- Preserve architecture decisions and important tradeoffs in `docs/`.
- Prefer security, authorization correctness, and real document generation over UI polish.
- Do not inspect or modify source data unless the active phase explicitly requires it.

## Technology

- Use Python and FastAPI for the backend.
- Use SQLite for the one-day demo.
- Keep boundaries suitable for later integration with LangChain Deep Agents and GPT-5.
- Do not add LangChain Deep Agents, GPT-5, ingestion, or agent behavior before its designated phase.

## Multi-Tenant Security

- Treat tenant isolation as a mandatory backend invariant.
- Enforce tenant and role restrictions in backend services and database queries, never only in prompts or frontend logic.
- Derive `company_id` exclusively from backend-created authenticated user context.
- Never accept or trust `company_id` from frontend input, prompt text, tool arguments supplied by a model, or model output.
- Scope every tenant-owned read and write by the authenticated user's `company_id`.
- Apply role-level authorization independently of tenant checks.
- Energy-only users must never receive financial data, including market prices, revenue, or monthly costs.
- Prevent restricted fields from appearing in API responses, generated documents, logs, model context, or tool results.
- Prefer deny-by-default authorization and explicit allowlists.
- Add tests for cross-tenant access and role-based data restrictions whenever relevant functionality is introduced.

## Agent and Tool Boundaries

- The agent must never receive raw SQL access or a general-purpose database tool.
- Future agent tools must be narrow, task-specific, read/write only the minimum required data, and enforce authorization server-side.
- Tool implementations must obtain identity and tenant scope from trusted backend context, not model-provided identifiers.
- Validate tool inputs and outputs at the backend boundary.
- Do not rely on system prompts as a security boundary.

## Generated Documents

- Generate real downloadable files rather than UI-only previews or placeholder content.
- Store and retrieve generated documents using `company_id`, `user_id`, and `run_id` ownership.
- Enforce all three ownership dimensions during document lookup and download.
- Use opaque document identifiers externally; do not expose filesystem paths.
- Prevent path traversal and cross-tenant enumeration.

## Architecture and Code Quality

- Keep API, authentication/authorization, services, persistence, agent tools, and document generation separated by clear boundaries.
- Centralize trusted user context and authorization rules.
- Keep database access behind scoped repository or service methods.
- Make security-sensitive assumptions explicit in documentation and tests.
- Use migrations or a documented schema initialization strategy when persistence is introduced.
- Avoid premature abstractions, but do not compromise tenant or role isolation for demo speed.
- Never commit secrets, credentials, API keys, generated private documents, or sensitive source data.

## Current Scope

The repository starts with `data/` and empty scaffolding directories.

Do not implement the following until a later, explicitly requested phase:

- data inspection;
- ingestion;
- agent behavior or agent tools;
- LangChain Deep Agents integration;
- GPT-5 integration.

