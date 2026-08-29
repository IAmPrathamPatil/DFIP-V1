"""HTTP ingest invokes existing evaluate_qa and exposes inspector findings."""

from __future__ import annotations

from pathlib import Path

from dfip_api.app import create_app
from dfip_core.ingest.store import InMemoryIngestStore
from dfip_core.transform.store import InMemoryFactStore
from dfip_web.api_client import DfipApiClient
from fastapi.testclient import TestClient

from http_ingest_support import source_row, workbook_bytes
from test_p5_api import (
    AUTH,
    CLIENT_ID,
    DEV_TOKEN,
    JWT_SECRET,
    MISSING_ID,
    RUN_A,
    _encode_jwt,
    inspector_settings,
    make_settings,
    seed_stores,
)
from test_p7_publication import publisher_settings
from test_v2_http_ingest import CLIENT_B, _publisher_app, _upload


def _error(response) -> dict:
    payload = response.json()
    assert "error" in payload
    return payload["error"]


def _findings(http: TestClient, run_id: str, *, headers=AUTH):
    return http.get(f"/api/v1/processing-runs/{run_id}/qa-findings", headers=headers)


def test_clean_upload_evaluates_qa_without_findings_or_publish(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "clean.xlsx", [source_row()])
    uploaded = _upload(http, content, "clean.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["published"] is False
    assert body["processing_run"]["status"] == "succeeded"
    assert body["processing_run"]["qa_verdict"] == "pass"
    run_id = body["processing_run"]["processing_run_id"]
    assert run_id in app.state.qa_store._by_run
    listed = _findings(http, run_id)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["items"] == []
    assert payload["pagination"]["total"] == 0
    assert payload["summary"] == {
        "total": 0,
        "error": 0,
        "warning": 0,
        "info": 0,
        "by_rule": {},
    }
    published = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert published["items"] == []


def test_upload_with_findings_still_requires_explicit_publish(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    rows = [source_row(), source_row(**{"Sent": 90})]
    content = workbook_bytes(tmp_path / "dup.xlsx", rows)
    uploaded = _upload(http, content, "dup.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["published"] is False
    assert body["processing_run"]["status"] == "succeeded"
    assert body["processing_run"]["qa_verdict"] == "fail"
    run_id = body["processing_run"]["processing_run_id"]
    listed = _findings(http, run_id).json()
    rule_ids = {item["rule_id"] for item in listed["items"]}
    assert "QA-DUPLICATE-GRAIN" in rule_ids
    assert "QA-REJECTED-ROWS" in rule_ids
    assert listed["summary"]["error"] >= 1
    assert all(item["processing_run_id"] == run_id for item in listed["items"])
    assert all(item["client_id"] == CLIENT_ID for item in listed["items"])
    created = http.post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_ID, "processing_run_id": run_id},
    )
    assert created.status_code == 422
    assert created.json()["error"]["message"] == "Processing run QA verdict is fail."
    published = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert published["items"] == []


def test_warning_findings_allow_explicit_publish(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(
        tmp_path / "failed-gt-sent.xlsx",
        [
            source_row(
                **{
                    "Sent": 10,
                    "Failed": 12,
                    "Delivered": 8,
                    "Unique Impressions": 7,
                    "Unique Clicks": 1,
                }
            )
        ],
    )
    uploaded = _upload(http, content, "failed-gt-sent.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["published"] is False
    assert body["processing_run"]["qa_verdict"] == "warn"
    run_id = body["processing_run"]["processing_run_id"]
    listed = _findings(http, run_id).json()
    assert "QA-IMPOSSIBLE-FAILED" in {item["rule_id"] for item in listed["items"]}
    assert listed["summary"]["error"] == 0
    assert listed["summary"]["warning"] >= 1
    created = http.post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_ID, "processing_run_id": run_id},
    )
    assert created.status_code == 201
    facts = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID, "limit": 200},
    ).json()
    assert facts["pagination"]["total"] == 1
    assert facts["items"][0]["processing_run_id"] == run_id


