from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from bh_doc_validator.api import create_app
from bh_doc_validator.exceptions import PersistenceError
from bh_doc_validator.persistence import SQLiteRunRepository


@pytest.fixture
def api_client(tmp_path: Path, project_root: Path):
    application = create_app(
        database_path=tmp_path / "api.sqlite3",
        config_path=project_root / "config/validation_rules.json",
    )
    with TestClient(application) as client:
        yield client


def test_health_reports_available_database(api_client: TestClient) -> None:
    response = api_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "available"}


def test_openapi_documents_runtime_error_envelopes(
    tmp_path: Path, project_root: Path
) -> None:
    application = create_app(
        database_path=tmp_path / "api.sqlite3",
        config_path=project_root / "config/validation_rules.json",
    )
    schema = application.openapi()

    parameterized_gets = [
        "/api/v1/runs",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/results",
        "/api/v1/runs/{run_id}/exceptions",
    ]
    for path in parameterized_gets:
        error_schema = schema["paths"][path]["get"]["responses"]["422"][
            "content"
        ]["application/json"]["schema"]
        assert error_schema["$ref"].endswith("/ErrorResponse")

    health_error_schema = schema["paths"]["/health"]["get"]["responses"][
        "503"
    ]["content"]["application/json"]["schema"]
    assert health_error_schema["$ref"].endswith("/ErrorResponse")


def test_committed_sample_json_is_a_runnable_request(
    api_client: TestClient, project_root: Path
) -> None:
    payload = json.loads(
        (project_root / "data/sample/validation_request.json").read_text(
            encoding="utf-8"
        )
    )

    response = api_client.post("/api/v1/runs", json=payload)

    assert response.status_code == 201
    assert response.json()["result_count"] == 13
    assert response.json()["exception_count"] == 8


def test_post_sample_persists_run_results_and_exceptions(
    api_client: TestClient, sample_payload: dict[str, object]
) -> None:
    create_response = api_client.post("/api/v1/runs", json=sample_payload)

    assert create_response.status_code == 201
    created = create_response.json()
    run_id = str(UUID(created["run_id"]))
    assert create_response.headers["location"] == f"/api/v1/runs/{run_id}"
    assert created == {
        "run_id": run_id,
        "created_at": created["created_at"],
        "source": "pytest",
        "rules_version": "1.0.0",
        "appointment_count": 12,
        "documentation_count": 9,
        "result_count": 13,
        "valid_count": 2,
        "exception_count": 8,
        "review_count": 6,
    }

    detail_response = api_client.get(f"/api/v1/runs/{run_id}")
    results_response = api_client.get(f"/api/v1/runs/{run_id}/results")
    exceptions_response = api_client.get(f"/api/v1/runs/{run_id}/exceptions")

    assert detail_response.status_code == 200
    assert detail_response.json() == created
    assert results_response.status_code == 200
    assert results_response.json()["total"] == 13
    assert exceptions_response.status_code == 200
    assert exceptions_response.json()["total"] == 8
    assert all(item["is_exception"] for item in exceptions_response.json()["items"])
    assert isinstance(results_response.json()["items"][0]["matched_note_count"], int)
    assert isinstance(results_response.json()["items"][0]["requires_review"], bool)


def test_two_api_runs_have_unique_ids_and_paginated_history(
    api_client: TestClient, sample_payload: dict[str, object]
) -> None:
    first = api_client.post("/api/v1/runs", json=sample_payload).json()
    second = api_client.post("/api/v1/runs", json=sample_payload).json()

    response = api_client.get("/api/v1/runs?limit=1&offset=0")
    body = response.json()

    assert response.status_code == 200
    assert first["run_id"] != second["run_id"]
    assert body["total"] == 2
    assert body["limit"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["run_id"] == second["run_id"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/results",
        "/api/v1/runs/{run_id}/exceptions",
    ],
)
def test_unknown_run_returns_stable_404(api_client: TestClient, path: str) -> None:
    response = api_client.get(path.format(run_id=uuid4()))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_malformed_run_id_returns_request_validation_error(
    api_client: TestClient,
) -> None:
    response = api_client.get("/api/v1/runs/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_structurally_invalid_request_returns_422_and_persists_nothing(
    api_client: TestClient,
) -> None:
    response = api_client.post("/api/v1/runs", json={"appointments": []})
    history = api_client.get("/api/v1/runs").json()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert history["total"] == 0


def test_malformed_json_is_sanitized(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/runs",
        content='{"appointments": [secret-broken-json]',
        headers={"content-type": "application/json"},
    )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert "secret-broken-json" not in json.dumps(body)


