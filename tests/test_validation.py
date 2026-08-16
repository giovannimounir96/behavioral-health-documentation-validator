from __future__ import annotations

import pandas as pd
import pytest

from bh_doc_validator.config import ValidationConfig
from bh_doc_validator.validation import (
    MISSING_DOCUMENTATION,
    NEEDS_HUMAN_REVIEW,
    NO_DOCUMENTATION_REQUIRED,
    POSSIBLE_DATA_MISMATCH,
    UNSIGNED_DOCUMENTATION,
    VALID,
    validate_records,
)


def make_config(
    *,
    tolerance: int = 1,
    no_show_policy: str = "no_documentation_required",
    require_signed_date: bool = True,
) -> ValidationConfig:
    return ValidationConfig(
        rules_version="test-1",
        date_tolerance_days=tolerance,
        no_show_policy=no_show_policy,
        require_signed_date_for_signed_notes=require_signed_date,
        appointment_status_groups={
            "documentation_required": frozenset({"completed"}),
            "cancelled": frozenset({"cancelled"}),
            "no_show": frozenset({"no show"}),
            "rescheduled": frozenset({"rescheduled"}),
        },
        note_status_groups={
            "signed": frozenset({"signed"}),
            "unsigned": frozenset({"unsigned"}),
        },
    )


