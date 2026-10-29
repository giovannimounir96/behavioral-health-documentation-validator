"""Pydantic models that define the public JSON API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppointmentInput(StrictAPIModel):
    appointment_id: str
    patient_id: str
    provider_id: str
    appointment_date: str
    appointment_time: str | None = None
    service_type: str | None = None
    appointment_status: str


class DocumentationInput(StrictAPIModel):
    note_id: str
    appointment_id: str
    patient_id: str
    provider_id: str
    note_date: str
    note_status: str
    note_type: str
    signed_date: str | None = None


class CreateRunRequest(StrictAPIModel):
    source: str = Field(default="api", min_length=1, max_length=100)
    appointments: list[AppointmentInput]
    documentation: list[DocumentationInput]

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class RunResponse(StrictAPIModel):
    run_id: UUID
    created_at: datetime
    source: str
    rules_version: str
    rules_fingerprint: str | None
    appointment_count: int
    documentation_count: int
    result_count: int
    valid_count: int
    exception_count: int
    review_count: int


class RunListResponse(StrictAPIModel):
    total: int
    limit: int
    offset: int
    items: list[RunResponse]


class ValidationResultResponse(StrictAPIModel):
    record_type: str
    appointment_id: str
    provider_id: str
    appointment_date: str
    appointment_status: str
    matched_note_id: str
    note_provider_id: str
    note_date: str
    note_status: str
    matched_note_count: int
    documentation_required: bool
    validation_result: str
    rule_code: str
    issue_codes: str
    reason: str
    requires_review: bool
    is_exception: bool
    rules_version: str


class ResultListResponse(StrictAPIModel):
    run_id: UUID
    total: int
    limit: int
    offset: int
    items: list[ValidationResultResponse]


class HealthResponse(StrictAPIModel):
    status: str
    database: str


class ErrorBody(StrictAPIModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(StrictAPIModel):
    error: ErrorBody
