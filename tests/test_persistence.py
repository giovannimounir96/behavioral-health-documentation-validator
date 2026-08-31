from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import sqlite3
from threading import Barrier
from uuid import UUID, uuid4

import pandas as pd
import pytest

from bh_doc_validator.config import ValidationConfig
from bh_doc_validator.exceptions import PersistenceError, RunNotFoundError
from bh_doc_validator.io import load_appointments, load_documentation
from bh_doc_validator.persistence import (
    SCHEMA_VERSION,
    RunRecord,
    SQLiteRunRepository,
)
from bh_doc_validator.service import ValidationRunService, validate_batch


def sample_batch(project_root: Path):
    appointments = load_appointments(project_root / "data/sample/appointments.csv")
    documentation = load_documentation(project_root / "data/sample/documentation.csv")
    config = ValidationConfig.from_file(project_root / "config/validation_rules.json")
    return appointments, documentation, validate_batch(appointments, documentation, config)


def make_run(batch, **overrides) -> RunRecord:
    values = {
        "run_id": str(uuid4()),
        "created_at": "2026-08-16T12:00:00.000000Z",
        "source": "pytest",
        "rules_version": batch.rules_version,
        "appointment_count": batch.appointment_count,
        "documentation_count": batch.documentation_count,
        "result_count": batch.result_count,
        "valid_count": batch.valid_count,
        "exception_count": batch.exception_count,
        "review_count": batch.review_count,
    }
    values.update(overrides)
    return RunRecord(**values)


def test_database_initialization_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "validator.sqlite3"
    repository = SQLiteRunRepository(database_path)

    repository.initialize()
    repository.initialize()

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == SCHEMA_VERSION
    assert {"validation_runs", "validation_results"}.issubset(tables)


def test_v1_database_migrates_without_losing_run_or_results(
    tmp_path: Path, project_root: Path
) -> None:
    _, _, batch = sample_batch(project_root)
    run = make_run(batch)
    database_path = tmp_path / "migration.sqlite3"
    repository = SQLiteRunRepository(database_path)
    repository.initialize()
    repository.save_run(run, batch.detailed)

    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP INDEX idx_validation_runs_idempotency_key")
        connection.execute(
            "ALTER TABLE validation_runs DROP COLUMN request_fingerprint"
        )
        connection.execute("ALTER TABLE validation_runs DROP COLUMN idempotency_key")
        connection.execute("PRAGMA user_version = 1")

    migrated = SQLiteRunRepository(database_path)
    migrated.initialize()
    result_total, results = migrated.get_results(run.run_id, limit=100, offset=0)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(validation_runs)")
        }
        stored_idempotency = connection.execute(
            "SELECT idempotency_key, request_fingerprint FROM validation_runs"
        ).fetchone()

    assert version == SCHEMA_VERSION
    assert {"idempotency_key", "request_fingerprint"}.issubset(columns)
    assert migrated.get_run(run.run_id) == run
    assert result_total == batch.result_count
    assert results == batch.detailed.to_dict(orient="records")
    assert stored_idempotency == (None, None)


def test_sample_run_round_trips_and_exceptions_are_filtered(
    tmp_path: Path, project_root: Path
) -> None:
    _, _, batch = sample_batch(project_root)
    run = make_run(batch)
    database_path = tmp_path / "validator.sqlite3"
    repository = SQLiteRunRepository(database_path)
    repository.initialize()

    repository.save_run(run, batch.detailed)

    reopened = SQLiteRunRepository(database_path)
    stored_run = reopened.get_run(run.run_id)
    result_total, results = reopened.get_results(
        run.run_id, limit=100, offset=0
    )
    exception_total, exceptions = reopened.get_results(
        run.run_id, limit=100, offset=0, exceptions_only=True
    )

    assert stored_run == run
    assert result_total == 13
    assert exception_total == 8
    assert results == batch.detailed.to_dict(orient="records")
    assert exceptions == batch.exceptions.to_dict(orient="records")
    assert all(record["is_exception"] is True for record in exceptions)


