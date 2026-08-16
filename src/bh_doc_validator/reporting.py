"""Build operational reports from appointment-level validation results."""

from __future__ import annotations

import pandas as pd

from .validation import (
    MISSING_DOCUMENTATION,
    NO_DOCUMENTATION_REQUIRED,
    POSSIBLE_DATA_MISMATCH,
    UNSIGNED_DOCUMENTATION,
    VALID,
)


SUMMARY_COLUMNS = [
    "provider_id",
    "total_appointments",
    "documentation_required",
    "valid_documentation",
    "missing_documentation",
    "unsigned_documentation",
    "possible_data_mismatches",
    "review_cases",
    "no_documentation_required",
    "documentation_completion_percentage",
]


def build_provider_summary(validation_report: pd.DataFrame) -> pd.DataFrame:
    """Aggregate appointment records into defensible provider workflow metrics."""

    appointments = validation_report[
        validation_report["record_type"].eq("APPOINTMENT")
    ].copy()
    if appointments.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    rows: list[dict[str, object]] = []
    for provider_id, group in appointments.groupby("provider_id", sort=True):
        documentation_required = int(group["documentation_required"].sum())
        valid = int(group["validation_result"].eq(VALID).sum())
        completion = (
            round(valid / documentation_required * 100, 1)
            if documentation_required
            else pd.NA
        )
        rows.append(
            {
                "provider_id": provider_id,
                "total_appointments": len(group),
                "documentation_required": documentation_required,
                "valid_documentation": valid,
                "missing_documentation": int(
                    group["validation_result"].eq(MISSING_DOCUMENTATION).sum()
                ),
                "unsigned_documentation": int(
                    group["validation_result"].eq(UNSIGNED_DOCUMENTATION).sum()
                ),
                "possible_data_mismatches": int(
                    group["validation_result"].eq(POSSIBLE_DATA_MISMATCH).sum()
                ),
                "review_cases": int(group["requires_review"].sum()),
                "no_documentation_required": int(
                    group["validation_result"].eq(NO_DOCUMENTATION_REQUIRED).sum()
                ),
                "documentation_completion_percentage": completion,
            }
        )

    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def build_exceptions_report(validation_report: pd.DataFrame) -> pd.DataFrame:
    """Return deterministic follow-up exceptions plus human-review cases."""

    return validation_report.loc[
        validation_report["is_exception"].eq(True)
    ].reset_index(drop=True)
