"""Coordinate input loading, validation, reporting, and CSV output."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from .config import ValidationConfig
from .io import load_appointments, load_documentation
from .service import validate_batch


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    detailed_report: Path
    provider_summary: Path
    exceptions_report: Path
    appointment_count: int
    documentation_count: int
    valid_count: int
    exception_count: int


def run_pipeline(
    appointments_path: str | Path,
    documentation_path: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> PipelineResult:
    """Run the complete Phase 1 pipeline and persist three reports."""

    LOGGER.info("Loading validation configuration from %s", config_path)
    config = ValidationConfig.from_file(config_path)
    appointments = load_appointments(appointments_path)
    documentation = load_documentation(documentation_path)
    LOGGER.info(
        "Loaded %d appointments and %d documentation records",
        len(appointments),
        len(documentation),
    )

    batch = validate_batch(appointments, documentation, config)

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    detailed_path = destination / "validation_report.csv"
    provider_path = destination / "provider_summary.csv"
    exceptions_path = destination / "exceptions_report.csv"

    batch.detailed.to_csv(detailed_path, index=False)
    batch.provider_summary.to_csv(provider_path, index=False)
    batch.exceptions.to_csv(exceptions_path, index=False)

    LOGGER.info(
        "Wrote reports to %s (%d valid records, %d exceptions)",
        destination,
        batch.valid_count,
        batch.exception_count,
    )
    return PipelineResult(
        detailed_report=detailed_path,
        provider_summary=provider_path,
        exceptions_report=exceptions_path,
        appointment_count=len(appointments),
        documentation_count=len(documentation),
        valid_count=batch.valid_count,
        exception_count=batch.exception_count,
    )
