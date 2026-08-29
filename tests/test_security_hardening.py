"""Security hardening: scoped development tokens, OpenAPI, inspector isolation.

Does not open PostgreSQL. Does not print credentials or connection strings.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from dfip_api.app import create_app
from dfip_api.auth import Principal, authenticate_bearer
from dfip_api.errors import AuthConfigurationError
from dfip_api.membership import rls_context_for
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_core.ingest.store import (
    BatchRecord,
    InMemoryIngestStore,
    ProcessingRunRecord,
    SourceFileRecord,
)
from dfip_core.transform.fact import FactRecord
from dfip_core.transform.store import InMemoryFactStore
from fastapi.testclient import TestClient

from test_p5_api import (
    AUTH,
    CLIENT_ID,
    DEV_TOKEN,
    JWT_SECRET,
    _encode_jwt,
    _ts,
    inspector_settings,
    make_settings,
    seed_stores,
)
from test_p7_publication import _publish
from test_p9_authz import _error, jwt_app, jwt_headers

CLIENT_B = "a0000000-0000-4000-8000-000000000002"
FILE_B = "b0000000-0000-4000-8000-000000000099"
BATCH_B_ID = "c0000000-0000-4000-8000-000000000099"
RUN_B_ID = "d0000000-0000-4000-8000-000000000099"
PLACEHOLDER_DSN = "postgresql://postgres@127.0.0.1:1/dfip"


def _seed_other_client(ingest, facts) -> None:
    record = SourceFileRecord(
        id=FILE_B,
        client_id=CLIENT_B,
        sha256="f" * 64,
        original_filename="other-client.xlsx",
        byte_size=11,
        source_kind="native_export",
        uploaded_at=_ts(8),
    )
    ingest.source_files[record.sha256] = record
    ingest.source_files_by_id[record.id] = record
    ingest.batches[BATCH_B_ID] = BatchRecord(
        id=BATCH_B_ID,
        source_file_id=FILE_B,
        client_id=CLIENT_B,
        status="processed",
        row_count_declared=1,
        row_count_staged=1,
        row_count_rejected=0,
        observed_day_min=date(2025, 8, 1),
        observed_day_max=date(2025, 8, 1),
        created_at=_ts(8),
        completed_at=_ts(8),
        error_summary=None,
        worksheet_name="Web-Engage Raw",
        header_row=1,
        source_start_column="K",
        empty_row_count=0,
    )
    ingest.processing_runs[RUN_B_ID] = ProcessingRunRecord(
        id=RUN_B_ID,
        batch_id=BATCH_B_ID,
        campaign_label_version_id=None,
        template_label_version_id=None,
        rate_card_version_id=None,
        label_group_version_id=None,
        engine_version="0.4.0",
        started_at=_ts(8),
        finished_at=_ts(8),
        status="succeeded",
        qa_verdict=None,
        client_id=CLIENT_B,
    )
    other = FactRecord(
        client_id=CLIENT_B,
        campaign_id="camp-other",
        variation_id="var-b",
        variation_id_key="var-b",
        day=date(2025, 8, 1),
        processing_run_id=RUN_B_ID,
        batch_id=BATCH_B_ID,
        total_cost=Decimal("9.00"),
        template_status="",
        first_seen_at=_ts(8),
        last_seen_at=_ts(8),
    )
    facts.facts[other.key] = other


def _scoped_publisher_app():
    ingest, facts = seed_stores()
    _seed_other_client(ingest, facts)
    store = InMemoryPublicationStore()
    app = create_app(
        settings=inspector_settings(dfip_dev_auth_client_id=CLIENT_ID),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    return TestClient(app), ingest, facts, store


def test_dev_token_with_database_url_requires_client_id() -> None:
    with pytest.raises(AuthConfigurationError, match="DFIP_DEV_AUTH_CLIENT_ID"):
        create_app(
            settings=make_settings(database_url=PLACEHOLDER_DSN),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_dev_token_client_id_must_be_uuid() -> None:
    with pytest.raises(AuthConfigurationError, match="UUID"):
        create_app(
            settings=make_settings(dfip_dev_auth_client_id="not-a-uuid"),
            ingest_store=InMemoryIngestStore(),
            fact_store=InMemoryFactStore(),
        )


def test_dev_token_rls_context_is_not_platform_admin() -> None:
    settings = make_settings(
        dfip_dev_auth_role="publisher",
        dfip_dev_auth_client_id=CLIENT_ID,
        database_url=PLACEHOLDER_DSN,
    )
    principal = authenticate_bearer(settings, f"Bearer {DEV_TOKEN}")
    assert principal.auth_mode == "dev_token"
    assert principal.client_id == CLIENT_ID
    assert principal.platform_admin is False
    assert principal.membership_client_ids == (CLIENT_ID,)
    context = rls_context_for(principal, db_mode=True)
    assert context is not None
    assert context.platform_admin is False
    assert context.client_ids == (CLIENT_ID,)
    assert rls_context_for(principal, db_mode=False) is None


def test_jwt_platform_admin_rls_flag_is_unchanged() -> None:
    principal = Principal(
        subject="admin-1",
        auth_mode="jwt",
        role="admin",
        client_id=None,
        platform_admin=True,
        membership_client_ids=None,
    )
    context = rls_context_for(principal, db_mode=True)
    assert context is not None
    assert context.platform_admin is True
    assert context.client_ids == ()


def test_scoped_dev_token_cannot_target_another_client() -> None:
    http, *_rest = _scoped_publisher_app()
    session = http.get("/api/v1/session", headers=AUTH)
    assert session.status_code == 200
    assert session.json()["client_id"] == CLIENT_ID
    own = http.get("/api/v1/facts", headers=AUTH)
    assert own.status_code == 200
    assert own.json()["pagination"]["total"] == 3
    assert "camp-other" not in {item["campaign_id"] for item in own.json()["items"]}
    denied = http.get("/api/v1/facts", headers=AUTH, params={"client_id": CLIENT_B})
    assert denied.status_code == 403
    assert _error(denied)["code"] == "AUTHORIZATION_FAILED"
    assert "camp-other" not in denied.text
    published = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_B},
    )
    assert published.status_code == 403
    hidden = http.get(f"/api/v1/source-files/{FILE_B}", headers=AUTH)
    assert hidden.status_code == 404
    other_run = http.get(f"/api/v1/processing-runs/{RUN_B_ID}", headers=AUTH)
    assert other_run.status_code == 404


def test_scoped_dev_token_publish_and_excel_path_stay_on_bound_client() -> None:
    http, *_rest = _scoped_publisher_app()
    created = _publish(http)
    assert created.status_code == 201
    current = http.get("/api/v1/publications/current/facts", headers=AUTH)
    assert current.status_code == 200
    assert current.json()["pagination"]["total"] == 3
    matching = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    )
    assert matching.status_code == 200
    assert matching.json()["pagination"]["total"] == 3
    csv_denied = http.get(
        "/api/v1/publications/current/facts.csv",
        headers=AUTH,
        params={"client_id": CLIENT_B},
    )
    assert csv_denied.status_code == 403


def test_production_disables_openapi_surface() -> None:
    app = create_app(
        settings=make_settings(
            dfip_env="production",
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url=PLACEHOLDER_DSN,
        ),
        ingest_store=InMemoryIngestStore(),
        fact_store=InMemoryFactStore(),
    )
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert "paths" not in client.get("/openapi.json").text


def test_development_openapi_surface_remains() -> None:
    ingest, facts = seed_stores()
    client = TestClient(
        create_app(settings=inspector_settings(), ingest_store=ingest, fact_store=facts)
    )
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert "/api/v1/facts" in spec.json()["paths"]
    assert client.get("/docs").status_code == 200


def test_jwt_inspector_routes_respect_client_scope() -> None:
    ingest, facts = seed_stores()
    _seed_other_client(ingest, facts)
    http, *_rest = jwt_app(ingest=ingest, facts=facts)
    headers_a = jwt_headers("publisher", CLIENT_ID)
    headers_b = jwt_headers("publisher", CLIENT_B)
    own = http.get("/api/v1/facts", headers=headers_a)
    assert own.status_code == 200
    assert own.json()["pagination"]["total"] == 3
    assert "camp-other" not in {item["campaign_id"] for item in own.json()["items"]}
    mismatch = http.get("/api/v1/facts", headers=headers_a, params={"client_id": CLIENT_B})
    assert mismatch.status_code == 403
    assert _error(mismatch)["code"] == "AUTHORIZATION_FAILED"
    other = http.get("/api/v1/facts", headers=headers_b)
    assert other.status_code == 200
    assert other.json()["pagination"]["total"] == 1
    assert other.json()["items"][0]["campaign_id"] == "camp-other"
    files_a = http.get("/api/v1/source-files", headers=headers_a)
    assert files_a.status_code == 200
    assert files_a.json()["pagination"]["total"] == 3
    assert http.get(f"/api/v1/source-files/{FILE_B}", headers=headers_a).status_code == 404
    assert http.get(f"/api/v1/source-files/{FILE_B}", headers=headers_b).status_code == 200
    history = http.get("/api/v1/facts/history", headers=headers_b)
    assert history.status_code == 200
    assert history.json()["pagination"]["total"] == 0
    runs_a = http.get("/api/v1/processing-runs", headers=headers_a)
    assert runs_a.status_code == 200
    assert RUN_B_ID not in {item["processing_run_id"] for item in runs_a.json()["items"]}
    assert http.get(f"/api/v1/processing-runs/{RUN_B_ID}", headers=headers_a).status_code == 404
    staged = http.get(f"/api/v1/batches/{BATCH_B_ID}/staged-rows", headers=headers_a)
    assert staged.status_code == 404


def test_jwt_without_client_id_still_inspects_as_publisher() -> None:
    http, *_rest = jwt_app()
    token = _encode_jwt(role="publisher")
    headers = {"Authorization": f"Bearer {token}"}
    response = http.get("/api/v1/facts", headers=headers)
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 3
