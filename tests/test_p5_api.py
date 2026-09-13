"""P5 API + authentication foundation tests.

These tests drive the HTTP boundary with FastAPI's TestClient against the real
application, injected in-memory P3/P4 stores, and the real auth dependency.
They do not open PostgreSQL and do not claim live database success.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import jwt
import pytest
from dfip_api.app import create_app
from dfip_api.auth import Principal, authenticate_bearer
from dfip_api.errors import (
    AUTHORIZATION_FAILED,
    AuthConfigurationError,
    AuthenticationError,
    PersistenceUnavailableError,
    authorization_error_response,
)
from dfip_api.schemas import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from dfip_config.settings import Settings
from dfip_core.ingest.headers import expected_source_headers
from dfip_core.ingest.store import (
    BatchRecord,
    InMemoryIngestStore,
    ProcessingRunRecord,
    SourceFileRecord,
    StagedRowRecord,
)
from dfip_core.transform import ConfigBinder, FactRecord, InMemoryFactStore, transform_row
from dfip_core.transform.store import SupersededFact
from fastapi.testclient import TestClient

CLIENT_ID = "a0000000-0000-4000-8000-000000000001"
FILE_A = "b0000000-0000-4000-8000-000000000001"
FILE_B = "b0000000-0000-4000-8000-000000000002"
FILE_C = "b0000000-0000-4000-8000-000000000003"
BATCH_A = "c0000000-0000-4000-8000-000000000001"
BATCH_B = "c0000000-0000-4000-8000-000000000002"
BATCH_C = "c0000000-0000-4000-8000-000000000003"
RUN_A = "d0000000-0000-4000-8000-000000000001"
RUN_B = "d0000000-0000-4000-8000-000000000002"
RUN_C = "d0000000-0000-4000-8000-000000000003"
MISSING_ID = "e0000000-0000-4000-8000-000000000099"
DEV_TOKEN = "p5-test-dev-token"
JWT_SECRET = "p5-test-jwt-secret-not-for-production"
PRODUCTION_JWT_SECRET = "dfip-p13a-accept-jwt-secret-ok32"
PRODUCTION_BOOTSTRAP_TOKEN = "dfip-p13a-bootstrap-token-ok"
HTTPS_WEB_ORIGIN = "https://app.example.invalid"
HTTPS_API_BASE = "https://api.example.invalid"
LOCAL_PLACEHOLDER_DSN = "postgresql://postgres@127.0.0.1:1/dfip"
REMOTE_TLS_DSN = "postgresql://dfip@db.example.internal:5432/dfip?sslmode=require"
SECRET_PATH = "file:///C:/Users/secret/workbook.xlsx"
AUTH = {"Authorization": f"Bearer {DEV_TOKEN}"}
PROTECTED_PATHS = (
    "/api/v1/session",
    "/api/v1/source-files",
    f"/api/v1/source-files/{FILE_A}",
    "/api/v1/batches",
    f"/api/v1/batches/{BATCH_A}",
    f"/api/v1/batches/{BATCH_A}/staged-rows",
    "/api/v1/processing-runs",
    f"/api/v1/processing-runs/{RUN_A}",
    f"/api/v1/processing-runs/{RUN_A}/qa-findings",
    "/api/v1/facts",
    "/api/v1/facts/history",
    "/api/v1/ops/ready",
)
ROOT = Path(__file__).resolve().parents[1]


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "dfip_env": "test",
        "dfip_auth_mode": "dev_token",
        "dfip_dev_auth_token": DEV_TOKEN,
        "dfip_dev_auth_role": "reader",
        "dfip_dev_auth_client_id": "",
        "dfip_auth_secret": "",
        "database_url": "",
        "dfip_api_prefix": "/api/v1",
        "dfip_web_origin": "http://127.0.0.1:3000",
        "dfip_bootstrap_token": "",
        "dfip_ask_provider": "none",
        "dfip_ask_api_key": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def production_settings(**overrides: Any) -> Settings:
    """Valid production-grade settings for startup tests. Local DSN needs no TLS."""
    values: dict[str, Any] = {
        "dfip_env": "production",
        "dfip_auth_mode": "jwt",
        "dfip_auth_secret": PRODUCTION_JWT_SECRET,
        "database_url": LOCAL_PLACEHOLDER_DSN,
        "dfip_web_origin": HTTPS_WEB_ORIGIN,
        "dfip_api_base_url": HTTPS_API_BASE,
        "dfip_bootstrap_token": "",
        "dfip_storage_endpoint": str(
            Path(tempfile.gettempdir()) / "dfip-p13b-test-archive"
        ),
    }
    values.update(overrides)
    return make_settings(**values)


def inspector_settings(**overrides: Any) -> Settings:
    """Working-set tests run as publisher. make_settings still defaults to reader."""
    return make_settings(dfip_dev_auth_role="publisher", **overrides)


def _ts(hour: int) -> datetime:
    return datetime(2025, 8, 1, hour, 0, 0, tzinfo=UTC)


def seed_stores() -> tuple[InMemoryIngestStore, InMemoryFactStore]:
    ingest = InMemoryIngestStore()
    files = [
        SourceFileRecord(
            id=FILE_A,
            client_id=CLIENT_ID,
            sha256="a" * 64,
            original_filename="alpha.xlsx",
            byte_size=10,
            source_kind="native_export",
            uploaded_at=_ts(1),
            storage_uri=SECRET_PATH,
        ),
        SourceFileRecord(
            id=FILE_B,
            client_id=CLIENT_ID,
            sha256="b" * 64,
            original_filename="bravo.xlsx",
            byte_size=20,
            source_kind="legacy_workbook",
            uploaded_at=_ts(2),
            storage_uri=SECRET_PATH,
        ),
        SourceFileRecord(
            id=FILE_C,
            client_id=CLIENT_ID,
            sha256="c" * 64,
            original_filename="charlie.xlsx",
            byte_size=30,
            source_kind="native_export",
            uploaded_at=_ts(3),
        ),
    ]
    for record in files:
        ingest.source_files[record.sha256] = record
        ingest.source_files_by_id[record.id] = record

    batches = [
        BatchRecord(
            id=BATCH_A,
            source_file_id=FILE_A,
            client_id=CLIENT_ID,
            status="processed",
            row_count_declared=3,
            row_count_staged=3,
            row_count_rejected=0,
            observed_day_min=date(2025, 8, 1),
            observed_day_max=date(2025, 8, 3),
            created_at=_ts(1),
            completed_at=_ts(4),
            error_summary=None,
            worksheet_name="Web-Engage Raw",
            header_row=1,
            source_start_column="K",
            empty_row_count=0,
        ),
        BatchRecord(
            id=BATCH_B,
            source_file_id=FILE_B,
            client_id=CLIENT_ID,
            status="failed",
            row_count_declared=None,
            row_count_staged=0,
            row_count_rejected=1,
            observed_day_min=None,
            observed_day_max=None,
            created_at=_ts(2),
            completed_at=_ts(3),
            error_summary="header contract failed",
            worksheet_name="Web-Engage Raw",
            header_row=1,
            source_start_column="K",
            empty_row_count=2,
        ),
        BatchRecord(
            id=BATCH_C,
            source_file_id=FILE_C,
            client_id=CLIENT_ID,
            status="staged",
            row_count_declared=1,
            row_count_staged=1,
            row_count_rejected=0,
            observed_day_min=date(2025, 7, 31),
            observed_day_max=date(2025, 7, 31),
            created_at=_ts(3),
            completed_at=None,
            error_summary=None,
            worksheet_name="Web-Engage Raw",
            header_row=1,
            source_start_column="K",
            empty_row_count=0,
        ),
    ]
    for record in batches:
        ingest.batches[record.id] = record

    raw_blank: dict[str, Any] = dict.fromkeys(expected_source_headers())
    raw_blank["Campaign ID"] = "camp-blank"
    raw_blank["Variation ID"] = ""
    raw_blank["Day"] = "2025-08-01"
    raw_null = dict(raw_blank)
    raw_null["Campaign ID"] = None
    raw_keep = dict(raw_blank)
    raw_keep["Campaign Name"] = " leading"

    ingest.staged_rows.extend(
        [
            StagedRowRecord(
                id="stg-2",
                batch_id=BATCH_A,
                source_row_number=2,
                raw=raw_keep,
                campaign_id="camp-1",
                variation_id="var-1",
                day=date(2025, 8, 1),
            ),
            StagedRowRecord(
                id="stg-3",
                batch_id=BATCH_A,
                source_row_number=3,
                raw=raw_blank,
                campaign_id="camp-blank",
                variation_id="",
                day=date(2025, 8, 2),
            ),
            StagedRowRecord(
                id="stg-4",
                batch_id=BATCH_A,
                source_row_number=4,
                raw=raw_null,
                campaign_id=None,
                variation_id=None,
                day=date(2025, 8, 3),
            ),
        ]
    )

    runs = [
        ProcessingRunRecord(
            id=RUN_A,
            batch_id=BATCH_A,
            campaign_label_version_id="a0000000-0000-4000-8000-000000000022",
            template_label_version_id="a0000000-0000-4000-8000-000000000034",
            rate_card_version_id="a0000000-0000-4000-8000-000000000012",
            label_group_version_id="a0000000-0000-4000-8000-000000000041",
            engine_version="0.4.0",
            started_at=_ts(1),
            finished_at=_ts(4),
            status="succeeded",
            qa_verdict="pass",
        ),
        ProcessingRunRecord(
            id=RUN_B,
            batch_id=BATCH_B,
            campaign_label_version_id=None,
            template_label_version_id=None,
            rate_card_version_id=None,
            label_group_version_id=None,
            engine_version=None,
            started_at=_ts(2),
            finished_at=_ts(3),
            status="failed",
            qa_verdict=None,
        ),
        ProcessingRunRecord(
            id=RUN_C,
            batch_id=BATCH_C,
            campaign_label_version_id=None,
            template_label_version_id=None,
            rate_card_version_id=None,
            label_group_version_id=None,
            engine_version="0.4.0",
            started_at=_ts(3),
            finished_at=None,
            status="pending",
            qa_verdict=None,
        ),
    ]
    for record in runs:
        ingest.processing_runs[record.id] = record

    facts = InMemoryFactStore()
    previous = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-1",
        variation_id="var-1",
        variation_id_key="var-1",
        day=date(2025, 8, 1),
        campaign_name=" leading",
        filter_logic_1=None,
        template_status="",
        total_cost=Decimal("1.00"),
        processing_run_id=RUN_B,
        batch_id=BATCH_A,
        first_seen_at=_ts(1),
        last_seen_at=_ts(1),
    )
    current = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-1",
        variation_id="var-1",
        variation_id_key="var-1",
        day=date(2025, 8, 1),
        campaign_name=" leading",
        filter_logic_1=None,
        template_status="",
        total_cost=Decimal("2.50"),
        revenue_inr=Decimal("1234.56"),
        processing_run_id=RUN_A,
        batch_id=BATCH_A,
        first_seen_at=_ts(1),
        last_seen_at=_ts(4),
    )
    later = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-2",
        variation_id=None,
        variation_id_key="",
        day=date(2025, 8, 2),
        campaign_name="other",
        filter_logic_1="group",
        template_status="Utility",
        total_cost=Decimal("0"),
        processing_run_id=RUN_A,
        batch_id=BATCH_A,
        first_seen_at=_ts(2),
        last_seen_at=_ts(2),
    )
    third = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-3",
        variation_id="var-3",
        variation_id_key="var-3",
        day=date(2025, 8, 3),
        processing_run_id=RUN_A,
        batch_id=BATCH_A,
        first_seen_at=_ts(3),
        last_seen_at=_ts(3),
    )
    facts.facts[current.key] = current
    facts.facts[later.key] = later
    facts.facts[third.key] = third
    facts.history.append(
        SupersededFact(fact=previous, superseded_at=_ts(4), superseded_by_run_id=RUN_A)
    )
    return ingest, facts


@pytest.fixture
def client() -> TestClient:
    ingest, facts = seed_stores()
    app = create_app(settings=inspector_settings(), ingest_store=ingest, fact_store=facts)
    return TestClient(app)


def _error(response) -> dict[str, Any]:
    payload = response.json()
    assert "error" in payload
    assert "code" in payload["error"]
    assert "message" in payload["error"]
    return payload["error"]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_is_public_and_shaped(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "application": "dfip-api",
        "environment": "test",
        "database": {"configured": False, "status": "not_checked"},
    }


def test_health_does_not_claim_database_when_url_is_set() -> None:
    ingest, facts = seed_stores()
    app = create_app(
        settings=make_settings(
            database_url="postgresql://example.invalid/dfip",
            dfip_dev_auth_client_id=CLIENT_ID,
        ),
        ingest_store=ingest,
        fact_store=facts,
    )
    body = TestClient(app).get("/health").json()
    assert body["database"] == {"configured": True, "status": "not_checked"}
    assert body["status"] == "ok"


def test_health_ignores_authorization_header(client: TestClient) -> None:
    response = client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Routing / OpenAPI
# ---------------------------------------------------------------------------


def test_api_v1_routes_exist_in_openapi(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/health" in paths
    for path in (
        "/api/v1/session",
        "/api/v1/source-files",
        "/api/v1/source-files/{source_file_id}",
        "/api/v1/batches",
        "/api/v1/batches/{batch_id}",
        "/api/v1/batches/{batch_id}/staged-rows",
        "/api/v1/processing-runs",
        "/api/v1/processing-runs/{processing_run_id}",
        "/api/v1/processing-runs/{processing_run_id}/qa-findings",
        "/api/v1/facts",
        "/api/v1/facts/history",
        "/api/v1/ops/ready",
    ):
        assert path in paths
        assert "get" in paths[path]
    assert "/api/v1/batches/{batch_id}/process" in paths
    assert "post" in paths["/api/v1/batches/{batch_id}/process"]
    assert "/api/v1/batches/{batch_id}/cancel" in paths
    assert "post" in paths["/api/v1/batches/{batch_id}/cancel"]
    assert "delete" in paths["/api/v1/batches/{batch_id}"]
    assert "/api/v1/publications/progress" in paths
    assert "get" in paths["/api/v1/publications/progress"]
    assert "/api/v1/processing-runs/{processing_run_id}/qa" in paths
    assert "post" in paths["/api/v1/processing-runs/{processing_run_id}/qa"]


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/not-a-resource", headers=AUTH)
    assert response.status_code == 404
    assert _error(response)["code"] == "NOT_FOUND"
    assert "traceback" not in response.text.lower()


def test_resource_routes_are_read_only(client: TestClient) -> None:
    response = client.post("/api/v1/facts", headers=AUTH, json={})
    assert response.status_code == 405
    assert _error(response)["code"] == "METHOD_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_protected_routes_require_credentials(client: TestClient) -> None:
    for path in PROTECTED_PATHS:
        response = client.get(path)
        assert response.status_code == 401, path
        assert _error(response)["code"] == "AUTHENTICATION_FAILED"


def test_valid_dev_token_reaches_session() -> None:
    ingest, facts = seed_stores()
    app = create_app(settings=make_settings(), ingest_store=ingest, fact_store=facts)
    response = TestClient(app).get("/api/v1/session", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "subject": "dev",
        "auth_mode": "dev_token",
        "role": "reader",
        "client_id": None,
        "clients": [],
    }


def test_invalid_and_malformed_authentication_fail(client: TestClient) -> None:
    cases = [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Bearer"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "bearer"},
        {"Authorization": f"Bearer {DEV_TOKEN} extra"},
        {"Authorization": "Token " + DEV_TOKEN},
    ]
    for headers in cases:
        response = client.get("/api/v1/session", headers=headers)
        assert response.status_code == 401, headers
        assert _error(response)["code"] == "AUTHENTICATION_FAILED"
        assert DEV_TOKEN not in response.text


def test_empty_dev_token_is_not_a_bypass() -> None:
    app = create_app(
        settings=make_settings(dfip_dev_auth_token=""),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )
    test_client = TestClient(app)
    assert test_client.get("/api/v1/facts").status_code == 401
    assert test_client.get("/api/v1/facts", headers={"Authorization": "Bearer "}).status_code == 401
    assert (
        test_client.get("/api/v1/facts", headers={"Authorization": "Bearer anything"}).status_code
        == 401
    )


def test_authenticate_bearer_is_the_dependency_used() -> None:
    settings = make_settings()
    principal = authenticate_bearer(settings, f"Bearer {DEV_TOKEN}")
    assert isinstance(principal, Principal)
    assert principal.subject == "dev"
    with pytest.raises(AuthenticationError):
        authenticate_bearer(settings, "Bearer no")


def test_production_refuses_dev_token() -> None:
    with pytest.raises(AuthConfigurationError):
        create_app(
            settings=make_settings(dfip_env="production", dfip_auth_mode="dev_token"),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_staging_refuses_dev_token() -> None:
    with pytest.raises(AuthConfigurationError):
        create_app(
            settings=make_settings(dfip_env="staging", dfip_auth_mode="dev_token"),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_jwt_mode_requires_secret() -> None:
    with pytest.raises(AuthConfigurationError):
        create_app(
            settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=""),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_production_jwt_requires_secret() -> None:
    with pytest.raises(AuthConfigurationError):
        create_app(
            settings=make_settings(
                dfip_env="production", dfip_auth_mode="jwt", dfip_auth_secret=""
            ),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def _jwt_app() -> TestClient:
    ingest, facts = seed_stores()
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=ingest,
        fact_store=facts,
    )
    return TestClient(app)


def _encode_jwt(secret: str = JWT_SECRET, **claims: Any) -> str:
    payload = {
        "sub": "user-1",
        "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        "role": "reader",
    }
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


def test_valid_jwt_succeeds() -> None:
    test_client = _jwt_app()
    token = _encode_jwt(client_id=CLIENT_ID, role="publisher")
    response = test_client.get("/api/v1/session", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["subject"] == "user-1"
    assert response.json()["auth_mode"] == "jwt"
    assert response.json()["role"] == "publisher"
    assert response.json()["client_id"] == CLIENT_ID


def test_invalid_jwt_variants_fail() -> None:
    test_client = _jwt_app()
    expired = _encode_jwt(exp=datetime.now(tz=UTC) - timedelta(minutes=1))
    missing_exp = jwt.encode({"sub": "user-1"}, JWT_SECRET, algorithm="HS256")
    missing_sub = jwt.encode(
        {"exp": datetime.now(tz=UTC) + timedelta(minutes=5)}, JWT_SECRET, algorithm="HS256"
    )
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "sub": "user-1",
                    "exp": int((datetime.now(tz=UTC) + timedelta(minutes=5)).timestamp()),
                }
            ).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    unsigned = f"{header}.{payload}."
    cases = [
        "not-a-jwt",
        _encode_jwt(secret="other-secret-that-is-long-enough-for-hmac"),
        expired,
        missing_exp,
        missing_sub,
        unsigned,
    ]
    for token in cases:
        response = test_client.get("/api/v1/session", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token
        assert _error(response)["code"] == "AUTHENTICATION_FAILED"
        assert JWT_SECRET not in response.text


def test_authorization_error_envelope_is_safe() -> None:
    response = authorization_error_response()
    assert response.status_code == 403
    payload = json.loads(response.body)
    assert payload["error"]["code"] == AUTHORIZATION_FAILED
    assert "traceback" not in response.body.decode().lower()


# ---------------------------------------------------------------------------
# Validation / pagination
# ---------------------------------------------------------------------------


def test_invalid_uuid_and_date_are_rejected(client: TestClient) -> None:
    uuid_response = client.get("/api/v1/source-files/not-a-uuid", headers=AUTH)
    assert uuid_response.status_code == 422
    assert _error(uuid_response)["code"] == "VALIDATION_ERROR"

    date_response = client.get("/api/v1/facts?day=not-a-date", headers=AUTH)
    assert date_response.status_code == 422
    assert _error(date_response)["code"] == "VALIDATION_ERROR"

    filter_response = client.get("/api/v1/facts?client_id=not-a-uuid", headers=AUTH)
    assert filter_response.status_code == 422


def test_invalid_pagination_is_rejected(client: TestClient) -> None:
    cases = [
        "/api/v1/facts?limit=0",
        "/api/v1/facts?limit=-1",
        f"/api/v1/facts?limit={MAX_PAGE_LIMIT + 1}",
        "/api/v1/facts?offset=-1",
        "/api/v1/facts?limit=abc",
    ]
    for path in cases:
        response = client.get(path, headers=AUTH)
        assert response.status_code == 422, path
        assert _error(response)["code"] == "INVALID_PAGINATION"


def test_invalid_source_row_number_is_rejected(client: TestClient) -> None:
    response = client.get(
        f"/api/v1/batches/{BATCH_A}/staged-rows?source_row_number=0", headers=AUTH
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "VALIDATION_ERROR"


def test_default_and_explicit_pagination(client: TestClient) -> None:
    default = client.get("/api/v1/facts", headers=AUTH).json()
    assert default["pagination"]["limit"] == DEFAULT_PAGE_LIMIT
    assert default["pagination"]["offset"] == 0
    assert default["pagination"]["total"] == 3
    assert len(default["items"]) == 3

    page = client.get("/api/v1/facts?limit=1&offset=1", headers=AUTH).json()
    assert page["pagination"] == {"limit": 1, "offset": 1, "total": 3}
    assert len(page["items"]) == 1
    assert page["items"][0]["campaign_id"] == "camp-2"

    maximum = client.get(f"/api/v1/facts?limit={MAX_PAGE_LIMIT}", headers=AUTH)
    assert maximum.status_code == 200
    assert maximum.json()["pagination"]["limit"] == MAX_PAGE_LIMIT


def test_pagination_is_deterministically_ordered(client: TestClient) -> None:
    files = client.get("/api/v1/source-files", headers=AUTH).json()["items"]
    assert [item["source_file_id"] for item in files] == [FILE_A, FILE_B, FILE_C]
    facts = client.get("/api/v1/facts", headers=AUTH).json()["items"]
    assert [item["campaign_id"] for item in facts] == ["camp-1", "camp-2", "camp-3"]
    rows = client.get(f"/api/v1/batches/{BATCH_A}/staged-rows", headers=AUTH).json()["items"]
    assert [item["source_row_number"] for item in rows] == [2, 3, 4]


def test_empty_collection_is_paginated(client: TestClient) -> None:
    response = client.get("/api/v1/facts?campaign_id=no-such-campaign", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "pagination": {"limit": DEFAULT_PAGE_LIMIT, "offset": 0, "total": 0},
    }


def test_sql_style_query_params_are_ignored(client: TestClient) -> None:
    response = client.get(
        "/api/v1/facts?where=1=1&sql=select%201&filter_expression=true",
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 3


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def test_source_file_resource_omits_storage_uri(client: TestClient) -> None:
    listed = client.get("/api/v1/source-files", headers=AUTH)
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["source_file_id"] == FILE_A
    assert item["original_filename"] == "alpha.xlsx"
    assert item["sha256"] == "a" * 64
    assert "storage_uri" not in item
    assert SECRET_PATH not in listed.text
    assert "C:/Users/secret" not in listed.text

    single = client.get(f"/api/v1/source-files/{FILE_A}", headers=AUTH)
    assert single.status_code == 200
    assert single.json()["original_filename"] == "alpha.xlsx"
    assert "storage_uri" not in single.json()
    assert SECRET_PATH not in single.text


def test_batch_and_run_resources(client: TestClient) -> None:
    batch = client.get(f"/api/v1/batches/{BATCH_A}", headers=AUTH).json()
    assert batch["batch_id"] == BATCH_A
    assert batch["source_file_id"] == FILE_A
    assert batch["status"] == "processed"
    assert batch["worksheet_name"] == "Web-Engage Raw"
    assert batch["source_start_column"] == "K"
    assert batch["row_count_staged"] == 3
    assert batch["observed_day_min"] == "2025-08-01"

    failed = client.get(f"/api/v1/batches/{BATCH_B}", headers=AUTH).json()
    assert failed["status"] == "failed"
    assert failed["error_summary"] == "header contract failed"

    filtered = client.get(f"/api/v1/batches?source_file_id={FILE_B}&status=failed", headers=AUTH)
    assert [item["batch_id"] for item in filtered.json()["items"]] == [BATCH_B]

    run = client.get(f"/api/v1/processing-runs/{RUN_A}", headers=AUTH).json()
    assert run["processing_run_id"] == RUN_A
    assert run["status"] == "succeeded"
    assert run["engine_version"] == "0.4.0"
    assert run["campaign_label_version_id"] == "a0000000-0000-4000-8000-000000000022"
    assert run["qa_verdict"] == "pass"

    pending = client.get("/api/v1/processing-runs?status=pending", headers=AUTH).json()
    assert [item["processing_run_id"] for item in pending["items"]] == [RUN_C]


def test_staged_rows_preserve_null_and_empty_string(client: TestClient) -> None:
    rows = client.get(f"/api/v1/batches/{BATCH_A}/staged-rows", headers=AUTH).json()["items"]
    by_number = {row["source_row_number"]: row for row in rows}
    assert by_number[2]["raw"]["Campaign Name"] == " leading"
    assert by_number[3]["campaign_id"] == "camp-blank"
    assert by_number[3]["variation_id"] == ""
    assert by_number[4]["campaign_id"] is None
    assert by_number[4]["variation_id"] is None
    assert by_number[4]["raw"]["Campaign ID"] is None

    filtered = client.get(
        f"/api/v1/batches/{BATCH_A}/staged-rows?campaign_id=camp-blank&day=2025-08-02",
        headers=AUTH,
    ).json()
    assert [row["source_row_number"] for row in filtered["items"]] == [3]


def test_facts_and_history_preserve_contracts(client: TestClient) -> None:
    facts = client.get("/api/v1/facts", headers=AUTH).json()["items"]
    first = facts[0]
    assert first["campaign_name"] == " leading"
    assert first["filter_logic_1"] is None
    assert first["template_status"] == ""
    assert first["total_cost"] == "2.50"
    assert first["revenue_inr"] == "1234.56"
    assert isinstance(first["total_cost"], str)
    assert first["variation_id"] == "var-1"

    blank = next(item for item in facts if item["campaign_id"] == "camp-2")
    assert blank["variation_id"] is None
    assert blank["variation_id_key"] == ""
    assert blank["total_cost"] == "0"

    filtered = client.get(
        f"/api/v1/facts?batch_id={BATCH_A}&processing_run_id={RUN_A}&campaign_id=camp-1",
        headers=AUTH,
    ).json()
    assert len(filtered["items"]) == 1

    history = client.get("/api/v1/facts/history", headers=AUTH).json()
    assert history["pagination"]["total"] == 1
    item = history["items"][0]
    assert item["total_cost"] == "1.00"
    assert item["superseded_by_run_id"] == RUN_A
    assert item["campaign_name"] == " leading"
    assert item["filter_logic_1"] is None
    assert item["template_status"] == ""


def test_real_p4_fact_is_readable_through_the_api() -> None:
    raw: dict[str, Any] = dict.fromkeys(expected_source_headers())
    raw.update(
        {
            "Day": "2025-08-01",
            "Campaign ID": "camp-p4",
            "Variation ID": "var-p4",
            "Campaign Name": "TAMC_tplus90_UV_Mid/Prem_Push",
            "Channel": "WhatsApp",
            "Delivered": 100,
        }
    )
    staged = StagedRowRecord(
        id="stg-p4",
        batch_id=BATCH_A,
        source_row_number=10,
        raw=raw,
        campaign_id="camp-p4",
        variation_id="var-p4",
        day=date(2025, 8, 1),
    )
    outcome = transform_row(
        staged,
        ConfigBinder(),
        client_id=CLIENT_ID,
        batch_id=BATCH_A,
        processing_run_id=RUN_A,
    )
    assert outcome.fact is not None
    ingest, facts = seed_stores()
    facts.upsert(outcome.fact)
    app = create_app(settings=inspector_settings(), ingest_store=ingest, fact_store=facts)
    response = TestClient(app).get("/api/v1/facts?campaign_id=camp-p4", headers=AUTH)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["campaign_id"] == "camp-p4"
    assert item["label_match_status"] == "matched"
    assert item["total_cost"] == format(outcome.fact.total_cost, "f")
    assert item["campaign_name"] == "TAMC_tplus90_UV_Mid/Prem_Push"


def test_not_found_resources(client: TestClient) -> None:
    cases = [
        f"/api/v1/source-files/{MISSING_ID}",
        f"/api/v1/batches/{MISSING_ID}",
        f"/api/v1/batches/{MISSING_ID}/staged-rows",
        f"/api/v1/processing-runs/{MISSING_ID}",
    ]
    for path in cases:
        response = client.get(path, headers=AUTH)
        assert response.status_code == 404, path
        assert _error(response)["code"] == "NOT_FOUND"
        assert UUID(MISSING_ID)


# ---------------------------------------------------------------------------
# Security / errors
# ---------------------------------------------------------------------------


def test_error_and_success_bodies_do_not_leak_secrets(client: TestClient) -> None:
    responses = [
        client.get("/health"),
        client.get("/api/v1/source-files", headers=AUTH),
        client.get("/api/v1/session", headers={"Authorization": "Bearer wrong"}),
        client.get("/api/v1/not-a-resource", headers=AUTH),
        client.get("/openapi.json"),
    ]
    forbidden = (
        DEV_TOKEN,
        JWT_SECRET,
        SECRET_PATH,
        "traceback",
        "DATABASE_URL",
        "DFIP_AUTH_SECRET",
        "supabase_service_role_key",
        "postgresql://",
    )
    for response in responses:
        text = response.text
        for token in forbidden:
            assert token.lower() not in text.lower(), token


def test_unexpected_error_does_not_leak_internals() -> None:
    class BoomRepository:
        def list_source_files(self, **_kwargs):
            raise RuntimeError(
                r"C:\Users\secret\app.py DATABASE_URL=postgresql://x DFIP_AUTH_SECRET=abc"
            )

    app = create_app(
        settings=inspector_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        repository=BoomRepository(),  # type: ignore[arg-type]
    )
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/source-files", headers=AUTH
    )
    assert response.status_code == 500
    assert _error(response) == {
        "code": "INTERNAL_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "traceback" not in response.text.lower()
    assert "DATABASE_URL" not in response.text
    assert "secret" not in response.text.lower()


def test_persistence_unavailable_is_safe() -> None:
    class DownRepository:
        def list_facts(self, **_kwargs):
            raise PersistenceUnavailableError()

    app = create_app(
        settings=inspector_settings(),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
        repository=DownRepository(),  # type: ignore[arg-type]
    )
    response = TestClient(app).get("/api/v1/facts", headers=AUTH)
    assert response.status_code == 503
    assert _error(response)["code"] == "PERSISTENCE_UNAVAILABLE"
    assert "postgresql" not in response.text.lower()


def test_application_imports_and_module_app_serves_health(monkeypatch) -> None:
    import dfip_api
    from dfip_config.settings import load_settings

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DFIP_DEV_AUTH_CLIENT_ID", "")
    load_settings.cache_clear()
    from dfip_api.app import app

    assert dfip_api.__version__ == "0.5.0"
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["database"]["status"] == "not_checked"
    load_settings.cache_clear()


# ---------------------------------------------------------------------------
# Runtime bind
# ---------------------------------------------------------------------------


def test_uvicorn_serves_health_without_database() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    pythonpath = os.pathsep.join(
        [
            str(ROOT / "packages" / "api"),
            str(ROOT / "packages" / "config"),
            str(ROOT / "packages" / "core"),
            str(ROOT / "packages" / "db"),
            str(ROOT / "packages" / "shared"),
            str(ROOT / "packages" / "analytics"),
            os.environ.get("PYTHONPATH", ""),
        ]
    )
    env = {
        **os.environ,
        "DFIP_ENV": "test",
        "DFIP_AUTH_MODE": "dev_token",
        "DFIP_DEV_AUTH_TOKEN": DEV_TOKEN,
        "DATABASE_URL": "",
        "PYTHONPATH": pythonpath,
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dfip_api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}/health"
    try:
        body = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(url, timeout=1) as handle:
                    body = json.loads(handle.read().decode())
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.1)
        assert body is not None, "uvicorn did not serve /health"
        assert body["status"] == "ok"
        assert body["database"]["status"] == "not_checked"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_p5_does_not_introduce_p6_plus_scope() -> None:
    api_root = ROOT / "packages" / "api" / "dfip_api"
    forbidden = (
        "dashboard",
        "login page",
        "login.html",
        "power query",
        "row level security",
        "enable row level security",
        "kpi engine",
        "qa engine",
        "excel export",
        "add-in",
    )
    for path in api_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in text, f"{path.name} references {token!r}"
    assert not (ROOT / "apps" / "web" / "src").exists()
    assert not list((ROOT / "apps" / "web").glob("*.tsx"))
    assert not list((ROOT / "apps" / "web").glob("*.html"))
