"""SQLite persistence for completed validation runs and their result rows."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sqlite3
from typing import Any

import pandas as pd

from .exceptions import (
    IdempotencyConflictError,
    PersistenceError,
    RunNotFoundError,
)
from .validation import DETAIL_COLUMNS


SCHEMA_VERSION = 3
_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    created_at: str
    source: str
    rules_version: str
    rules_fingerprint: str | None
    appointment_count: int
    documentation_count: int
    result_count: int
    valid_count: int
    exception_count: int
    review_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SQLiteRunRepository:
    """Small repository using one short-lived SQLite connection per operation."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        """Create the current schema, migrate earlier versions, or reject newer ones."""

        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise PersistenceError(
                        f"Database schema version {version} is newer than supported "
                        f"version {SCHEMA_VERSION}."
                    )
                if version == 0:
                    with connection:
                        _create_schema(connection)
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version == 1:
                    with connection:
                        _migrate_v1_to_v2(connection)
                        _migrate_v2_to_v3(connection)
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version == 2:
                    with connection:
                        _migrate_v2_to_v3(connection)
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise PersistenceError(
                        f"Database schema version {version} is not supported."
                    )
        except PersistenceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PersistenceError("Could not initialize the SQLite database.") from exc

    def healthcheck(self) -> None:
        try:
            with closing(self._connect()) as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError("SQLite health check failed.") from exc

    def save_run(self, run: RunRecord, results: pd.DataFrame) -> None:
        """Insert metadata and all results in one all-or-nothing transaction."""

        self._save_run(run, results)

    def save_run_idempotently(
        self,
        run: RunRecord,
        results: pd.DataFrame,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[RunRecord, bool]:
        """Atomically create a keyed run or replay its completed predecessor."""

        return self._save_run(
            run,
            results,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    def get_run_by_idempotency_key(
        self,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> RunRecord | None:
        """Return a matching completed run, or reject conflicting key reuse."""

        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM validation_runs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError("Could not read the idempotency record.") from exc

        return _matching_idempotent_run(row, request_fingerprint)

    def _save_run(
        self,
        run: RunRecord,
        results: pd.DataFrame,
        *,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> tuple[RunRecord, bool]:
        """Persist a run; keyed writes serialize their check-and-insert step."""

        _validate_new_run_fingerprint(run.rules_fingerprint)
        missing_columns = [column for column in DETAIL_COLUMNS if column not in results]
        if missing_columns:
            raise PersistenceError(
                "Validation results are missing columns required for persistence: "
                + ", ".join(missing_columns)
            )

        try:
            with closing(self._connect()) as connection:
                with connection:
                    if idempotency_key is not None:
                        connection.execute("BEGIN IMMEDIATE")
                        existing = connection.execute(
                            "SELECT * FROM validation_runs WHERE idempotency_key = ?",
                            (idempotency_key,),
                        ).fetchone()
                        matching_run = _matching_idempotent_run(
                            existing, request_fingerprint
                        )
                        if matching_run is not None:
                            return matching_run, True

                    connection.execute(
                        """
                        INSERT INTO validation_runs (
                            run_id, created_at, source, rules_version,
                            rules_fingerprint,
                            appointment_count, documentation_count, result_count,
                            valid_count, exception_count, review_count,
                            idempotency_key, request_fingerprint
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run.run_id,
                            run.created_at,
                            run.source,
                            run.rules_version,
                            run.rules_fingerprint,
                            run.appointment_count,
                            run.documentation_count,
                            run.result_count,
                            run.valid_count,
                            run.exception_count,
                            run.review_count,
                            idempotency_key,
                            request_fingerprint,
                        ),
                    )
                    connection.executemany(
                        _RESULT_INSERT_SQL,
                        (
                            _result_parameters(run.run_id, order, record)
                            for order, record in enumerate(
                                results.to_dict(orient="records")
                            )
                        ),
                    )
            return run, False
        except IdempotencyConflictError:
            raise
        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            raise PersistenceError("Could not persist the validation run.") from exc

    def list_runs(self, *, limit: int, offset: int) -> tuple[int, list[RunRecord]]:
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute("BEGIN")
                    total = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM validation_runs"
                        ).fetchone()[0]
                    )
                    rows = connection.execute(
                        """
                        SELECT * FROM validation_runs
                        ORDER BY created_at DESC, run_id DESC
                        LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    ).fetchall()
            return total, [_row_to_run(row) for row in rows]
        except sqlite3.Error as exc:
            raise PersistenceError("Could not read validation run history.") from exc

    def get_run(self, run_id: str) -> RunRecord:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM validation_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError("Could not read the validation run.") from exc

        if row is None:
            raise RunNotFoundError(f"Validation run {run_id} was not found.")
        return _row_to_run(row)

    def get_results(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        exceptions_only: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute("BEGIN")
                    exists = connection.execute(
                        "SELECT 1 FROM validation_runs WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    if exists is None:
                        raise RunNotFoundError(
                            f"Validation run {run_id} was not found."
                        )

                    exception_clause = (
                        " AND is_exception = 1" if exceptions_only else ""
                    )
                    total = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM validation_results "
                            "WHERE run_id = ?" + exception_clause,
                            (run_id,),
                        ).fetchone()[0]
                    )
                    rows = connection.execute(
                        "SELECT "
                        + ", ".join(DETAIL_COLUMNS)
                        + " FROM validation_results WHERE run_id = ?"
                        + exception_clause
                        + " ORDER BY result_order LIMIT ? OFFSET ?",
                        (run_id, limit, offset),
                    ).fetchall()
            return total, [_row_to_result(row) for row in rows]
        except RunNotFoundError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("Could not read validation results.") from exc

    def count_runs(self) -> int:
        try:
            with closing(self._connect()) as connection:
                return int(
                    connection.execute("SELECT COUNT(*) FROM validation_runs").fetchone()[0]
                )
        except sqlite3.Error as exc:
            raise PersistenceError("Could not count validation runs.") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS validation_runs (
            run_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            rules_version TEXT NOT NULL,
            rules_fingerprint TEXT NOT NULL CHECK (length(rules_fingerprint) = 64),
            appointment_count INTEGER NOT NULL CHECK (appointment_count >= 0),
            documentation_count INTEGER NOT NULL CHECK (documentation_count >= 0),
            result_count INTEGER NOT NULL CHECK (result_count >= 0),
            valid_count INTEGER NOT NULL CHECK (valid_count >= 0),
            exception_count INTEGER NOT NULL CHECK (exception_count >= 0),
            review_count INTEGER NOT NULL CHECK (review_count >= 0),
            idempotency_key TEXT,
            request_fingerprint TEXT CHECK (
                (idempotency_key IS NULL AND request_fingerprint IS NULL)
                OR (
                    idempotency_key IS NOT NULL
                    AND request_fingerprint IS NOT NULL
                    AND length(request_fingerprint) = 64
                )
            )
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS validation_results (
            run_id TEXT NOT NULL,
            result_order INTEGER NOT NULL CHECK (result_order >= 0),
            record_type TEXT NOT NULL,
            appointment_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            appointment_date TEXT NOT NULL,
            appointment_status TEXT NOT NULL,
            matched_note_id TEXT NOT NULL,
            note_provider_id TEXT NOT NULL,
            note_date TEXT NOT NULL,
            note_status TEXT NOT NULL,
            matched_note_count INTEGER NOT NULL CHECK (matched_note_count >= 0),
            documentation_required INTEGER NOT NULL CHECK (documentation_required IN (0, 1)),
            validation_result TEXT NOT NULL,
            rule_code TEXT NOT NULL,
            issue_codes TEXT NOT NULL,
            reason TEXT NOT NULL,
            requires_review INTEGER NOT NULL CHECK (requires_review IN (0, 1)),
            is_exception INTEGER NOT NULL CHECK (is_exception IN (0, 1)),
            rules_version TEXT NOT NULL,
            PRIMARY KEY (run_id, result_order),
            FOREIGN KEY (run_id) REFERENCES validation_runs(run_id) ON DELETE CASCADE
        )
        """
    )
    _create_indexes(connection)


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE validation_runs ADD COLUMN idempotency_key TEXT")
    connection.execute(
        """
        ALTER TABLE validation_runs
        ADD COLUMN request_fingerprint TEXT CHECK (
            (idempotency_key IS NULL AND request_fingerprint IS NULL)
            OR (
                idempotency_key IS NOT NULL
                AND request_fingerprint IS NOT NULL
                AND length(request_fingerprint) = 64
            )
        )
        """
    )
    _create_indexes(connection)


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Add an optional fingerprint while preserving unreconstructable older runs."""

    connection.execute(
        """
        ALTER TABLE validation_runs
        ADD COLUMN rules_fingerprint TEXT CHECK (
            rules_fingerprint IS NULL OR length(rules_fingerprint) = 64
        )
        """
    )


def _create_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_validation_runs_idempotency_key
        ON validation_runs (idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_validation_results_exception
        ON validation_results (run_id, is_exception, result_order)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_validation_runs_created_at
        ON validation_runs (created_at DESC, run_id DESC)
        """
    )


