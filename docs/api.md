# REST API contract

## Purpose and boundary

The API is a local JSON adapter around the same deterministic engine used by the
CSV command. It does not contain separate validation rules. The server owns the
rules configuration; callers cannot submit a filesystem path or replace policy
through a request.

Inline JSON was selected because it is portable to n8n and avoids exposing local
server file paths. Multipart CSV upload can be added later only if a concrete
workflow requires it.

## Create a validation run

`POST /api/v1/runs`

```json
{
  "source": "portfolio-sample",
  "appointments": [
    {
      "appointment_id": "APT-5001",
      "patient_id": "PAT-1001",
      "provider_id": "PROV-001",
      "appointment_date": "2026-07-01",
      "appointment_time": "09:00",
      "service_type": "Medication Management",
      "appointment_status": "Completed"
    }
  ],
  "documentation": []
}
```

Both collections are required but may be empty. Extra or misspelled fields are
rejected. Dates remain strings so the existing strict `YYYY-MM-DD` data contract
is still the source of truth. `source` defaults to `api`, is trimmed, and must
contain 1–100 characters after trimming.

Example `201 Created` response:

```json
{
  "run_id": "c9c7c816-4f25-45c1-9d14-f2115f9cf046",
  "created_at": "2026-08-16T20:15:30.123456Z",
  "source": "portfolio-sample",
  "rules_version": "1.0.0",
  "appointment_count": 1,
  "documentation_count": 0,
  "result_count": 1,
  "valid_count": 0,
  "exception_count": 1,
  "review_count": 0
}
```

The response is intentionally summary-only. Automation can branch on
`exception_count` and fetch the exception collection only when needed.

## Read endpoints

- `GET /api/v1/runs?limit=50&offset=0`
- `GET /api/v1/runs/{run_id}`
- `GET /api/v1/runs/{run_id}/results?limit=100&offset=0`
- `GET /api/v1/runs/{run_id}/exceptions?limit=100&offset=0`

Collection responses include `total`, `limit`, `offset`, and `items`. Result
flags are JSON booleans and counts are JSON numbers. Empty fields inside detailed
validation results remain empty strings to preserve the Phase 1 result contract.
For every collection, `limit` must be between 1 and 200 and `offset` must be zero
or greater.

## Error contract

All handled errors use a stable envelope:

```json
{
  "error": {
    "code": "RUN_NOT_FOUND",
    "message": "Validation run ... was not found."
  }
}
```

| Status | Code | Meaning |
|---:|---|---|
| `422` | `REQUEST_VALIDATION_ERROR` | JSON shape, type, or UUID path is invalid |
| `422` | `INPUT_DATA_ERROR` | JSON is well formed but violates the shared data contract |
| `404` | `RUN_NOT_FOUND` | A valid UUID has no persisted run |
| `500` | `CONFIGURATION_ERROR` | The server-owned rule file is missing or invalid |
| `500` | `INTERNAL_ERROR` | An unexpected server error occurred |
| `503` | `PERSISTENCE_UNAVAILABLE` | SQLite could not safely read or write the run |

Request-schema details contain only field locations, messages, and error types;
the raw request body is not echoed. Semantic data errors may identify the field,
record position, or duplicate synthetic ID needed to correct the batch. Local
database paths and tracebacks are never returned.

## Phase 3 n8n contract

The intended flow is:

```text
synthetic source → transform to JSON → POST /api/v1/runs
                 → branch on exception_count
                 → GET /exceptions when count > 0
                 → human review task or local notification
```

Before retry behavior is enabled, Phase 3 should add an idempotency or workflow
execution key so a retried POST cannot silently create a duplicate run.