@pytest.mark.parametrize(
    ("mutator", "expected_text"),
    [
        (
            lambda payload: payload["appointments"].append(
                payload["appointments"][0].copy()
            ),
            "duplicate appointment_id",
        ),
        (
            lambda payload: payload["appointments"][0].update(
                {"appointment_date": "07/01/2026"}
            ),
            "YYYY-MM-DD",
        ),
    ],
)
def test_domain_invalid_request_returns_422_without_run(
    api_client: TestClient,
    sample_payload: dict[str, object],
    mutator,
    expected_text: str,
) -> None:
    mutator(sample_payload)

    response = api_client.post("/api/v1/runs", json=sample_payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INPUT_DATA_ERROR"
    assert expected_text in response.json()["error"]["message"]
    assert api_client.get("/api/v1/runs").json()["total"] == 0


def test_json_data_error_identifies_json_item_position(
    api_client: TestClient, sample_payload: dict[str, object]
) -> None:
    sample_payload["appointments"][0]["appointment_date"] = "07/01/2026"

    response = api_client.post("/api/v1/runs", json=sample_payload)

    assert response.status_code == 422
    assert "JSON item(s): 1" in response.json()["error"]["message"]


def test_business_exception_is_successful_run(
    api_client: TestClient, sample_payload: dict[str, object]
) -> None:
    sample_payload["appointments"][0]["appointment_status"] = "Unknown Workflow State"

    response = api_client.post("/api/v1/runs", json=sample_payload)

    assert response.status_code == 201
    run = response.json()
    exceptions = api_client.get(
        f"/api/v1/runs/{run['run_id']}/exceptions"
    ).json()
    assert any(
        item["rule_code"] == "UNKNOWN_APPOINTMENT_STATUS"
        for item in exceptions["items"]
    )


def test_empty_json_batch_is_valid(api_client: TestClient) -> None:
    response = api_client.post(
        "/api/v1/runs",
        json={"source": "empty", "appointments": [], "documentation": []},
    )

    assert response.status_code == 201
    assert response.json()["result_count"] == 0
    assert response.json()["exception_count"] == 0


def test_source_is_trimmed_before_length_validation(api_client: TestClient) -> None:
    source = "  " + ("x" * 100) + "  "

    response = api_client.post(
        "/api/v1/runs",
        json={"source": source, "appointments": [], "documentation": []},
    )

    assert response.status_code == 201
    assert response.json()["source"] == "x" * 100


@pytest.mark.parametrize("source", ["   ", "x" * 101])
def test_source_rejects_invalid_trimmed_length(
    api_client: TestClient, source: str
) -> None:
    response = api_client.post(
        "/api/v1/runs",
        json={"source": source, "appointments": [], "documentation": []},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_configuration_error_is_generic_and_does_not_persist(
    tmp_path: Path,
) -> None:
    invalid_config = tmp_path / "bad-rules.json"
    invalid_config.write_text("not valid JSON", encoding="utf-8")
    application = create_app(
        database_path=tmp_path / "api.sqlite3",
        config_path=invalid_config,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/runs",
            json={"appointments": [], "documentation": []},
        )
        history = client.get("/api/v1/runs").json()

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "CONFIGURATION_ERROR",
            "message": "The server validation rules are unavailable or invalid.",
        }
    }
    assert history["total"] == 0


def test_configuration_io_error_uses_configuration_taxonomy(tmp_path: Path) -> None:
    application = create_app(
        database_path=tmp_path / "api.sqlite3",
        config_path=tmp_path,
    )

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/runs",
            json={"appointments": [], "documentation": []},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "CONFIGURATION_ERROR"


def test_persistence_failure_returns_sanitized_503(
    tmp_path: Path,
    project_root: Path,
    sample_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteRunRepository(tmp_path / "api.sqlite3")
    application = create_app(
        config_path=project_root / "config/validation_rules.json",
        repository=repository,
    )

    def fail_save(*_args, **_kwargs):
        raise PersistenceError("secret database path and implementation details")

    monkeypatch.setattr(repository, "save_run", fail_save)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/runs", json=sample_payload)
        history = client.get("/api/v1/runs").json()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PERSISTENCE_UNAVAILABLE"
    assert "secret" not in json.dumps(response.json())
    assert history["total"] == 0


def test_health_persistence_failure_returns_sanitized_503(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteRunRepository(tmp_path / "api.sqlite3")
    application = create_app(
        config_path=project_root / "config/validation_rules.json",
        repository=repository,
    )

    def fail_healthcheck():
        raise PersistenceError("secret read-side database details")

    monkeypatch.setattr(repository, "healthcheck", fail_healthcheck)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PERSISTENCE_UNAVAILABLE"
    assert "secret" not in json.dumps(response.json())


def test_unexpected_error_returns_sanitized_500(
    tmp_path: Path,
    project_root: Path,
    sample_payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = create_app(
        database_path=tmp_path / "api.sqlite3",
        config_path=project_root / "config/validation_rules.json",
    )

    def fail_unexpectedly(*_args, **_kwargs):
        raise RuntimeError("secret unexpected implementation detail")

    monkeypatch.setattr(application.state.service, "create_run", fail_unexpectedly)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/runs", json=sample_payload)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in json.dumps(response.json())
