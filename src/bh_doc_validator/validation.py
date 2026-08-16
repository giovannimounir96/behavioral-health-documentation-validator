"""Deterministic appointment-to-documentation reconciliation rules."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from .config import ValidationConfig


VALID = "VALID"
MISSING_DOCUMENTATION = "MISSING_DOCUMENTATION"
UNSIGNED_DOCUMENTATION = "UNSIGNED_DOCUMENTATION"
NO_DOCUMENTATION_REQUIRED = "NO_DOCUMENTATION_REQUIRED"
POSSIBLE_DATA_MISMATCH = "POSSIBLE_DATA_MISMATCH"
NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"

DETAIL_COLUMNS = [
    "record_type",
    "appointment_id",
    "provider_id",
    "appointment_date",
    "appointment_status",
    "matched_note_id",
    "note_provider_id",
    "note_date",
    "note_status",
    "matched_note_count",
    "documentation_required",
    "validation_result",
    "rule_code",
    "issue_codes",
    "reason",
    "requires_review",
    "is_exception",
    "rules_version",
]


def validate_records(
    appointments: pd.DataFrame,
    documentation: pd.DataFrame,
    config: ValidationConfig,
) -> pd.DataFrame:
    """Return an auditable validation row for every appointment and orphan note."""

    notes_by_appointment = {
        appointment_id: group.sort_values("note_id", kind="stable")
        for appointment_id, group in documentation.groupby("appointment_id", sort=False)
    }

    records: list[dict[str, Any]] = []
    for _, appointment in appointments.sort_values("appointment_id", kind="stable").iterrows():
        matching_notes = notes_by_appointment.get(
            appointment["appointment_id"], documentation.iloc[0:0]
        )
        records.append(_evaluate_appointment(appointment, matching_notes, config))

    appointment_ids = set(appointments["appointment_id"])
    orphan_documentation = documentation[
        ~documentation["appointment_id"].isin(appointment_ids)
    ]
    for appointment_id, orphan_group in orphan_documentation.groupby(
        "appointment_id", sort=True
    ):
        records.append(_orphan_record(appointment_id, orphan_group, config))

    return pd.DataFrame(records, columns=DETAIL_COLUMNS)


def _evaluate_appointment(
    appointment: pd.Series,
    matching_notes: pd.DataFrame,
    config: ValidationConfig,
) -> dict[str, Any]:
    status_group = config.appointment_group(appointment["appointment_status"])
    note_count = len(matching_notes)

    if status_group == "cancelled":
        return _appointment_record(
            appointment,
            matching_notes,
            config,
            documentation_required=False,
            validation_result=NO_DOCUMENTATION_REQUIRED,
            rule_code="APPOINTMENT_CANCELLED",
            reason="Cancelled appointment; documentation is not required by the configured rules.",
        )

    if status_group == "rescheduled":
        return _appointment_record(
            appointment,
            matching_notes,
            config,
            documentation_required=False,
            validation_result=NO_DOCUMENTATION_REQUIRED,
            rule_code="APPOINTMENT_RESCHEDULED",
            reason="Rescheduled appointment; documentation is not required by the configured rules.",
        )

    if status_group == "no_show":
        if config.no_show_policy == "review":
            return _appointment_record(
                appointment,
                matching_notes,
                config,
                documentation_required=False,
                validation_result=NEEDS_HUMAN_REVIEW,
                rule_code="NO_SHOW_POLICY_REVIEW",
                issue_codes=["NO_SHOW_POLICY_REVIEW"],
                reason="No-show appointment is routed for review by configuration.",
                requires_review=True,
                is_exception=True,
            )
        return _appointment_record(
            appointment,
            matching_notes,
            config,
            documentation_required=False,
            validation_result=NO_DOCUMENTATION_REQUIRED,
            rule_code="NO_SHOW_NOT_REQUIRED",
            reason="No-show appointment; documentation is not required by the configured rules.",
        )

    if status_group is None:
        return _appointment_record(
            appointment,
            matching_notes,
            config,
            documentation_required=False,
            validation_result=NEEDS_HUMAN_REVIEW,
            rule_code="UNKNOWN_APPOINTMENT_STATUS",
            issue_codes=["UNKNOWN_APPOINTMENT_STATUS"],
            reason=(
                f"Appointment status {appointment['appointment_status']!r} is not mapped "
                "in the rules configuration."
            ),
            requires_review=True,
            is_exception=True,
        )

    if note_count == 0:
        return _appointment_record(
            appointment,
            matching_notes,
            config,
            documentation_required=True,
            validation_result=MISSING_DOCUMENTATION,
            rule_code="COMPLETED_NO_NOTE",
            issue_codes=["NO_NOTE_FOUND"],
            reason="Completed appointment has no documentation with the same appointment_id.",
            is_exception=True,
        )

    if note_count > 1:
        return _appointment_record(
            appointment,
            matching_notes,
            config,
            documentation_required=True,
            validation_result=NEEDS_HUMAN_REVIEW,
            rule_code="MULTIPLE_DOCUMENTATION_RECORDS",
            issue_codes=["MULTIPLE_DOCUMENTATION_RECORDS"],
            reason=(
                f"Completed appointment has {note_count} documentation records; "
                "the validator will not choose one automatically."
            ),
            requires_review=True,
            is_exception=True,
        )

    note = matching_notes.iloc[0]
    issue_codes: list[str] = []
    reasons: list[str] = []

    if appointment["patient_id"] != note["patient_id"]:
        issue_codes.append("PATIENT_ID_MISMATCH")
        reasons.append("appointment and documentation patient IDs differ")
    if appointment["provider_id"] != note["provider_id"]:
        issue_codes.append("PROVIDER_ID_MISMATCH")
        reasons.append("appointment and documentation provider IDs differ")

    appointment_date = pd.Timestamp(appointment["appointment_date"])
    note_date = pd.Timestamp(note["note_date"])
    date_offset_days = abs((note_date - appointment_date).days)
    if date_offset_days > config.date_tolerance_days:
        issue_codes.append("NOTE_DATE_OUTSIDE_TOLERANCE")
        reasons.append(
            f"note date differs by {date_offset_days} day(s), beyond the configured "
            f"{config.date_tolerance_days}-day tolerance"
        )

    note_group = config.note_group(note["note_status"])
    if note_group is None:
        issue_codes.append("UNKNOWN_NOTE_STATUS")
        reasons.append(f"note status {note['note_status']!r} is not mapped")
    elif note_group == "signed" and config.require_signed_date_for_signed_notes:
        if not note["signed_date"]:
            issue_codes.append("SIGNED_DATE_MISSING")
            reasons.append("note is marked Signed but signed_date is blank")
    elif note_group == "unsigned":
        issue_codes.append("NOTE_UNSIGNED")
        reasons.append("completed appointment has an unsigned note")
        if note["signed_date"]:
            issue_codes.append("UNSIGNED_WITH_SIGNED_DATE")
            reasons.append("note is marked Unsigned but signed_date is populated")

    identity_issues = {"PATIENT_ID_MISMATCH", "PROVIDER_ID_MISMATCH"}
    review_issues = {
        "NOTE_DATE_OUTSIDE_TOLERANCE",
        "UNKNOWN_NOTE_STATUS",
        "SIGNED_DATE_MISSING",
        "UNSIGNED_WITH_SIGNED_DATE",
    }

    if identity_issues.intersection(issue_codes):
        result = POSSIBLE_DATA_MISMATCH
        rule_code = "IDENTITY_MISMATCH"
        requires_review = True
    elif review_issues.intersection(issue_codes):
        result = NEEDS_HUMAN_REVIEW
        rule_code = next(code for code in issue_codes if code in review_issues)
        requires_review = True
    elif note_group == "unsigned":
        result = UNSIGNED_DOCUMENTATION
        rule_code = "COMPLETED_UNSIGNED_NOTE"
        requires_review = False
    else:
        result = VALID
        rule_code = "COMPLETED_SIGNED_NOTE"
        reasons.append("completed appointment has one aligned, signed note")
        requires_review = False

    return _appointment_record(
        appointment,
        matching_notes,
        config,
        documentation_required=True,
        validation_result=result,
        rule_code=rule_code,
        issue_codes=issue_codes,
        reason=_as_sentence("; ".join(reasons)),
        requires_review=requires_review,
        is_exception=result != VALID,
    )


def _appointment_record(
    appointment: pd.Series,
    matching_notes: pd.DataFrame,
    config: ValidationConfig,
    *,
    documentation_required: bool,
    validation_result: str,
    rule_code: str,
    reason: str,
    issue_codes: Iterable[str] = (),
    requires_review: bool = False,
    is_exception: bool = False,
) -> dict[str, Any]:
    note = matching_notes.iloc[0] if len(matching_notes) == 1 else None
    note_ids = _join_unique(matching_notes["note_id"])
    note_provider_ids = _join_unique(matching_notes["provider_id"])
    note_dates = _join_unique(matching_notes["note_date"])
    note_statuses = _join_unique(matching_notes["note_status"])
    return {
        "record_type": "APPOINTMENT",
        "appointment_id": appointment["appointment_id"],
        "provider_id": appointment["provider_id"],
        "appointment_date": appointment["appointment_date"],
        "appointment_status": appointment["appointment_status"],
        "matched_note_id": note["note_id"] if note is not None else note_ids,
        "note_provider_id": (
            note["provider_id"] if note is not None else note_provider_ids
        ),
        "note_date": note["note_date"] if note is not None else note_dates,
        "note_status": note["note_status"] if note is not None else note_statuses,
        "matched_note_count": len(matching_notes),
        "documentation_required": documentation_required,
        "validation_result": validation_result,
        "rule_code": rule_code,
        "issue_codes": "|".join(issue_codes),
        "reason": reason,
        "requires_review": requires_review,
        "is_exception": is_exception,
        "rules_version": config.rules_version,
    }


def _orphan_record(
    appointment_id: str,
    orphan_group: pd.DataFrame,
    config: ValidationConfig,
) -> dict[str, Any]:
    note_ids = _join_unique(orphan_group["note_id"])
    provider_ids = "|".join(sorted(orphan_group["provider_id"].unique()))
    note_dates = "|".join(sorted(orphan_group["note_date"].unique()))
    note_statuses = "|".join(sorted(orphan_group["note_status"].unique()))
    count = len(orphan_group)
    noun = "record references" if count == 1 else "records reference"
    return {
        "record_type": "DOCUMENTATION_ONLY",
        "appointment_id": appointment_id,
        "provider_id": provider_ids,
        "appointment_date": "",
        "appointment_status": "",
        "matched_note_id": note_ids,
        "note_provider_id": provider_ids,
        "note_date": note_dates,
        "note_status": note_statuses,
        "matched_note_count": count,
        "documentation_required": False,
        "validation_result": NEEDS_HUMAN_REVIEW,
        "rule_code": "ORPHAN_DOCUMENTATION",
        "issue_codes": "ORPHAN_DOCUMENTATION",
        "reason": (
            f"{count} documentation {noun} an appointment_id that is absent "
            "from the appointment input."
        ),
        "requires_review": True,
        "is_exception": True,
        "rules_version": config.rules_version,
    }


def _join_unique(values: pd.Series) -> str:
    if values.empty:
        return ""
    return "|".join(sorted(values.unique()))


def _as_sentence(value: str) -> str:
    if not value:
        return value
    return value[0].upper() + value[1:] + "."