def make_appointment(**overrides: str) -> pd.DataFrame:
    row = {
        "appointment_id": "APT-1",
        "patient_id": "PAT-1",
        "provider_id": "PROV-1",
        "appointment_date": "2026-07-01",
        "appointment_time": "09:00",
        "service_type": "Medication Management",
        "appointment_status": "Completed",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def make_documentation(**overrides: str) -> pd.DataFrame:
    row = {
        "note_id": "NOTE-1",
        "appointment_id": "APT-1",
        "patient_id": "PAT-1",
        "provider_id": "PROV-1",
        "note_date": "2026-07-01",
        "note_status": "Signed",
        "note_type": "Progress Note",
        "signed_date": "2026-07-01",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def result_row(
    appointments: pd.DataFrame | None = None,
    documentation: pd.DataFrame | None = None,
    config: ValidationConfig | None = None,
) -> pd.Series:
    appointments = appointments if appointments is not None else make_appointment()
    documentation = (
        documentation if documentation is not None else make_documentation()
    )
    return validate_records(appointments, documentation, config or make_config()).iloc[0]


def test_completed_signed_note_is_valid() -> None:
    row = result_row()

    assert row["validation_result"] == VALID
    assert row["rule_code"] == "COMPLETED_SIGNED_NOTE"
    assert not row["is_exception"]
    assert not row["requires_review"]


def test_completed_without_note_is_missing_but_not_ambiguous() -> None:
    row = result_row(documentation=make_documentation().iloc[0:0])

    assert row["validation_result"] == MISSING_DOCUMENTATION
    assert row["issue_codes"] == "NO_NOTE_FOUND"
    assert row["is_exception"]
    assert not row["requires_review"]


def test_unsigned_note_is_deterministic_exception() -> None:
    row = result_row(
        documentation=make_documentation(note_status="Unsigned", signed_date="")
    )

    assert row["validation_result"] == UNSIGNED_DOCUMENTATION
    assert row["issue_codes"] == "NOTE_UNSIGNED"
    assert row["is_exception"]
    assert not row["requires_review"]


@pytest.mark.parametrize("status", ["Cancelled", "No Show", "Rescheduled"])
def test_non_completed_lifecycle_statuses_do_not_require_documentation(
    status: str,
) -> None:
    row = result_row(
        appointments=make_appointment(appointment_status=status),
        documentation=make_documentation().iloc[0:0],
    )

    assert row["validation_result"] == NO_DOCUMENTATION_REQUIRED
    assert not row["documentation_required"]
    assert not row["is_exception"]


def test_no_show_policy_can_route_record_for_review() -> None:
    row = result_row(
        appointments=make_appointment(appointment_status="No Show"),
        documentation=make_documentation().iloc[0:0],
        config=make_config(no_show_policy="review"),
    )

    assert row["validation_result"] == NEEDS_HUMAN_REVIEW
    assert row["rule_code"] == "NO_SHOW_POLICY_REVIEW"
    assert row["requires_review"]
    assert not row["documentation_required"]


@pytest.mark.parametrize(
    ("field", "issue_code"),
    [
        ("patient_id", "PATIENT_ID_MISMATCH"),
        ("provider_id", "PROVIDER_ID_MISMATCH"),
    ],
)
def test_identity_mismatch_requires_review(field: str, issue_code: str) -> None:
    row = result_row(documentation=make_documentation(**{field: "DIFFERENT"}))

    assert row["validation_result"] == POSSIBLE_DATA_MISMATCH
    assert issue_code in row["issue_codes"]
    assert row["requires_review"]


def test_date_tolerance_is_inclusive() -> None:
    row = result_row(documentation=make_documentation(note_date="2026-07-02"))

    assert row["validation_result"] == VALID


def test_date_outside_tolerance_requires_review() -> None:
    row = result_row(documentation=make_documentation(note_date="2026-07-03"))

    assert row["validation_result"] == NEEDS_HUMAN_REVIEW
    assert row["rule_code"] == "NOTE_DATE_OUTSIDE_TOLERANCE"


def test_signed_note_without_signed_date_requires_review() -> None:
    row = result_row(documentation=make_documentation(signed_date=""))

    assert row["validation_result"] == NEEDS_HUMAN_REVIEW
    assert row["rule_code"] == "SIGNED_DATE_MISSING"


def test_unsigned_note_with_signed_date_is_inconsistent() -> None:
    row = result_row(documentation=make_documentation(note_status="Unsigned"))

    assert row["validation_result"] == NEEDS_HUMAN_REVIEW
    assert "NOTE_UNSIGNED" in row["issue_codes"]
    assert "UNSIGNED_WITH_SIGNED_DATE" in row["issue_codes"]


def test_unknown_note_status_requires_review() -> None:
    row = result_row(documentation=make_documentation(note_status="Draft"))

    assert row["validation_result"] == NEEDS_HUMAN_REVIEW
    assert row["rule_code"] == "UNKNOWN_NOTE_STATUS"


def test_unknown_appointment_status_is_excluded_and_reviewed() -> None:
    row = result_row(
        appointments=make_appointment(appointment_status="Pending Review"),
        documentation=make_documentation().iloc[0:0],
    )

    assert row["validation_result"] == NEEDS_HUMAN_REVIEW
    assert row["rule_code"] == "UNKNOWN_APPOINTMENT_STATUS"
    assert not row["documentation_required"]


def test_multiple_notes_are_not_resolved_automatically() -> None:
    documentation = pd.concat(
        [make_documentation(), make_documentation(note_id="NOTE-2")],
        ignore_index=True,
    )
    row = result_row(documentation=documentation)

    assert row["validation_result"] == NEEDS_HUMAN_REVIEW
    assert row["rule_code"] == "MULTIPLE_DOCUMENTATION_RECORDS"
    assert row["matched_note_count"] == 2
    assert row["matched_note_id"] == "NOTE-1|NOTE-2"


def test_signed_date_requirement_can_be_disabled() -> None:
    row = result_row(
        documentation=make_documentation(signed_date=""),
        config=make_config(require_signed_date=False),
    )

    assert row["validation_result"] == VALID


def test_status_mapping_ignores_case_and_surrounding_whitespace() -> None:
    row = result_row(
        appointments=make_appointment(appointment_status="  cOmPlEtEd  "),
        documentation=make_documentation(note_status="  sIgNeD  "),
    )

    assert row["validation_result"] == VALID


def test_orphan_documentation_is_reported() -> None:
    documentation = make_documentation(
        note_id="NOTE-9", appointment_id="APT-NOT-IN-INPUT"
    )
    report = validate_records(make_appointment(), documentation, make_config())

    orphan = report[report["record_type"].eq("DOCUMENTATION_ONLY")].iloc[0]
    assert orphan["validation_result"] == NEEDS_HUMAN_REVIEW
    assert orphan["rule_code"] == "ORPHAN_DOCUMENTATION"
    assert orphan["requires_review"]


def test_orphan_note_order_is_deterministic() -> None:
    documentation = pd.concat(
        [
            make_documentation(note_id="NOTE-9", appointment_id="APT-ORPHAN"),
            make_documentation(note_id="NOTE-8", appointment_id="APT-ORPHAN"),
        ],
        ignore_index=True,
    )

    first = validate_records(make_appointment(), documentation, make_config())
    second = validate_records(
        make_appointment(), documentation.iloc[::-1].reset_index(drop=True), make_config()
    )

    first_orphan = first[first["record_type"].eq("DOCUMENTATION_ONLY")].iloc[0]
    second_orphan = second[second["record_type"].eq("DOCUMENTATION_ONLY")].iloc[0]
    assert first_orphan["matched_note_id"] == "NOTE-8|NOTE-9"
    assert first_orphan.to_dict() == second_orphan.to_dict()


def test_reason_preserves_source_value_case() -> None:
    row = result_row(documentation=make_documentation(note_status="Needs MD Review"))

    assert "Needs MD Review" in row["reason"]


def test_primary_result_does_not_hide_secondary_issue_codes() -> None:
    row = result_row(
        documentation=make_documentation(
            provider_id="PROV-OTHER",
            note_date="2026-07-05",
            note_status="Unsigned",
            signed_date="",
        )
    )

    assert row["validation_result"] == POSSIBLE_DATA_MISMATCH
    assert row["issue_codes"].split("|") == [
        "PROVIDER_ID_MISMATCH",
        "NOTE_DATE_OUTSIDE_TOLERANCE",
        "NOTE_UNSIGNED",
    ]


def test_input_order_does_not_change_appointment_results() -> None:
    appointments = pd.concat(
        [make_appointment(), make_appointment(appointment_id="APT-2")],
        ignore_index=True,
    )
    documentation = pd.concat(
        [make_documentation(), make_documentation(note_id="NOTE-2", appointment_id="APT-2")],
        ignore_index=True,
    )

    first = validate_records(appointments, documentation, make_config())
    second = validate_records(
        appointments.iloc[::-1].reset_index(drop=True),
        documentation.iloc[::-1].reset_index(drop=True),
        make_config(),
    )

    pd.testing.assert_frame_equal(first, second)
