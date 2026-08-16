"""CSV input loading and data-contract validation."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .exceptions import InputDataError


APPOINTMENT_COLUMNS = [
    "appointment_id",
    "patient_id",
    "provider_id",
    "appointment_date",
    "appointment_time",
    "service_type",
    "appointment_status",
]

DOCUMENTATION_COLUMNS = [
    "note_id",
    "appointment_id",
    "patient_id",
    "provider_id",
    "note_date",
    "note_status",
    "note_type",
    "signed_date",
]


def load_appointments(path: str | Path) -> pd.DataFrame:
    frame = _read_csv(path, "appointments")
    return prepare_appointments(
        frame, location_label="CSV row", position_offset=2
    )


def prepare_appointments(
    frame: pd.DataFrame,
    *,
    location_label: str = "record",
    position_offset: int = 1,
) -> pd.DataFrame:
    """Normalize and validate appointment records from any input adapter."""

    _require_columns(frame, APPOINTMENT_COLUMNS, "appointments")
    frame = _clean_strings(frame, APPOINTMENT_COLUMNS)
    _require_values(
        frame,
        ["appointment_id", "patient_id", "provider_id", "appointment_date", "appointment_status"],
        "appointments",
        location_label,
        position_offset,
    )
    _require_valid_dates(
        frame,
        ["appointment_date"],
        "appointments",
        location_label,
        position_offset,
    )

    duplicate_ids = sorted(
        frame.loc[frame["appointment_id"].duplicated(keep=False), "appointment_id"].unique()
    )
    if duplicate_ids:
        raise InputDataError(
            "appointments contains duplicate appointment_id values: "
            + ", ".join(duplicate_ids)
        )
    return frame


def load_documentation(path: str | Path) -> pd.DataFrame:
    frame = _read_csv(path, "documentation")
    return prepare_documentation(
        frame, location_label="CSV row", position_offset=2
    )


def prepare_documentation(
    frame: pd.DataFrame,
    *,
    location_label: str = "record",
    position_offset: int = 1,
) -> pd.DataFrame:
    """Normalize and validate documentation records from any input adapter."""

    _require_columns(frame, DOCUMENTATION_COLUMNS, "documentation")
    frame = _clean_strings(frame, DOCUMENTATION_COLUMNS)
    _require_values(
        frame,
        [
            "note_id",
            "appointment_id",
            "patient_id",
            "provider_id",
            "note_date",
            "note_status",
            "note_type",
        ],
        "documentation",
        location_label,
        position_offset,
    )
    _require_valid_dates(
        frame,
        ["note_date"],
        "documentation",
        location_label,
        position_offset,
    )
    _require_optional_valid_dates(
        frame,
        ["signed_date"],
        "documentation",
        location_label,
        position_offset,
    )

    duplicate_note_ids = sorted(
        frame.loc[frame["note_id"].duplicated(keep=False), "note_id"].unique()
    )
    if duplicate_note_ids:
        raise InputDataError(
            "documentation contains duplicate note_id values: "
            + ", ".join(duplicate_note_ids)
        )
    return frame


def _read_csv(path: str | Path, dataset_name: str) -> pd.DataFrame:
    csv_path = Path(path)
    try:
        return pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    except FileNotFoundError as exc:
        raise InputDataError(f"{dataset_name} file not found: {csv_path}") from exc
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        raise InputDataError(f"Could not read {dataset_name} CSV: {exc}") from exc


def _require_columns(frame: pd.DataFrame, required: list[str], dataset_name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise InputDataError(
            f"{dataset_name} is missing required columns: {', '.join(missing)}"
        )


def _clean_strings(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    cleaned = frame.copy()
    for column in columns:
        cleaned[column] = cleaned[column].fillna("").astype(str).str.strip()
    return cleaned


def _require_values(
    frame: pd.DataFrame,
    columns: list[str],
    dataset_name: str,
    location_label: str,
    position_offset: int,
) -> None:
    for column in columns:
        missing_rows = frame.index[frame[column].eq("")].tolist()
        if missing_rows:
            positions = ", ".join(
                str(index + position_offset) for index in missing_rows
            )
            raise InputDataError(
                f"{dataset_name}.{column} has blank required values on "
                f"{location_label}(s): {positions}"
            )


def _require_valid_dates(
    frame: pd.DataFrame,
    columns: list[str],
    dataset_name: str,
    location_label: str,
    position_offset: int,
) -> None:
    for column in columns:
        valid_shape = frame[column].str.fullmatch(r"\d{4}-\d{2}-\d{2}")
        parsed = pd.to_datetime(frame[column], format="%Y-%m-%d", errors="coerce")
        invalid_rows = frame.index[~valid_shape | parsed.isna()].tolist()
        if invalid_rows:
            positions = ", ".join(
                str(index + position_offset) for index in invalid_rows
            )
            raise InputDataError(
                f"{dataset_name}.{column} must use YYYY-MM-DD on "
                f"{location_label}(s): {positions}"
            )


def _require_optional_valid_dates(
    frame: pd.DataFrame,
    columns: list[str],
    dataset_name: str,
    location_label: str,
    position_offset: int,
) -> None:
    for column in columns:
        populated = frame[column].ne("")
        valid_shape = frame[column].str.fullmatch(r"\d{4}-\d{2}-\d{2}")
        parsed = pd.to_datetime(frame[column], format="%Y-%m-%d", errors="coerce")
        invalid_rows = frame.index[populated & (~valid_shape | parsed.isna())].tolist()
        if invalid_rows:
            positions = ", ".join(
                str(index + position_offset) for index in invalid_rows
            )
            raise InputDataError(
                f"{dataset_name}.{column} must be blank or use YYYY-MM-DD on "
                f"{location_label}(s): {positions}"
            )
