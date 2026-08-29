"""P9 application-level authorization.

This is not PostgreSQL RLS, not Supabase Auth, and not a production identity
provider. GET /facts remains the working-set API for admin/publisher.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from dfip_api.app import create_app
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_api.roles import ALLOWED_ROLES, can_inspect, can_publish
from dfip_api.schemas import MAX_PAGE_LIMIT
from dfip_core.transform.fact import FactRecord
from fastapi.testclient import TestClient

from test_p5_api import (
    AUTH,
    BATCH_A,
    CLIENT_ID,
    FILE_A,
    JWT_SECRET,
    RUN_A,
    RUN_C,
    _encode_jwt,
    inspector_settings,
    make_settings,
    seed_stores,
)
from test_p7_publication import _publish

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"
CLIENT_B = "a0000000-0000-4000-8000-000000000002"
FORBIDDEN_BODY = "Not authorized to access this resource."
INSPECTOR_PATHS = (
    "/api/v1/facts",
    "/api/v1/facts/history",
    "/api/v1/source-files",
    f"/api/v1/source-files/{FILE_A}",
    "/api/v1/batches",
    f"/api/v1/batches/{BATCH_A}",
    f"/api/v1/batches/{BATCH_A}/staged-rows",
    "/api/v1/processing-runs",
    f"/api/v1/processing-runs/{RUN_A}",
    f"/api/v1/processing-runs/{RUN_A}/qa-findings",
)


def _error(response) -> dict:
    payload = response.json()
    assert "error" in payload
    assert "code" in payload["error"]
    assert "message" in payload["error"]
    return payload["error"]


def _assert_forbidden(response) -> None:
    assert response.status_code == 403
    assert _error(response) == {
        "code": "AUTHORIZATION_FAILED",
        "message": FORBIDDEN_BODY,
    }
    body = response.json()
    assert "items" not in body
    assert "pagination" not in body
    assert "alpha.xlsx" not in response.text
    assert "camp-1" not in response.text
    assert "stg_source_row" not in response.text
    assert "Campaign Name" not in response.text
    assert JWT_SECRET not in response.text


def jwt_headers(role: str, client_id: str | None = CLIENT_ID) -> dict[str, str]:
    token = _encode_jwt(role=role, client_id=client_id)
    return {"Authorization": f"Bearer {token}"}


def jwt_app(
    *,
    ingest=None,
    facts=None,
    publication_store=None,
):
    resolved_ingest, resolved_facts = (ingest, facts) if ingest else seed_stores()
    store = publication_store or InMemoryPublicationStore()
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET),
        ingest_store=resolved_ingest,
        fact_store=resolved_facts,
        publication_store=store,
    )
    return TestClient(app), resolved_ingest, resolved_facts, store


def publisher_http():
    ingest, facts = seed_stores()
    store = InMemoryPublicationStore()
    app = create_app(
        settings=inspector_settings(),
        ingest_store=ingest,
        fact_store=facts,
        publication_store=store,
    )
    return TestClient(app), ingest, facts, store


def test_allowlist_is_exactly_the_four_locked_roles() -> None:
    assert ALLOWED_ROLES == frozenset({"admin", "publisher", "reader", "client"})
    assert can_inspect("admin")
    assert can_inspect("publisher")
    assert not can_inspect("reader")
    assert not can_inspect("client")
    assert can_publish("admin")
    assert can_publish("publisher")
    assert not can_publish("reader")
    assert not can_publish("client")


@pytest.mark.parametrize("role", ["client", "reader"])
@pytest.mark.parametrize("path", INSPECTOR_PATHS)
def test_client_and_reader_jwt_cannot_inspect_working_set(role: str, path: str) -> None:
    http, *_rest = jwt_app()
    _assert_forbidden(http.get(path, headers=jwt_headers(role)))


@pytest.mark.parametrize("role", ["client", "reader"])
def test_client_and_reader_may_read_session(role: str) -> None:
    http, *_rest = jwt_app()
    response = http.get("/api/v1/session", headers=jwt_headers(role))
    assert response.status_code == 200
    assert response.json()["role"] == role


@pytest.mark.parametrize("role", ["client", "reader"])
def test_client_and_reader_may_read_current_publication_and_facts(role: str) -> None:
    publisher, ingest, facts, store = publisher_http()
    created = _publish(publisher)
    assert created.status_code == 201
    http, *_rest = jwt_app(ingest=ingest, facts=facts, publication_store=store)
    headers = jwt_headers(role, CLIENT_ID)
    current = http.get("/api/v1/publications/current", headers=headers)
    published = http.get("/api/v1/publications/current/facts", headers=headers)
    assert current.status_code == 200
    assert current.json()["publication"]["processing_run_id"] == RUN_A
    assert published.status_code == 200
    assert published.json()["pagination"]["total"] == 3


@pytest.mark.parametrize("role", ["client", "reader"])
def test_client_and_reader_cannot_publish(role: str) -> None:
    http, *_rest = jwt_app()
    response = http.post(
        "/api/v1/publications",
        headers=jwt_headers(role),
        json={"client_id": CLIENT_ID, "processing_run_id": RUN_A},
    )
    assert response.status_code == 403
    assert _error(response)["code"] == "AUTHORIZATION_FAILED"


@pytest.mark.parametrize("role", ["admin", "publisher"])
def test_admin_and_publisher_jwt_can_read_working_set(role: str) -> None:
    http, *_rest = jwt_app()
    response = http.get("/api/v1/facts", headers=jwt_headers(role))
    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 3
    assert {item["campaign_id"] for item in response.json()["items"]} == {
        "camp-1",
        "camp-2",
        "camp-3",
    }


def test_publisher_working_set_total_is_unchanged_by_publication() -> None:
    http, ingest, facts, store = publisher_http()
    unpublished = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-unpublished",
        variation_id="var-z",
        variation_id_key="var-z",
        day=date(2025, 8, 4),
        processing_run_id=RUN_C,
        total_cost=None,
        template_status="",
    )
    facts.facts[unpublished.key] = unpublished
    before = http.get("/api/v1/facts", headers=AUTH).json()
    created = _publish(http)
    assert created.status_code == 201
    after = http.get("/api/v1/facts", headers=AUTH).json()
    assert before["pagination"]["total"] == 4
    assert after["pagination"]["total"] == 4
    assert {item["campaign_id"] for item in after["items"]} == {
        "camp-1",
        "camp-2",
        "camp-3",
        "camp-unpublished",
    }
    published = http.get(
        "/api/v1/publications/current/facts",
        headers=AUTH,
        params={"client_id": CLIENT_ID},
    ).json()
    assert published["pagination"]["total"] == 3
    assert "camp-unpublished" not in {item["campaign_id"] for item in published["items"]}


@pytest.mark.parametrize("role", ["admin", "publisher"])
def test_inspector_roles_can_read_staging_source_and_runs(role: str) -> None:
    http, *_rest = jwt_app()
    headers = jwt_headers(role)
    source = http.get("/api/v1/source-files", headers=headers)
    one_file = http.get(f"/api/v1/source-files/{FILE_A}", headers=headers)
    batches = http.get("/api/v1/batches", headers=headers)
    one_batch = http.get(f"/api/v1/batches/{BATCH_A}", headers=headers)
    rows = http.get(f"/api/v1/batches/{BATCH_A}/staged-rows", headers=headers)
    runs = http.get("/api/v1/processing-runs", headers=headers)
    one_run = http.get(f"/api/v1/processing-runs/{RUN_A}", headers=headers)
    history = http.get("/api/v1/facts/history", headers=headers)
    assert source.status_code == 200
    assert source.json()["pagination"]["total"] == 3
    assert one_file.status_code == 200
    assert one_file.json()["original_filename"] == "alpha.xlsx"
    assert "storage_uri" not in one_file.json()
    assert batches.status_code == 200
    assert one_batch.status_code == 200
    assert rows.status_code == 200
    assert rows.json()["pagination"]["total"] == 3
    assert runs.status_code == 200
    assert one_run.status_code == 200
    assert one_run.json()["status"] == "succeeded"
    assert history.status_code == 200
    assert history.json()["pagination"]["total"] == 1


def test_unknown_jwt_role_is_rejected() -> None:
    http, *_rest = jwt_app()
    headers = jwt_headers("superadmin")
    session = http.get("/api/v1/session", headers=headers)
    facts = http.get("/api/v1/facts", headers=headers)
    assert session.status_code == 401
    assert _error(session)["code"] == "AUTHENTICATION_FAILED"
    assert facts.status_code == 401
    assert _error(facts)["code"] == "AUTHENTICATION_FAILED"
    assert "superadmin" not in session.text
    assert JWT_SECRET not in session.text


def test_unauthenticated_protected_route_is_401() -> None:
    http, *_rest = publisher_http()
    response = http.get("/api/v1/facts")
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTHENTICATION_FAILED"


def test_max_page_limit_remains_200() -> None:
    assert MAX_PAGE_LIMIT == 200
    http, *_rest = publisher_http()
    response = http.get(f"/api/v1/facts?limit={MAX_PAGE_LIMIT}", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["pagination"]["limit"] == 200
    assert response.json()["pagination"]["total"] == 3


def test_published_facts_pagination_still_works() -> None:
    publisher, ingest, facts, store = publisher_http()
    created = _publish(publisher)
    assert created.status_code == 201
    http, *_rest = jwt_app(ingest=ingest, facts=facts, publication_store=store)
    headers = jwt_headers("client", CLIENT_ID)
    page = http.get(
        "/api/v1/publications/current/facts",
        headers=headers,
        params={"limit": 1, "offset": 1},
    )
    assert page.status_code == 200
    body = page.json()
    assert body["pagination"] == {"limit": 1, "offset": 1, "total": 3}
    assert len(body["items"]) == 1
    maximum = http.get(
        "/api/v1/publications/current/facts",
        headers=headers,
        params={"limit": MAX_PAGE_LIMIT},
    )
    assert maximum.status_code == 200
    assert maximum.json()["pagination"]["limit"] == 200
    assert maximum.json()["pagination"]["total"] == 3


def test_jwt_client_id_publication_scoping_remains_intact() -> None:
    publisher, ingest, facts, store = publisher_http()
    created = _publish(publisher)
    assert created.status_code == 201
    http, *_rest = jwt_app(ingest=ingest, facts=facts, publication_store=store)
    own = http.get(
        "/api/v1/publications/current/facts",
        headers=jwt_headers("client", CLIENT_ID),
    )
    assert own.status_code == 200
    assert own.json()["pagination"]["total"] == 3
    other = http.get(
        "/api/v1/publications/current/facts",
        headers=jwt_headers("client", CLIENT_B),
    )
    assert other.status_code == 200
    assert other.json()["items"] == []
    denied = http.get(
        "/api/v1/publications/current/facts",
        headers=jwt_headers("client", CLIENT_B),
        params={"client_id": CLIENT_ID},
    )
    assert denied.status_code == 403
    assert _error(denied)["code"] == "AUTHORIZATION_FAILED"
    current_denied = http.get(
        "/api/v1/publications/current",
        headers=jwt_headers("client", CLIENT_B),
        params={"client_id": CLIENT_ID},
    )
    assert current_denied.status_code == 403


def test_client_cannot_retrieve_unpublished_or_staging_payloads() -> None:
    publisher, ingest, facts, store = publisher_http()
    unpublished = FactRecord(
        client_id=CLIENT_ID,
        campaign_id="camp-unpublished",
        variation_id="var-z",
        variation_id_key="var-z",
        day=date(2025, 8, 4),
        processing_run_id=RUN_C,
        total_cost=None,
        template_status="",
    )
    facts.facts[unpublished.key] = unpublished
    created = _publish(publisher)
    assert created.status_code == 201
    http, *_rest = jwt_app(ingest=ingest, facts=facts, publication_store=store)
    headers = jwt_headers("client", CLIENT_ID)
    for path in INSPECTOR_PATHS:
        response = http.get(path, headers=headers)
        _assert_forbidden(response)
        assert "camp-unpublished" not in response.text
        assert "alpha.xlsx" not in response.text
        assert FILE_A not in response.text
    published = http.get("/api/v1/publications/current/facts", headers=headers)
    assert published.status_code == 200
    ids = {item["campaign_id"] for item in published.json()["items"]}
    assert "camp-unpublished" not in ids


def test_frontend_admin_uses_working_set_and_client_uses_published() -> None:
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    admin_list = app_js[
        app_js.index('if (name === "admin-facts")') : app_js.index(
            'if (name === "admin-fact-detail")'
        )
    ]
    assert "listFacts" in admin_list
    assert "listPublishedFacts" not in admin_list
    client_list = app_js[
        app_js.index('if (name === "client-facts")') : app_js.index(
            'if (name === "client-fact-detail")'
        )
    ]
    assert "listPublishedFacts" in client_list
    assert "listFacts" not in client_list
    client_detail = app_js[
        app_js.index('if (name === "client-fact-detail")') : app_js.index("return notFoundView();")
    ]
    assert "loadPublishedFact" in client_detail
    assert "loadFact(" not in client_detail
    assert "/publications/current/facts" in client_js
    assert "listPublishedFacts" in client_js
