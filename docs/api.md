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
  "rules_fingerprint": "979c7f432e0e5f54ef4e93f8f5b8bd0f85db2bfd268e38fa80c98ee1533619d6",
  "appointment_count": 1,
  "documentation_count": 0,
  "result_count": 1,
  "valid_count": 0,
  "exception_count": 1,
  "review_count": 0
}
```

The response is intentionally summary-only. `rules_version` is the readable
label supplied by the configuration file. `rules_fingerprint` is the SHA-256
fingerprint of the validated, normalized effective configuration, including the
version label, scalar settings, and normalized status groups. Automation can
record it with the run ID to establish which exact effective rules produced a
result, then branch on `exception_count` and fetch the exception collection only
when needed.

## Retry-safe creation with `Idempotency-Key`

Automation clients should add an `Idempotency-Key` header to create-run requests:

```http
POST /api/v1/runs
Content-Type: application/json
Idempotency-Key: n8n-phase3:portfolio-job-001
```

The key is optional to preserve the Phase 2 contract. Without it, every valid
POST intentionally creates a new UUID. With it:

| Request | Status | Result |
|---|---:|---|
| First use of key and JSON | `201` | New run; `Idempotency-Replayed: false` |
| Same key and normalized JSON | `200` | Original run and UUID; `Idempotency-Replayed: true` |
| Same key and different JSON | `409` | `IDEMPOTENCY_CONFLICT`; no new run |

Keys contain 1–128 characters, begin with an alphanumeric character, and may
otherwise contain alphanumerics, `.`, `_`, `:`, or `-`. A UUID, source event ID,
or workflow/job ID is appropriate. The client must create the key once before a
retrying HTTP node starts; timestamps or randomness generated per attempt defeat
the protection.

The server fingerprints the normalized Pydantic request with SHA-256. JSON
whitespace and object-key order therefore do not create false conflicts, while
array order remains meaningful because it controls stable result order. The key
and fingerprint are stored only with a completed run. Validation, configuration,
or rolled-back persistence failures do not reserve the key.

SQLite schema version 3 enforces key uniqueness with a partial unique index. A
keyed write checks and inserts within one transaction, so simultaneous retries
cannot create two runs. Existing schema-version-1 and version-2 databases
migrate in place. Earlier runs receive a null `rules_fingerprint` because their
actual effective configuration was not stored and cannot be reconstructed
reliably; every new completed run stores a 64-character SHA-256 value.

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
| `409` | `IDEMPOTENCY_CONFLICT` | The key already belongs to different normalized JSON |
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
manual/webhook trigger → choose synthetic scenario + stable job key
                       → POST /api/v1/runs
                       → store run_id
                       → GET /results and GET /exceptions
                       → branch on exceptions and requires_review
                       → operational summary or human-review notification
```

The exported workflow always supplies a key, uses three bounded HTTP attempts,
and routes terminal API failures to a failed n8n execution. See
[`n8n-workflow.md`](n8n-workflow.md) for the complete node-by-node contract.
