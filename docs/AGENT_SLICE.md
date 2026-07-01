# Scoped Deep Agents Runner

## Implementation

`run_agent_question(user_identifier, question) -> str` retains its existing
synchronous contract and now delegates orchestration to LangChain Deep Agents
with `gpt-5`. The API, CLI, persisted run recovery, and frontend therefore do
not change.

The runner registers a model-specific Deep Agents harness profile. It excludes
every built-in broad tool:

- `write_todos`
- `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- `execute`
- `task`

The default general-purpose subagent is disabled and `create_deep_agent`
receives no subagents. `StateBackend` is used for ephemeral graph state only;
no host filesystem backend or execution backend is configured.

## Model-visible tools

Financial users receive exactly:

1. `list_plants`
2. `get_energy_summary`
3. `compare_plants`
4. `get_market_price_summary`
5. `get_monthly_cost_summary`
6. `create_energy_report`
7. `create_financial_report`

Energy-only users receive exactly the first three tools plus
`create_energy_report`. Tests assert these exact sets and assert that they are
disjoint from the excluded Deep Agents tools.

## Security boundary

1. The runner receives a stored demo user ID or email, never a company ID.
2. `load_user_context` resolves company and permissions from the stored user.
3. LangChain tool closures capture that trusted `UserContext`.
4. Each invocation opens its own database transaction and rebuilds the scoped
   service wrapper with the captured context.
5. Model-visible schemas contain only dates, month numbers, plant IDs, and
   allowlisted report formats.
6. Services enforce tenant and financial authorization on every invocation.
7. Financial tools are removed for energy-only users; `FinanceService` and
   `DocumentService` remain the authoritative permission checks.
8. Reports can only be generated through `DocumentService`; tools return safe
   metadata and an ownership-checked relative download URL.
9. The model has no SQL, database, raw filesystem, shell, code execution, or
   subagent tool.
10. Cross-tenant plant IDs produce the existing non-revealing inaccessible
    result.

## Tracing

Every scoped tool attempt is written to the Uvicorn logger and
`backend/logs/agent_traces.jsonl` with UTC timestamp, tool name, allowlisted
arguments, status, and `duration_ms`. Each run also records one sanitized
completion or failure event with `total_duration_ms`.

Prompts, model responses, tool outputs, tenant/user identifiers, financial
values, storage details, SQL, credentials, and API keys are not traced.
Redaction and latency are covered by tests.

## Limitations

- Runs remain single-turn and synchronous.
- The Deep Agents harness-profile API is beta and pinned package upgrades must
  rerun the exact-visible-tool tests.
- There is no streaming, retry policy, or production authentication.
- Agent answers remain free-form text; evaluation and output-policy checks
  should be expanded before production use.