def test_failed_processing_records_run_failed_and_cannot_publish(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "num.xlsx", [source_row(**{"Sent": "twelve"})])
    uploaded = _upload(http, content, "num.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["published"] is False
    run = body["processing_run"]
    assert run["status"] == "failed"
    assert run["qa_verdict"] == "fail"
    listed = _findings(http, run["processing_run_id"]).json()
    rule_ids = {item["rule_id"] for item in listed["items"]}
    assert "QA-RUN-FAILED" in rule_ids
    assert "QA-INVALID-NUMERIC" in rule_ids
    assert "QA-REJECTED-ROWS" in rule_ids
    blocked = http.post(
        "/api/v1/publications",
        headers=AUTH,
        json={"client_id": CLIENT_ID, "processing_run_id": run["processing_run_id"]},
    )
    assert blocked.status_code == 422
    current = http.get(
        "/api/v1/publications/current",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert current["publication"] is None


def test_header_failure_does_not_create_qa_success_state(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(
        tmp_path / "bad-headers.xlsx",
        [source_row()],
        header_override={12: "Campaign"},
    )
    uploaded = _upload(http, content, "bad-headers.xlsx", client_id=CLIENT_ID)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["processing_run"] is None
    assert body["batch"]["status"] == "failed"
    assert app.state.qa_store._by_run == {}
    missing = _findings(http, MISSING_ID)
    assert missing.status_code == 404


def test_unauthorized_and_reader_cannot_read_findings(tmp_path: Path) -> None:
    app = _publisher_app()
    http = TestClient(app)
    content = workbook_bytes(tmp_path / "a.xlsx", [source_row()])
    run_id = _upload(http, content, "a.xlsx", client_id=CLIENT_ID).json()["processing_run"][
        "processing_run_id"
    ]
    unauth = http.get(f"/api/v1/processing-runs/{run_id}/qa-findings")
    assert unauth.status_code == 401
    assert _error(unauth)["code"] == "AUTHENTICATION_FAILED"
    reader = create_app(
        settings=make_settings(dfip_dev_auth_role="reader"),
        ingest_store=app.state.ingest_store,
        fact_store=app.state.fact_store,
        qa_store=app.state.qa_store,
    )
    forbidden = TestClient(reader).get(
        f"/api/v1/processing-runs/{run_id}/qa-findings", headers=AUTH
    )
    assert forbidden.status_code == 403
    assert _error(forbidden)["code"] == "AUTHORIZATION_FAILED"


def test_client_b_cannot_read_client_a_findings(tmp_path: Path) -> None:
    ingest = InMemoryIngestStore()
    facts = InMemoryFactStore()
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=ingest,
        fact_store=facts,
    )
    http = TestClient(app)
    headers_a = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"}
    headers_b = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_B)}"}
    content = workbook_bytes(tmp_path / "dup.xlsx", [source_row(), source_row(**{"Sent": 90})])
    uploaded = _upload(http, content, "dup.xlsx", headers=headers_a)
    assert uploaded.status_code == 201
    run_id = uploaded.json()["processing_run"]["processing_run_id"]
    own = _findings(http, run_id, headers=headers_a)
    assert own.status_code == 200
    assert own.json()["pagination"]["total"] >= 1
    leak = _findings(http, run_id, headers=headers_b)
    assert leak.status_code == 404
    assert "QA-DUPLICATE-GRAIN" not in leak.text
    assert "camp-a" not in leak.text


def test_python_client_lists_findings_after_upload(tmp_path: Path) -> None:
    app = create_app(
        settings=publisher_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )
    with DfipApiClient(
        "http://testserver",
        token=DEV_TOKEN,
        http_client=TestClient(app),
    ) as api:
        content = workbook_bytes(tmp_path / "a.xlsx", [source_row(), source_row(**{"Sent": 90})])
        uploaded = api.upload_workbook(content, "dup.xlsx", client_id=CLIENT_ID)
        run_id = uploaded["processing_run"]["processing_run_id"]
        listed = api.list_qa_findings(run_id, limit=200)
        assert listed["pagination"]["total"] >= 1
        assert {item["rule_id"] for item in listed["items"]} >= {
            "QA-DUPLICATE-GRAIN",
            "QA-REJECTED-ROWS",
        }


def test_seeded_run_without_http_evaluation_returns_empty_findings() -> None:
    ingest, facts = seed_stores()
    app = create_app(settings=inspector_settings(), ingest_store=ingest, fact_store=facts)
    response = TestClient(app).get(f"/api/v1/processing-runs/{RUN_A}/qa-findings", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["pagination"]["total"] == 0
