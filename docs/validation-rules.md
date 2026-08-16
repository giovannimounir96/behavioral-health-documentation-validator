# Validation rules and data contract

## Input contract

All identifiers are opaque synthetic strings. Leading and trailing whitespace is
removed, but identifiers are not case-normalized or fuzzy-matched.

### `appointments.csv`

| Column | Required value | Purpose |
|---|---:|---|
| `appointment_id` | Yes; unique | Authoritative reconciliation key |
| `patient_id` | Yes | Synthetic consistency check |
| `provider_id` | Yes | Synthetic consistency check and summary grouping |
| `appointment_date` | Yes; `YYYY-MM-DD` | Note-date alignment |
| `appointment_time` | Column only | Retained for workflow context; unused by Phase 1 rules |
| `service_type` | Column only | Retained for future rule segmentation |
| `appointment_status` | Yes | Determines documentation requirement |

### `documentation.csv`

| Column | Required value | Purpose |
|---|---:|---|
| `note_id` | Yes; unique | Distinguishes multiple documentation records |
| `appointment_id` | Yes | Exact link to an appointment |
| `patient_id` | Yes | Synthetic consistency check |
| `provider_id` | Yes | Synthetic consistency check |
| `note_date` | Yes; `YYYY-MM-DD` | Date alignment check |
| `note_status` | Yes | Signed/unsigned evaluation |
| `note_type` | Yes | Retained for future rule segmentation |
| `signed_date` | No; blank or `YYYY-MM-DD` | Signature consistency check |

## Rule precedence

Precedence is fixed in code so a lower-priority favorable condition cannot hide a
higher-risk ambiguity. `issue_codes` retains secondary issues.

| Order | Condition | `validation_result` | `rule_code` | Exception | Review |
|---:|---|---|---|---:|---:|
| 1 | Cancelled appointment | `NO_DOCUMENTATION_REQUIRED` | `APPOINTMENT_CANCELLED` | No | No |
| 2 | Rescheduled appointment | `NO_DOCUMENTATION_REQUIRED` | `APPOINTMENT_RESCHEDULED` | No | No |
| 3 | No-show with default policy | `NO_DOCUMENTATION_REQUIRED` | `NO_SHOW_NOT_REQUIRED` | No | No |
| 3a | No-show with review policy | `NEEDS_HUMAN_REVIEW` | `NO_SHOW_POLICY_REVIEW` | Yes | Yes |
| 4 | Unknown appointment status | `NEEDS_HUMAN_REVIEW` | `UNKNOWN_APPOINTMENT_STATUS` | Yes | Yes |
| 5 | Completed appointment, zero notes | `MISSING_DOCUMENTATION` | `COMPLETED_NO_NOTE` | Yes | No |
| 6 | Completed appointment, multiple notes | `NEEDS_HUMAN_REVIEW` | `MULTIPLE_DOCUMENTATION_RECORDS` | Yes | Yes |
| 7 | Patient or provider mismatch | `POSSIBLE_DATA_MISMATCH` | `IDENTITY_MISMATCH` | Yes | Yes |
| 8 | Note date outside tolerance | `NEEDS_HUMAN_REVIEW` | `NOTE_DATE_OUTSIDE_TOLERANCE` | Yes | Yes |
| 9 | Unknown/inconsistent signature state | `NEEDS_HUMAN_REVIEW` | Relevant issue code | Yes | Yes |
| 10 | One aligned unsigned note | `UNSIGNED_DOCUMENTATION` | `COMPLETED_UNSIGNED_NOTE` | Yes | No |
| 11 | One aligned signed note | `VALID` | `COMPLETED_SIGNED_NOTE` | No | No |
| Separate | Documentation references absent appointment | `NEEDS_HUMAN_REVIEW` | `ORPHAN_DOCUMENTATION` | Yes | Yes |

Input rows with invalid required values or duplicate primary IDs fail before
business rules run. This is different from a legitimate one-to-many relationship
where two uniquely identified notes point to one appointment; that condition is
reported for review.

## Multiple simultaneous issues

Example: a completed appointment has one note with a provider mismatch, a date
outside tolerance, and unsigned status.

```text
validation_result = POSSIBLE_DATA_MISMATCH
rule_code         = IDENTITY_MISMATCH
issue_codes       = PROVIDER_ID_MISMATCH|NOTE_DATE_OUTSIDE_TOLERANCE|NOTE_UNSIGNED
requires_review   = true
is_exception      = true
```

The mismatch wins the primary-result precedence, while all findings remain
machine-readable for audit and later filtering.

## Configuration behavior

- `date_tolerance_days` is a non-negative integer and applies symmetrically.
  A difference exactly equal to the tolerance is allowed.
- `no_show_policy` is either `no_documentation_required` or `review`.
- `require_signed_date_for_signed_notes` controls whether a signed note must have
  a populated signed date.
- Status matching ignores case and surrounding whitespace after inputs pass the
  required-value checks.
- Status groups must be non-empty and cannot overlap.
- `rules_version` is a caller-managed label copied into every detailed result.
  Phase 4 plans to add a configuration checksum for stronger traceability.
