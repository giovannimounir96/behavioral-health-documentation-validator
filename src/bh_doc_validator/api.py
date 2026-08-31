"""FastAPI adapter for JSON validation requests and persisted run history."""

from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, Header, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import pandas as pd

from .api_models import (
    CreateRunRequest,
    ErrorResponse,
    HealthResponse,
    ResultListResponse,
    RunListResponse,
    RunResponse,
)
from .exceptions import (
    ConfigurationError,
    IdempotencyConflictError,
    InputDataError,
    PersistenceError,
    RunNotFoundError,
)
from .io import (
    APPOINTMENT_COLUMNS,
    DOCUMENTATION_COLUMNS,
    prepare_appointments,
    prepare_documentation,
)
from .persistence import RunRecord, SQLiteRunRepository
from .service import ValidationRunService


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data/local/validator.sqlite3"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/validation_rules.json"
PageLimit = Annotated[int, Query(ge=1, le=200)]
PageOffset = Annotated[int, Query(ge=0)]
IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        description=(
            "Optional retry key. Reusing it with the same JSON request replays "
            "the completed run; reusing it with different JSON returns 409."
        ),
    ),
]
IDEMPOTENCY_RESPONSE_HEADER = {
    "Idempotency-Replayed": {
        "description": "true when this response replays an existing completed run",
        "schema": {"type": "string", "enum": ["true", "false"]},
    }
}


def create_app(
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    repository: SQLiteRunRepository | None = None,
) -> FastAPI:
    """Create an isolated API instance; injectable paths keep tests local."""

    run_repository = repository or SQLiteRunRepository(database_path)
    run_service = ValidationRunService(run_repository, config_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        run_repository.initialize()
        yield

    application = FastAPI(
        title="Behavioral Health Documentation Validator API",
        version="0.3.0",
        description=(
            "Portfolio-safe local API for deterministic validation of synthetic "
            "appointment and documentation records."
        ),
        lifespan=lifespan,
    )
    application.state.repository = run_repository
    application.state.service = run_service

    _register_error_handlers(application)
    _register_routes(application, run_repository, run_service)
    return application


def _register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def request_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "location": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "REQUEST_VALIDATION_ERROR",
            "The JSON request does not match the required schema.",
            details,
        )

    @application.exception_handler(InputDataError)
    async def input_data_error(_: Request, exc: InputDataError) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "INPUT_DATA_ERROR",
            str(exc),
        )

    @application.exception_handler(RunNotFoundError)
    async def run_not_found(_: Request, exc: RunNotFoundError) -> JSONResponse:
        return _error_response(
            status.HTTP_404_NOT_FOUND,
            "RUN_NOT_FOUND",
            str(exc),
        )

    @application.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(
        _: Request, exc: IdempotencyConflictError
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_409_CONFLICT,
            "IDEMPOTENCY_CONFLICT",
            str(exc),
        )

    @application.exception_handler(ConfigurationError)
    async def configuration_error(_: Request, exc: ConfigurationError) -> JSONResponse:
        LOGGER.error("Validation configuration failed", exc_info=exc)
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "CONFIGURATION_ERROR",
            "The server validation rules are unavailable or invalid.",
        )

    @application.exception_handler(PersistenceError)
    async def persistence_error(_: Request, exc: PersistenceError) -> JSONResponse:
        LOGGER.error("SQLite persistence failed", exc_info=exc)
        return _error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PERSISTENCE_UNAVAILABLE",
            "Validation run storage is temporarily unavailable.",
        )

    @application.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("Unexpected API failure", exc_info=exc)
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "An unexpected server error occurred.",
        )


