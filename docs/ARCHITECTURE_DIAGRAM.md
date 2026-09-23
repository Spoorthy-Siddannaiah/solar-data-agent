# Presentation Architecture Diagram

[Open the presentation SVG](architecture-diagram.svg)

## Editable Mermaid source

```mermaid
flowchart LR
    USER["User"]

    subgraph EXPERIENCE["User Experience"]
        UI["Frontend Demo UI<br/>Chat • Reports • Downloads"]
    end

    subgraph BACKEND["Secure Backend"]
        API["FastAPI API<br/>Receives requests and returns results"]
        ACCESS["Identity & Access Check<br/>Loads user, company, role, permissions"]
        AGENT["AI Agent<br/>Understands the question<br/>and chooses an approved action"]

        subgraph SERVICES["Safe Tools & Backend Services"]
            PLANT["Plant & Energy Service"]
            FINANCE["Financial Service<br/>Permission required"]
            DOCUMENT["Report Service<br/>PDF • Excel • Word"]
            RUNS["Run History Service"]
        end

        API --> ACCESS
        ACCESS -->|"Trusted user context"| AGENT
        AGENT -->|"Approved tool calls only"| PLANT
        AGENT --> FINANCE
        AGENT --> DOCUMENT
        API -->|"Report button"| DOCUMENT
        API --> RUNS
    end

    subgraph STORAGE["Data & Outputs"]
        DB[("SQLite Data Store<br/>Users • Plants • Readings • Finance")]
        FILES["Generated Report Files"]
        LOGS["Sanitized Agent Traces"]
    end

    USER -->|"1. Ask or request report"| UI
    UI -->|"2. Request + demo identity"| API
    ACCESS -->|"3. Stored identity lookup"| DB
    PLANT -->|"Company-scoped query"| DB
    FINANCE -->|"Company + role-scoped query"| DB
    DOCUMENT --> DB
    DOCUMENT --> FILES
    RUNS --> DB
    AGENT -.-> LOGS
    PLANT -.-> LOGS
    FINANCE -.-> LOGS
    DOCUMENT -.-> LOGS
    AGENT -->|"Answer"| API
    DOCUMENT -->|"Report metadata"| API
    FILES -->|"Ownership-checked download"| API
    API -->|"7. Answer or file"| UI
    UI --> USER
```

## How to explain the diagram

1. The **user** asks a question or requests a report in the browser.
2. The **Frontend Demo UI** sends the question and selected demo identity to
   FastAPI. It never sends a company ID.
3. The **FastAPI API** coordinates the request. The **Identity & Access Check**
   loads the stored user and creates trusted company and permission context.
4. For chat, the **AI Agent** understands the request and can select only the
   tools it was given. It has no SQL, database, filesystem, or shell tool.
5. **Safe Tools & Backend Services** perform fixed operations:
   - plant and energy summaries;
   - financial summaries after a financial permission check;
   - owned PDF, Excel, and Word report generation;
   - owned run-result storage and recovery.
6. Services—not the AI—query the **SQLite Data Store**. Every query is
   restricted to the trusted company. Report files are generated server-side,
   and agent traces contain only sanitized event metadata.
7. The API returns an answer or an ownership-checked download to the frontend.

## Security messages to highlight

- Access is determined inside the backend from the stored user.
- The AI agent cannot query the database directly.
- Company isolation and financial permissions are rechecked by services.
- Reports are generated and downloaded through server-side ownership checks.

For a shorter executive version, hide the four individual service boxes and
show only one **Safe Tools & Services** box. For a technical version, add the
individual SQLAlchemy models and composite tenant foreign keys under the data
store; those details are intentionally omitted from the presentation diagram.
