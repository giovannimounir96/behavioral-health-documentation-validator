# Portfolio progress

Last updated: 2026-08-16

## Current milestone

**Phase 1 — trusted rule engine: complete**

The repository now has a small end-to-end product slice: synthetic inputs,
data-contract validation, deterministic reconciliation, operational reports,
tests, and documentation. The next milestone is persistence and a lightweight
review dashboard, not additional rule complexity.

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
| Automated test suite | Prove the rules and output counts are stable | `.venv\Scripts\python.exe -m pytest` → `31 passed` | pytest, unit testing, integration testing |
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

## Remaining phases

### Phase 2 — persistence and dashboard

- Design a small SQLite schema for validation runs and results.
- Persist each batch transactionally with a run ID and timestamps.
- Add a lightweight dashboard with KPI cards, filters, and an exception queue.
- Add tests for database writes and empty states.
- Capture portfolio screenshots using only synthetic data.

### Phase 3 — API and n8n

- Expose validation through a small REST endpoint using JSON responses.
- Create an n8n workflow: receive batch → call validator → branch on exceptions →
  persist results → send a local/demo notification or create a review task.
- Export the workflow JSON and document retry/error behavior.
- Avoid paid APIs during local development.

### Phase 4 — hardening

- Add GitHub Actions for tests.
- Add optional Docker packaging after the local path is stable.
- Produce a run manifest with counts, rules version, and input checksums.
- Add larger-volume performance and failure-recovery tests.

### Phase 5 — optional assistive AI

- Evaluate an operations-summary feature using only deterministic output fields.
- Label generated text as a suggestion and preserve source issue codes.
- Require human review for any prioritization suggestion.
- Do not let an LLM create or overwrite validation outcomes.

## Known limitations and open decisions

- The MVP is batch-based and does not retain run history.
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

No known Phase 1 defects. All 31 tests pass on Python 3.13.14 with pandas 3.0.5.

## Ideas for future improvement

- Add service-type-specific rules only if a concrete workflow requirement exists.
- Add a separate input-reject report for partial batch acceptance.
- Add a configuration checksum so a rules-version label cannot drift from its
  actual settings.
- Measure validation time on generated synthetic batches of increasing size.
- Add a reviewer disposition/audit trail in Phase 2.
- Compare deterministic output snapshots across rules versions.
