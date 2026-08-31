from __future__ import annotations

import json
from pathlib import Path


WORKFLOW_PATH = Path("n8n/workflows/behavioral-health-validation.json")


def load_workflow(project_root: Path) -> dict[str, object]:
    return json.loads((project_root / WORKFLOW_PATH).read_text(encoding="utf-8"))


def nodes_by_name(workflow: dict[str, object]) -> dict[str, dict[str, object]]:
    return {node["name"]: node for node in workflow["nodes"]}


def test_n8n_export_has_portable_import_shape(project_root: Path) -> None:
    workflow = load_workflow(project_root)
    nodes = workflow["nodes"]

    assert workflow["name"] == "Behavioral Health Validation - Phase 3"
    assert workflow["active"] is False
    assert workflow["settings"]["executionOrder"] == "v1"
    assert workflow["connections"]
    assert len({node["id"] for node in nodes}) == len(nodes)
    assert not any("credentials" in node for node in nodes)
    assert not any(
        keyword in node["type"].lower()
        for node in nodes
        for keyword in ("openai", "langchain", "llm", "agent")
    )


def test_n8n_workflow_exposes_manual_and_webhook_triggers(
    project_root: Path,
) -> None:
    workflow = load_workflow(project_root)
    nodes = nodes_by_name(workflow)
    webhook = nodes["Validation Webhook"]

    assert nodes["Manual Trigger"]["type"] == "n8n-nodes-base.manualTrigger"
    assert webhook["type"] == "n8n-nodes-base.webhook"
    assert webhook["typeVersion"] == 2.1
    assert webhook["parameters"]["httpMethod"] == "POST"
    assert webhook["parameters"]["path"] == "behavioral-health-validation"
    assert webhook["parameters"]["responseMode"] == "onReceived"
    assert (
        webhook["parameters"]["options"]["responseCode"]["values"][
            "customCode"
        ]
        == 202
    )


def test_n8n_http_nodes_match_api_contract_and_retry_safely(
    project_root: Path,
) -> None:
    workflow = load_workflow(project_root)
    nodes = nodes_by_name(workflow)
    create = nodes["Create Validation Run"]
    results = nodes["Fetch Persisted Results"]
    exceptions = nodes["Fetch Persisted Exceptions"]

    assert create["parameters"]["method"] == "POST"
    assert "/api/v1/runs" in create["parameters"]["url"]
    assert any(
        header["name"] == "Idempotency-Key"
        for header in create["parameters"]["headerParameters"]["parameters"]
    )
    assert "validation_request" in create["parameters"]["jsonBody"]
    assert "/results?limit=200&offset=0" in results["parameters"]["url"]
    assert "/exceptions?limit=200&offset=0" in exceptions["parameters"]["url"]

    for node in (create, results, exceptions):
        assert node["type"] == "n8n-nodes-base.httpRequest"
        assert node["typeVersion"] == 4.4
        assert node["retryOnFail"] is True
        assert node["maxTries"] == 3
        assert node["waitBetweenTries"] == 2000
        assert node["onError"] == "continueErrorOutput"


def test_n8n_workflow_routes_exceptions_review_and_terminal_failures(
    project_root: Path,
) -> None:
    workflow = load_workflow(project_root)
    nodes = nodes_by_name(workflow)
    connections = workflow["connections"]

    assert nodes["Any Exceptions?"]["type"] == "n8n-nodes-base.if"
    assert nodes["Any Human Review?"]["type"] == "n8n-nodes-base.if"
    assert "requires_review === true" in nodes["Assemble Workflow Context"][
        "parameters"
    ]["jsCode"]
    review_code = nodes["Prepare Human Review Notification"]["parameters"][
        "jsCode"
    ]
    assert "AWAITING_HUMAN_REVIEW" in review_code
    assert "deterministic_results_unchanged: true" in review_code

    for http_node, failure_node in (
        ("Create Validation Run", "Mark Create Failure"),
        ("Fetch Persisted Results", "Mark Results Failure"),
        ("Fetch Persisted Exceptions", "Mark Exceptions Failure"),
    ):
        error_output = connections[http_node]["main"][1]
        assert any(item["node"] == failure_node for item in error_output)
        assert any(
            item["node"] == "Stop with Operational Error"
            for item in connections[failure_node]["main"][0]
        )


def test_n8n_embedded_records_are_clearly_synthetic(project_root: Path) -> None:
    workflow = load_workflow(project_root)
    prepare_code = nodes_by_name(workflow)["Prepare Synthetic Request"][
        "parameters"
    ]["jsCode"]

    assert "APT-AUTO-" in prepare_code
    assert "PAT-AUTO-" in prepare_code
    assert "PROV-AUTO-" in prepare_code
    assert "n8n-phase3:synthetic-${scenario}-v1" in prepare_code
    assert "deterministic_exception" in prepare_code
    assert "clean" in prepare_code
    assert "api_key" not in prepare_code.lower()
    assert "password" not in prepare_code.lower()