def _register_routes(
    application: FastAPI,
    repository: SQLiteRunRepository,
    service: ValidationRunService,
) -> None:
    @application.get(
        "/health",
        response_model=HealthResponse,
        responses={
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["system"],
    )
    def health() -> HealthResponse:
        repository.healthcheck()
        return HealthResponse(status="ok", database="available")

    @application.post(
        "/api/v1/runs",
        response_model=RunResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            200: {
                "model": RunResponse,
                "description": "A matching completed run was replayed.",
                "headers": IDEMPOTENCY_RESPONSE_HEADER,
            },
            201: {
                "description": "A validation run was created.",
                "headers": IDEMPOTENCY_RESPONSE_HEADER,
            },
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["validation runs"],
    )
    def create_run(
        request_body: CreateRunRequest,
        response: Response,
        idempotency_key: IdempotencyKey = None,
    ) -> RunResponse:
        appointments = _appointments_frame(request_body)
        documentation = _documentation_frame(request_body)
        if idempotency_key is None:
            run = service.create_run(
                appointments,
                documentation,
                source=request_body.source,
            )
        else:
            creation = service.create_run_idempotently(
                appointments,
                documentation,
                source=request_body.source,
                idempotency_key=idempotency_key,
                request_fingerprint=_request_fingerprint(request_body),
            )
            run = creation.run
            response.status_code = (
                status.HTTP_200_OK
                if creation.replayed
                else status.HTTP_201_CREATED
            )
            response.headers["Idempotency-Replayed"] = str(
                creation.replayed
            ).lower()
        response.headers["Location"] = f"/api/v1/runs/{run.run_id}"
        return _run_response(run)

    @application.get(
        "/api/v1/runs",
        response_model=RunListResponse,
        responses={
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["validation runs"],
    )
    def list_runs(limit: PageLimit = 50, offset: PageOffset = 0) -> RunListResponse:
        total, runs = repository.list_runs(limit=limit, offset=offset)
        return RunListResponse(
            total=total,
            limit=limit,
            offset=offset,
            items=[_run_response(run) for run in runs],
        )

    @application.get(
        "/api/v1/runs/{run_id}",
        response_model=RunResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["validation runs"],
    )
    def get_run(run_id: UUID) -> RunResponse:
        return _run_response(repository.get_run(str(run_id)))

    @application.get(
        "/api/v1/runs/{run_id}/results",
        response_model=ResultListResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["validation results"],
    )
    def get_results(
        run_id: UUID,
        limit: PageLimit = 100,
        offset: PageOffset = 0,
    ) -> ResultListResponse:
        return _result_list_response(
            repository,
            str(run_id),
            limit=limit,
            offset=offset,
            exceptions_only=False,
        )

    @application.get(
        "/api/v1/runs/{run_id}/exceptions",
        response_model=ResultListResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["validation results"],
    )
    def get_exceptions(
        run_id: UUID,
        limit: PageLimit = 100,
        offset: PageOffset = 0,
    ) -> ResultListResponse:
        return _result_list_response(
            repository,
            str(run_id),
            limit=limit,
            offset=offset,
            exceptions_only=True,
        )


def _appointments_frame(request_body: CreateRunRequest) -> pd.DataFrame:
    raw = [record.model_dump() for record in request_body.appointments]
    return prepare_appointments(
        pd.DataFrame(raw, columns=APPOINTMENT_COLUMNS),
        location_label="JSON item",
        position_offset=1,
    )


def _documentation_frame(request_body: CreateRunRequest) -> pd.DataFrame:
    raw = [record.model_dump() for record in request_body.documentation]
    return prepare_documentation(
        pd.DataFrame(raw, columns=DOCUMENTATION_COLUMNS),
        location_label="JSON item",
        position_offset=1,
    )


def _run_response(run: RunRecord) -> RunResponse:
    return RunResponse.model_validate(run.to_dict())


def _request_fingerprint(request_body: CreateRunRequest) -> str:
    canonical_json = json.dumps(
        request_body.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(canonical_json.encode("utf-8")).hexdigest()


def _result_list_response(
    repository: SQLiteRunRepository,
    run_id: str,
    *,
    limit: int,
    offset: int,
    exceptions_only: bool,
) -> ResultListResponse:
    total, items = repository.get_results(
        run_id,
        limit=limit,
        offset=offset,
        exceptions_only=exceptions_only,
    )
    return ResultListResponse(
        run_id=UUID(run_id),
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        content["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=content)


app = create_app(
    database_path=os.environ.get("BH_VALIDATOR_DATABASE_PATH", DEFAULT_DATABASE_PATH),
    config_path=os.environ.get("BH_VALIDATOR_CONFIG_PATH", DEFAULT_CONFIG_PATH),
)