_RESULT_INSERT_SQL = """
    INSERT INTO validation_results (
        run_id, result_order, record_type, appointment_id, provider_id,
        appointment_date, appointment_status, matched_note_id, note_provider_id,
        note_date, note_status, matched_note_count, documentation_required,
        validation_result, rule_code, issue_codes, reason, requires_review,
        is_exception, rules_version
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _result_parameters(
    run_id: str, result_order: int, record: dict[str, Any]
) -> tuple[Any, ...]:
    return (
        run_id,
        result_order,
        _text(record["record_type"]),
        _text(record["appointment_id"]),
        _text(record["provider_id"]),
        _text(record["appointment_date"]),
        _text(record["appointment_status"]),
        _text(record["matched_note_id"]),
        _text(record["note_provider_id"]),
        _text(record["note_date"]),
        _text(record["note_status"]),
        int(record["matched_note_count"]),
        int(bool(record["documentation_required"])),
        _text(record["validation_result"]),
        _text(record["rule_code"]),
        _text(record["issue_codes"]),
        _text(record["reason"]),
        int(bool(record["requires_review"])),
        int(bool(record["is_exception"])),
        _text(record["rules_version"]),
    )


def _row_to_run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        created_at=row["created_at"],
        source=row["source"],
        rules_version=row["rules_version"],
        rules_fingerprint=row["rules_fingerprint"],
        appointment_count=row["appointment_count"],
        documentation_count=row["documentation_count"],
        result_count=row["result_count"],
        valid_count=row["valid_count"],
        exception_count=row["exception_count"],
        review_count=row["review_count"],
    )


def _validate_new_run_fingerprint(fingerprint: str | None) -> None:
    """Require newly inserted runs to carry a SHA-256 hexadecimal digest."""

    if not isinstance(fingerprint, str) or _SHA256_HEX.fullmatch(fingerprint) is None:
        raise PersistenceError(
            "New validation runs require a 64-character SHA-256 hexadecimal "
            "rules_fingerprint."
        )


def _matching_idempotent_run(
    row: sqlite3.Row | None,
    request_fingerprint: str | None,
) -> RunRecord | None:
    if row is None:
        return None
    if (
        request_fingerprint is not None
        and row["request_fingerprint"] == request_fingerprint
    ):
        return _row_to_run(row)
    raise IdempotencyConflictError(
        "The Idempotency-Key was already used with a different request."
    )


def _row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    result = {column: row[column] for column in DETAIL_COLUMNS}
    result["matched_note_count"] = int(result["matched_note_count"])
    result["documentation_required"] = bool(result["documentation_required"])
    result["requires_review"] = bool(result["requires_review"])
    result["is_exception"] = bool(result["is_exception"])
    return result


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)
