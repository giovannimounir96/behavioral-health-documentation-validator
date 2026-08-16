"""Shared application services for CSV, API, and future workflow adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .config import ValidationConfig
from .persistence import RunRecord, SQLiteRunRepository
from .reporting import build_exceptions_report, build_provider_summary
from .validation import VALID, validate_records


@dataclass(frozen=True)
class ValidationBatch:
    """In-memory result shared by every delivery adapter."""

    detailed: pd.DataFrame
    provider_summary: pd.DataFrame
    exceptions: pd.DataFrame
    appointment_count: int
    documentation_count: int
    result_count: int
    valid_count: int
    exception_count: int
    review_count: int
    rules_version: str


def validate_batch(
    appointments: pd.DataFrame,
    documentation: pd.DataFrame,
    config: ValidationConfig,
) -> ValidationBatch:
    """Apply the deterministic engine and build all derived reports in memory."""

    detailed = validate_records(appointments, documentation, config)
    provider_summary = build_provider_summary(detailed)
    exceptions = build_exceptions_report(detailed)
    return ValidationBatch(
        detailed=detailed,
        provider_summary=provider_summary,
        exceptions=exceptions,
        appointment_count=len(appointments),
        documentation_count=len(documentation),
        result_count=len(detailed),
        valid_count=int(detailed["validation_result"].eq(VALID).sum()),
        exception_count=len(exceptions),
        review_count=int(detailed["requires_review"].sum()),
        rules_version=config.rules_version,
    )


class ValidationRunService:
    """Validate one batch and persist its completed run atomically."""

    def __init__(
        self,
        repository: SQLiteRunRepository,
        config_path: str | Path,
    ) -> None:
        self.repository = repository
        self.config_path = Path(config_path)

    def create_run(
        self,
        appointments: pd.DataFrame,
        documentation: pd.DataFrame,
        *,
        source: str,
    ) -> RunRecord:
        config = ValidationConfig.from_file(self.config_path)
        batch = validate_batch(appointments, documentation, config)
        run = RunRecord(
            run_id=str(uuid4()),
            created_at=_utc_now(),
            source=source,
            rules_version=batch.rules_version,
            appointment_count=batch.appointment_count,
            documentation_count=batch.documentation_count,
            result_count=batch.result_count,
            valid_count=batch.valid_count,
            exception_count=batch.exception_count,
            review_count=batch.review_count,
        )
        self.repository.save_run(run, batch.detailed)
        return run


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
