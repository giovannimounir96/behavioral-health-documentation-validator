# Architecture and phased plan

## Architecture goal

The design keeps the deterministic rule engine at the center and treats CSV,
HTTP, SQLite, and future n8n/dashboard components as adapters around it. The API
does not reimplement any validation rule.

```mermaid
flowchart TB
    subgraph Input adapters
        A["CSV loader / CLI"]
        B["FastAPI / Pydantic JSON"]
        N["n8n Manual / Webhook trigger"]
    end

    subgraph Core
        C["Shared data-contract checks"]
        D["Application service"]
        E["Deterministic rule engine"]
        F["Report builders"]
    end

    subgraph Output adapters
        G["Three CSV reports"]
        H["SQLite repository"]
        I["REST JSON responses"]
        R["n8n summaries / review notification"]
    end

    J["Server-owned validation_rules.json"] --> E
    A --> C
    N --> B
    B --> C
    C --> D --> E --> F
    F --> G
    F --> H --> I
    H --> B
    I --> N --> R
```

## Component responsibilities

| Component | Responsibility | What it deliberately does not do |
|---|---|---|
| `io.py` | Normalize and enforce required columns/values/dates/unique IDs for CSV and JSON adapters | Apply business outcomes |
| `config.py` | Load JSON parameters and reject invalid or overlapping status mappings | Allow arbitrary executable rules |
| `validation.py` | Match by exact appointment ID, apply precedence, preserve issue codes | Fuzzy-match people or make clinical judgments |
| `reporting.py` | Build exception and provider-level views with a defined denominator | Modify source records |
| `service.py` | Run shared in-memory validation and persist completed API runs | Reimplement deterministic rules |
| `pipeline.py` | Preserve the Phase 1 CSV-output workflow | Depend on FastAPI or SQLite |
| `cli.py` | Expose a reproducible user entry point and useful logs | Contain validation logic |
| `persistence.py` | Own schema version 2, migration, atomic idempotency, history, and result queries | Store raw inputs or duplicate exceptions |
| `api_models.py` | Define typed request/response JSON contracts | Perform pandas or SQL work |
| `api.py` | Map HTTP requests, idempotency headers, responses, and errors onto the service/repository | Accept server file paths or change policy rules |
| n8n workflow | Trigger, move JSON, retain `run_id`, retrieve results, branch, notify, retry, and expose failures | Apply or modify validation rules |

## Key design decisions

### Exact matching, then consistency checks

`appointment_id` is the only authoritative link. For documentation-required
appointments, patient ID, provider ID, and note date are checked for consistency.
Lifecycle statuses that do not require documentation are classified first and do
not undergo note-consistency checks in Phase 1. A future candidate-match feature
may suggest likely links, but it must never silently link records.

### Outcome, exception, and review are separate

- `validation_result` is the primary outcome chosen by fixed precedence.
- `issue_codes` preserves all detected conditions even if only one primary result
  can be selected.
- `is_exception` controls the operational exception queue.
- `requires_review` is reserved for ambiguity that needs human judgment.

This avoids labeling a clearly missing note as an ambiguous decision while still
keeping it in the exceptions report.

### Configuration has a narrow boundary

Status aliases, no-show handling, date tolerance, signature-date enforcement,
and the rules version can change in JSON. Rule precedence stays in versioned code
and tests. A custom rules engine would add complexity without improving this MVP.

### Data-quality failures have explicit behavior

Malformed required values, invalid dates, and duplicate primary IDs fail the run
with an actionable message. Multiple distinct notes for one appointment and
documentation without a matching appointment remain valid input but become
review exceptions. No record is silently selected or discarded.

### One source of truth, multiple adapters

Both CSV and API records pass through the same input checks and
`validate_records()` implementation. The shared service builds detailed,
exception, and provider-summary DataFrames in memory. The CSV adapter writes
those frames to files; the API adapter stores the detailed frame in SQLite.

### Minimal relational persistence

SQLite schema version 2 has two tables:

```text
validation_runs (one row per completed UUID run)
    └── validation_results (ordered detailed rows for that run)
```

`validation_runs` stores UTC creation time, source, rules version, counts, and
nullable idempotency key/fingerprint fields. Existing schema-version-1 databases
migrate in place, leaving older rows unkeyed.
`validation_results` stores all 18 Phase 1 detailed-result fields with a foreign
key and stable order. Exceptions are ordinary persisted results selected with
`is_exception = 1`; a second exception table would duplicate state.

Input validation and deterministic evaluation finish before the database write.
One short transaction inserts the run and all results. An error rolls back both,
so history can never contain a partially written completed run.

### Idempotency at the state-changing boundary

Idempotency belongs to the API/service/repository boundary rather than the rule
engine. The n8n client sends one stable key for a logical job. FastAPI computes a
canonical SHA-256 fingerprint of the normalized JSON request. SQLite's partial
unique index permits many historical null keys but only one completed run per
supplied key.

A preliminary lookup makes ordinary replays cheap. The definitive check and
insert still occur atomically in a write transaction, which closes the race
between simultaneous retries. Same-key/same-fingerprint requests return the
persisted run; same-key/different-fingerprint requests fail with `409`. Failed
validation and rolled-back writes never reserve a key.

### Synchronous local API

