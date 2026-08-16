# Portfolio progress

Last updated: 2026-08-16

## Current milestone

**Phase 2 — SQLite persistence and REST API: complete**

The repository now has two working delivery paths around one rule engine: the
original CSV CLI and a local JSON REST API. API runs receive UUIDs and UTC
timestamps, and completed metadata/results are stored atomically in SQLite. The
next milestone is n8n orchestration, not new validation logic.

## Completed features and evidence

| Feature | Business purpose | Evidence / verification | Skills demonstrated |
|---|---|---|---|
| Synthetic input datasets | Demonstrate the workflow without PHI or employer data | `data/sample/`; inspect for opaque IDs only | Data minimization, safe portfolio design |
| Versioned JSON rules | Change practical policy values without editing logic | `config/validation_rules.json`; no-show policy test | JSON, configuration validation, business-rule modeling |
| CSV data-contract checks | Stop bad inputs with actionable messages | `src/bh_doc_validator/io.py`; invalid-date and duplicate-ID tests | pandas, defensive programming, error handling |
| Exact-key reconciliation | Reliably compare appointments and notes | `validation.py`; matching and order-independence tests | Data reconciliation, deterministic design |
| Rule precedence and issue codes | Make outcomes auditable when several problems coexist | `docs/validation-rules.md`; simultaneous-issue test | Requirements analysis, decision tables, traceability |
| Human-review boundary | Prevent automation from guessing about ambiguous records | `requires_review` versus `is_exception` in outputs | Human-in-the-loop workflow design |
| Three operational reports | Turn validation into actionable provider and exception views | `examples/output/`; end-to-end test | pandas aggregation, KPI definition, reporting |
| Command-line workflow and logs | Make the batch repeatable and automation-ready | `python -m bh_doc_validator ...` | CLI design, logging, pipeline orchestration |
| Phase 1 Git restore point | Preserve the trusted CLI/rule baseline before infrastructure changes | Commit `52751a0` | Git staging, commits, safe incremental delivery |
| Shared validation service | Keep CSV and API paths on one deterministic implementation | `service.py`; complete regression suite | Application-layer design, separation of concerns |
| SQLite schema and history | Retain completed batches without a paid service | `persistence.py`; schema/restart tests | SQL, relational modeling, schema versioning |
| Atomic run persistence | Prevent partially stored run history | forced-failure rollback test | Transactions, integrity, failure handling |
| UUIDs and UTC timestamps | Give automation stable globally unique run references | service and uniqueness tests | UUIDs, timezone-aware metadata, traceability |
| Persisted exceptions | Keep actionable rows queryable without duplicate state | `is_exception = 1` query and test | Normalization, derived views, data integrity |
| Versioned FastAPI endpoints | Make the validator callable by n8n and other tools | `/api/v1/runs`; `docs/api.md` | REST, HTTP methods/statuses, OpenAPI |
| Typed JSON contract | Validate nested inputs and serialize typed outputs | `api_models.py`; API schema/type tests | JSON, Pydantic, contract-first integration |
| Sanitized API errors | Return actionable codes without leaking paths/payloads | error handlers and negative tests | Error taxonomy, security boundaries |
| Automated test suite | Prove rules, CSV, database, and API behavior are stable | `.venv\Scripts\python.exe -m pytest` → `61 passed` | pytest, unit, integration, API, transaction testing |
| Architecture and setup documentation | Let a recruiter run and evaluate the work | `README.md`, `docs/architecture.md` | Technical communication, system design |

## Technical concepts learned

- Why an exact business key should be separated from consistency checks.
- How rule precedence chooses one primary result without discarding secondary
  `issue_codes`.
- Why an operational exception is not always a case requiring human judgment.
- How schema validation differs from record-level business validation.
- How to define a defensible percentage denominator and explicit exclusions.
- How package entry points, modules, logging, and pytest make a script into a
  maintainable automation project.
- How configuration should expose stable parameters without becoming an unsafe,
  hard-to-test rules programming language.
- Why APIs should accept portable JSON data rather than arbitrary server paths.
- How an application service lets CLI and HTTP adapters reuse one rule engine.
- Why a database transaction must commit run metadata and results together.
- Why exceptions can be a filtered result set instead of a duplicate table.
- How UUIDs differ from database auto-increment IDs in distributed workflows.
- How HTTP `POST`, `GET`, `201`, `404`, `422`, `500`, and `503` communicate
  different outcomes to an automation client.
- Why synchronous pandas/sqlite work should use normal FastAPI `def` handlers.
- How dependency injection and temporary database files make APIs testable.

## Remaining phases

### Phase 3 — n8n integration

- Create an n8n workflow: receive batch → call validator → branch on exceptions →
  retrieve persisted exceptions → send a local/demo notification or create a
  human-review task.
- Add a workflow execution/idempotency key before enabling HTTP retries so a
  retry cannot create a duplicate validation run.
- Exercise webhook concepts only where they improve the workflow.
- Export the workflow JSON and document success, no-exception, and error paths.
- Avoid paid APIs during local development.

### Phase 4 — dashboard, selected AI, and portfolio polish

- Add a lightweight dashboard for run history, filters, and exception review.
- Add GitHub Actions for tests.
- Capture screenshots and improve the end-to-end portfolio narrative.
- Consider optional Docker packaging only if it simplifies the n8n demo.
- Evaluate an exception-summary or prioritization feature using only persisted
  deterministic output fields.
- Label generated text as a suggestion and preserve source issue codes.
- Require human review for any prioritization suggestion.
- Do not let an LLM create or overwrite validation outcomes.

## Known limitations and open decisions

- SQLite is a local single-service database and is not presented as a high-scale
  production architecture.
- The API is synchronous and designed for small synthetic batches.
- The API has no authentication, TLS, rate limiting, or body-size limit and must
  remain local during this phase.
- Invalid requests do not create failed-run audit rows; only completed runs are
  persisted.
- Raw input payloads are not stored, so a run cannot yet be replayed from SQLite.
- There is no idempotency key yet; Phase 3 must add one before n8n retries.
- Duplicate primary IDs fail the run; future ingestion may add a separate reject
  file if partial processing becomes a requirement.
- Date tolerance is symmetric; a real policy could need separate before/after
  limits.
- Provider summaries exclude documentation-only orphan records to avoid counting
  them as appointments. They remain visible in the exception report.
- The sample fixture is deliberately small; it proves branches, not performance.
- No real EHR integration is claimed or planned for the public demo.
- “HIPAA compliant,” “production-ready,” and quantified time-savings are not
  claimed because this portfolio MVP does not provide evidence for those claims.

## Bugs / issues

No known Phase 1 or Phase 2 defects. All 61 tests pass on Python 3.13.14 with
pandas 3.0.5, FastAPI 0.141.1, and SQLite 3.50.4.

## Ideas for future improvement

- Add service-type-specific rules only if a concrete workflow requirement exists.
- Add a separate input-reject report for partial batch acceptance.
- Add a configuration checksum so a rules-version label cannot drift from its
  actual settings.
- Add idempotent run creation for automation retries.
- Consider storing an input checksum without retaining raw inputs.
- Measure validation time on generated synthetic batches of increasing size.
- Add a reviewer disposition/audit trail only when the dashboard workflow is
  defined.
- Compare deterministic output snapshots across rules versions.
