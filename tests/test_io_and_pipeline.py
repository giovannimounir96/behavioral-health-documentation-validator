from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from bh_doc_validator.config import ValidationConfig
from bh_doc_validator.exceptions import ConfigurationError, InputDataError
from bh_doc_validator.io import load_appointments, load_documentation
from bh_doc_validator.pipeline import run_pipeline
from bh_doc_validator.reporting import build_provider_summary
from bh_doc_validator.validation import DETAIL_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


def test_sample_pipeline_generates_expected_reports(tmp_path: Path) -> None:
    result = run_pipeline(
        ROOT / "data/sample/appointments.csv",
        ROOT / "data/sample/documentation.csv",
        ROOT / "config/validation_rules.json",
        tmp_path,
    )

    assert result.appointment_count == 12
    assert result.documentation_count == 9
    assert result.valid_count == 2
    assert result.exception_count == 8
    assert result.detailed_report.exists()
    assert result.provider_summary.exists()
    assert result.exceptions_report.exists()

    detailed = pd.read_csv(result.detailed_report)
    exceptions = pd.read_csv(result.exceptions_report)
    summary = pd.read_csv(result.provider_summary).set_index("provider_id")

    assert len(detailed) == 13
    assert len(exceptions) == 8
    assert summary.loc["PROV-001", "documentation_completion_percentage"] == 25.0
    assert summary.loc["PROV-002", "documentation_completion_percentage"] == 0.0
    assert summary.loc["PROV-003", "documentation_completion_percentage"] == 50.0


def test_duplicate_appointment_ids_fail_with_clear_error(tmp_path: Path) -> None:
    source = pd.read_csv(ROOT / "data/sample/appointments.csv", dtype=str)
    duplicated = pd.concat([source.iloc[[0]], source.iloc[[0]]], ignore_index=True)
    path = tmp_path / "appointments.csv"
    duplicated.to_csv(path, index=False)

    with pytest.raises(InputDataError, match="duplicate appointment_id.*APT-5001"):
        load_appointments(path)


def test_duplicate_note_ids_fail_with_clear_error(tmp_path: Path) -> None:
    source = pd.read_csv(ROOT / "data/sample/documentation.csv", dtype=str)
    duplicated = pd.concat([source.iloc[[0]], source.iloc[[0]]], ignore_index=True)
    path = tmp_path / "documentation.csv"
    duplicated.to_csv(path, index=False)

    with pytest.raises(InputDataError, match="duplicate note_id.*NOTE-7001"):
        load_documentation(path)


def test_invalid_date_reports_csv_row(tmp_path: Path) -> None:
    source = pd.read_csv(ROOT / "data/sample/appointments.csv", dtype=str)
    source.loc[0, "appointment_date"] = "07/01/2026"
    path = tmp_path / "appointments.csv"
    source.to_csv(path, index=False)

    with pytest.raises(InputDataError, match=r"YYYY-MM-DD.*2"):
        load_appointments(path)


def test_date_requires_zero_padded_iso_format(tmp_path: Path) -> None:
    source = pd.read_csv(ROOT / "data/sample/appointments.csv", dtype=str)
    source.loc[0, "appointment_date"] = "2026-7-1"
    path = tmp_path / "appointments.csv"
    source.to_csv(path, index=False)

    with pytest.raises(InputDataError, match=r"YYYY-MM-DD.*2"):
        load_appointments(path)


def test_invalid_configuration_is_rejected(tmp_path: Path) -> None:
    source = json.loads(
        (ROOT / "config/validation_rules.json").read_text(encoding="utf-8")
    )
    source["no_show_policy"] = "guess"
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="no_show_policy"):
        ValidationConfig.from_file(path)


def test_non_string_no_show_policy_is_configuration_error(tmp_path: Path) -> None:
    source = json.loads(
        (ROOT / "config/validation_rules.json").read_text(encoding="utf-8")
    )
    source["no_show_policy"] = ["review"]
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="no_show_policy"):
        ValidationConfig.from_file(path)


def test_empty_batch_reports_keep_stable_headers(tmp_path: Path) -> None:
    appointment_columns = pd.read_csv(
        ROOT / "data/sample/appointments.csv", nrows=0
    ).columns
    documentation_columns = pd.read_csv(
        ROOT / "data/sample/documentation.csv", nrows=0
    ).columns
    appointments_path = tmp_path / "appointments.csv"
    documentation_path = tmp_path / "documentation.csv"
    pd.DataFrame(columns=appointment_columns).to_csv(appointments_path, index=False)
    pd.DataFrame(columns=documentation_columns).to_csv(documentation_path, index=False)

    result = run_pipeline(
        appointments_path,
        documentation_path,
        ROOT / "config/validation_rules.json",
        tmp_path / "output",
    )

    detailed = pd.read_csv(result.detailed_report)
    exceptions = pd.read_csv(result.exceptions_report)
    summary = pd.read_csv(result.provider_summary)
    assert detailed.columns.tolist() == DETAIL_COLUMNS
    assert exceptions.columns.tolist() == DETAIL_COLUMNS
    assert summary.columns.tolist() == build_provider_summary(detailed).columns.tolist()
    assert detailed.empty and exceptions.empty and summary.empty