The endpoints use normal synchronous `def` functions because pandas and standard
library `sqlite3` are synchronous. FastAPI runs them in a worker thread, while
the repository opens a fresh SQLite connection for each operation. Async database
libraries, pools, background workers, and queues would not improve this small
local workload.

The API accepts inline JSON arrays rather than local file paths. That boundary is
portable to n8n and avoids arbitrary server file access. Rules remain server
owned, and the supported launch command binds to localhost during this phase.

### n8n is an orchestration adapter

The exported n8n workflow has Manual and Webhook triggers, prepares one of three
small synthetic JSON scenarios, calls the API, stores the returned UUID, retrieves
persisted results/exceptions, and branches first on exceptions and then on
`requires_review`. It produces explicit success, deterministic-follow-up, or
human-review-notification summaries.

Every HTTP node has three bounded attempts and a separate error output leading to
a deliberately failed execution. The POST key is calculated before retries. The
workflow never calculates an outcome, edits a rule result, or adds AI judgment.

```mermaid
flowchart TD
    T["Manual trigger / POST webhook"] --> N["n8n synthetic JSON + stable key"]
    N --> P["POST validation run"] --> API["FastAPI"]
    API --> V["Deterministic validator"] --> DB["SQLite"]
    DB --> G["GET results + exceptions"] --> E{"Exceptions?"}
    E -- "No" --> S["Success"]
    E -- "Yes" --> H{"requires_review?"}
    H -- "No" --> D["Deterministic follow-up"]
    H -- "Yes" --> Q["Human-review notification"] --> U["Reviewer"]
    P -. "Retries exhausted" .-> F["Failed execution"]
    G -. "Retries exhausted" .-> F
```

## MVP acceptance criteria

Phase 1 is complete when:

1. one command processes the included synthetic inputs;
2. three output reports are created with stable schemas;
3. every documented rule branch has an automated test;
4. malformed inputs fail with a useful explanation;
5. the sample produces known counts and provider metrics; and
6. the repository explains its privacy boundary and limitations.

All six criteria are currently met.

## Phase 2 acceptance criteria

Phase 2 is complete when:

1. every successful run receives a unique UUID and timezone-aware UTC timestamp;
2. run metadata and detailed results survive repository/application restart;
3. a failed insert rolls back the entire run;
4. JSON requests invoke the same deterministic engine as the CSV workflow;
5. history, detail, results, and exceptions can be retrieved through versioned
   REST endpoints;
6. invalid requests and infrastructure failures return stable, sanitized errors;
7. the original Phase 1 behavior and tests remain green; and
8. the public sample can be submitted without paid or external services.

All eight criteria are currently met.

## Phase 3 acceptance criteria

Phase 3 is complete when:

1. keyed `POST /api/v1/runs` requests replay the original run safely, reject a
   changed payload with `409`, and remain race-safe under concurrent retries;
2. the exported workflow has a manual trigger and a webhook trigger;
3. n8n sends synthetic JSON, retains `run_id`, and retrieves persisted results
   and exceptions through the API;
4. exception and `requires_review` branches produce explicit operational and
   human-review paths;
5. HTTP failures use bounded retries and a visible failed-execution path;
6. the workflow uses no credentials, PHI, paid service, or LLM;
7. static workflow tests, the complete Python suite, and live local end-to-end
   scenarios pass; and
8. the workflow, diagrams, API contract, and portfolio notes are committed with
   the implementation.

All eight criteria are currently met.

## Phased development plan

### Phase 1 — trusted rule engine (complete)

Build the synthetic data contract, JSON configuration, validation logic, CLI,
reports, tests, example artifacts, and portfolio documentation.

Professional value: rule analysis, Python/pandas, error handling, testing,
auditability, and operational reporting.

### Phase 2 — SQLite persistence and REST API (complete)

Add a shared service, SQLite run history, atomic result persistence, unique run
IDs, typed JSON request/response models, versioned FastAPI endpoints, pagination,
and consistent error handling. Retain CSV as a separate working adapter.

Professional value: relational modeling, SQL transactions, repository patterns,
REST/HTTP, JSON schemas, dependency boundaries, and API testing.

### Phase 3 — n8n automation integration (complete)

Add a retry-safe idempotency boundary and an exported n8n workflow that
transforms synthetic input, invokes the existing API, retrieves persisted
results/exceptions, branches on exceptions and review requirements, and creates
an explicit local human-review notification path. Keep all outcomes owned by
the deterministic validator.

Professional value: APIs, HTTP/JSON, orchestration, branching, retry/error paths,
and integration testing.

### Phase 4 — dashboard, selected AI, and presentation

Add a lightweight run/exception dashboard, reviewer disposition only if needed,
GitHub Actions, screenshots, and optional packaging. Carefully selected AI may
summarize deterministic exceptions or suggest review priority, but it must cite
source issue codes and never write validation outcomes.

Professional value: CI, reproducibility, packaging, observability, and technical
communication.

## Avoided overengineering

The MVP intentionally avoids microservices, Postgres, an ORM, queues, React,
Kubernetes, fuzzy identity matching, FHIR simulation, authentication claims, and
LLM-based validation. SQLite uses a small in-code migration because the project
has only one prior schema version. Those technologies can be added only after a
concrete requirement earns their complexity.