def test_service_generates_unique_uuid_runs_and_newest_first_history(
    tmp_path: Path, project_root: Path
) -> None:
    appointments, documentation, _ = sample_batch(project_root)
    repository = SQLiteRunRepository(tmp_path / "validator.sqlite3")
    repository.initialize()
    service = ValidationRunService(
        repository, project_root / "config/validation_rules.json"
    )

    first = service.create_run(appointments, documentation, source="first")
    second = service.create_run(appointments, documentation, source="second")
    total, history = repository.list_runs(limit=10, offset=0)

    assert UUID(first.run_id) != UUID(second.run_id)
    assert total == 2
    assert [run.run_id for run in history] == [second.run_id, first.run_id]
    assert all(datetime.fromisoformat(run.created_at.replace("Z", "+00:00")).tzinfo for run in history)


def test_persistence_transaction_rolls_back_partial_run(
    tmp_path: Path, project_root: Path
) -> None:
    _, _, batch = sample_batch(project_root)
    repository = SQLiteRunRepository(tmp_path / "validator.sqlite3")
    repository.initialize()
    run = make_run(batch)
    invalid_results = batch.detailed.copy()
    invalid_results["matched_note_count"] = invalid_results[
        "matched_note_count"
    ].astype(object)
    invalid_results.loc[0, "matched_note_count"] = "not-an-integer"

    with pytest.raises(PersistenceError, match="Could not persist"):
        repository.save_run(run, invalid_results)

    assert repository.count_runs() == 0
    with pytest.raises(RunNotFoundError):
        repository.get_run(run.run_id)


def test_concurrent_idempotent_writes_create_one_complete_run(
    tmp_path: Path, project_root: Path
) -> None:
    _, _, batch = sample_batch(project_root)
    repository = SQLiteRunRepository(tmp_path / "validator.sqlite3")
    repository.initialize()
    candidates = [make_run(batch), make_run(batch)]
    barrier = Barrier(2)

    def save(candidate: RunRecord) -> tuple[RunRecord, bool]:
        barrier.wait()
        return repository.save_run_idempotently(
            candidate,
            batch.detailed,
            idempotency_key="n8n-concurrent-retry",
            request_fingerprint="a" * 64,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, candidates))

    stored_ids = {outcome[0].run_id for outcome in outcomes}
    replay_flags = {outcome[1] for outcome in outcomes}
    stored_run = repository.get_run(next(iter(stored_ids)))
    result_total, _ = repository.get_results(
        stored_run.run_id, limit=100, offset=0
    )

    assert len(stored_ids) == 1
    assert replay_flags == {False, True}
    assert repository.count_runs() == 1
    assert result_total == batch.result_count

    with sqlite3.connect(repository.database_path) as connection:
        key, fingerprint = connection.execute(
            "SELECT idempotency_key, request_fingerprint FROM validation_runs"
        ).fetchone()
    assert key == "n8n-concurrent-retry"
    assert fingerprint == "a" * 64


def test_empty_batch_is_persisted_with_zero_counts(
    tmp_path: Path, project_root: Path
) -> None:
    appointment_path = project_root / "data/sample/appointments.csv"
    documentation_path = project_root / "data/sample/documentation.csv"
    appointments = pd.read_csv(appointment_path, dtype=str, nrows=0)
    documentation = pd.read_csv(documentation_path, dtype=str, nrows=0)
    repository = SQLiteRunRepository(tmp_path / "validator.sqlite3")
    repository.initialize()
    service = ValidationRunService(
        repository, project_root / "config/validation_rules.json"
    )

    run = service.create_run(appointments, documentation, source="empty-test")
    total, results = repository.get_results(run.run_id, limit=10, offset=0)

    assert run.appointment_count == 0
    assert run.documentation_count == 0
    assert run.result_count == 0
    assert run.exception_count == 0
    assert total == 0
    assert results == []


def test_repository_rejects_unsupported_future_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "future.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(PersistenceError, match="newer than supported"):
        SQLiteRunRepository(database_path).initialize()
