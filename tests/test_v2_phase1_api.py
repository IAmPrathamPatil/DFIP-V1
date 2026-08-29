"""PostgreSQL API regression: 401/403/422, working set vs published, membership."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jwt
from dfip_api.app import create_app
from fastapi.testclient import TestClient

from postgres_support import (
    CLIENT_A,
    CLIENT_B,
    RUN_A,
    postgres_only,
    requires_postgres,
    sample_fact,
    seed_identity,
    seed_working_set,
)
from test_p5_api import JWT_SECRET, make_settings

pytestmark = [postgres_only, requires_postgres]


def _jwt(*, role: str, sub: str, client_id: str | None = None) -> dict[str, str]:
    payload = {
        "sub": sub,
        "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        "role": role,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _app(postgres_url: str) -> TestClient:
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_env="test",
        )
    )
    return TestClient(app)


def test_unauthenticated_is_401(pg_conn, postgres_url) -> None:
    seed_working_set(pg_conn)
    client = _app(postgres_url)
    response = client.get("/api/v1/facts")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"


def test_publisher_working_set_and_reader_published(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-1", role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject="reader-1", role="reader", client_id=CLIENT_A)
    facts.upsert(sample_fact())
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="publisher-1",
        notes=None,
    )
    client = _app(postgres_url)
    publisher = _jwt(role="publisher", sub="publisher-1", client_id=CLIENT_A)
    reader = _jwt(role="reader", sub="reader-1", client_id=CLIENT_A)
    working = client.get("/api/v1/facts", headers=publisher)
    assert working.status_code == 200
    assert working.json()["pagination"]["total"] >= 1
    denied = client.get("/api/v1/facts", headers=reader)
    assert denied.status_code == 403
    published = client.get(
        "/api/v1/publications/current/facts",
        headers=reader,
        params={"client_id": CLIENT_A},
    )
    assert published.status_code == 200
    assert published.json()["pagination"]["total"] >= 1
    current = client.get(
        "/api/v1/publications/current",
        headers=reader,
        params={"client_id": CLIENT_A},
    )
    assert current.status_code == 200
    assert current.json()["publication"] is not None


def test_cross_client_and_invalid_pagination(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-a", role="publisher", client_id=CLIENT_A)
    facts.upsert(sample_fact())
    client = _app(postgres_url)
    headers = _jwt(role="publisher", sub="publisher-a", client_id=CLIENT_A)
    cross = client.get("/api/v1/facts", headers=headers, params={"client_id": CLIENT_B})
    assert cross.status_code in {200, 403}
    if cross.status_code == 200:
        assert cross.json()["pagination"]["total"] == 0
    page = client.get("/api/v1/facts", headers=headers, params={"limit": 201})
    assert page.status_code == 422
    assert page.json()["error"]["code"] == "INVALID_PAGINATION"
    jwt_cross = client.get(
        "/api/v1/publications/current/facts",
        headers=_jwt(role="publisher", sub="publisher-a", client_id=CLIENT_A),
        params={"client_id": CLIENT_B},
    )
    assert jwt_cross.status_code == 403


def test_jwt_without_membership_is_403(pg_conn, postgres_url) -> None:
    seed_working_set(pg_conn)
    client = _app(postgres_url)
    response = client.get(
        "/api/v1/session",
        headers=_jwt(role="publisher", sub="missing-user", client_id=CLIENT_A),
    )
    assert response.status_code == 403


def test_restart_persistence(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-1", role="publisher", client_id=CLIENT_A)
    facts.upsert(sample_fact())
    first = _app(postgres_url)
    headers = _jwt(role="publisher", sub="publisher-1", client_id=CLIENT_A)
    before = first.get("/api/v1/facts", headers=headers).json()["pagination"]["total"]
    first.close()
    second = _app(postgres_url)
    after = second.get("/api/v1/facts", headers=headers).json()["pagination"]["total"]
    second.close()
    assert before == after
    assert after >= 1


def test_health_postgres_mode_does_not_probe(pg_conn, postgres_url) -> None:
    del pg_conn
    client = _app(postgres_url)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["configured"] is True
    assert payload["database"]["status"] == "not_checked"


def test_facts_decimal_and_restart_entities(pg_conn, pg_stores, postgres_url) -> None:
    ingest, facts, pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-1", role="publisher", client_id=CLIENT_A)
    facts.upsert(sample_fact(total_cost=Decimal("2.50")))
    facts.upsert(sample_fact(total_cost=Decimal("3.00"), sent=2))
    pubs.create(
        client_id=CLIENT_A,
        processing_run_id=RUN_A,
        period_start=None,
        period_end=None,
        published_by="publisher-1",
        notes=None,
    )
    first = _app(postgres_url)
    headers = _jwt(role="publisher", sub="publisher-1", client_id=CLIENT_A)
    working = first.get("/api/v1/facts", headers=headers)
    assert working.status_code == 200
    item = working.json()["items"][0]
    assert isinstance(item["total_cost"], str)
    assert Decimal(item["total_cost"]) == Decimal("3")
    history = first.get("/api/v1/facts/history", headers=headers)
    assert history.status_code == 200
    assert history.json()["pagination"]["total"] >= 1
    current = first.get(
        "/api/v1/publications/current",
        headers=headers,
        params={"client_id": CLIENT_A},
    )
    assert current.json()["publication"] is not None
    first.close()
    second = _app(postgres_url)
    after_facts = second.get("/api/v1/facts", headers=headers)
    after_current = second.get(
        "/api/v1/publications/current",
        headers=headers,
        params={"client_id": CLIENT_A},
    )
    after_history = second.get("/api/v1/facts/history", headers=headers)
    run = ingest.get_processing_run(RUN_A)
    second.close()
    assert after_facts.json()["pagination"]["total"] >= 1
    assert after_current.json()["publication"]["processing_run_id"] == RUN_A
    assert after_history.json()["pagination"]["total"] >= 1
    assert run is not None
    assert run.client_id == CLIENT_A


def test_jwt_mismatch_is_403_run_mismatch_is_422(pg_conn, pg_stores, postgres_url) -> None:
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-a", role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject="publisher-b", role="publisher", client_id=CLIENT_B)
    facts.upsert(sample_fact())
    client = _app(postgres_url)
    jwt_mismatch = client.post(
        "/api/v1/publications",
        headers=_jwt(role="publisher", sub="publisher-a", client_id=CLIENT_A),
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    assert jwt_mismatch.status_code == 403
    integrity = client.post(
        "/api/v1/publications",
        headers=_jwt(role="publisher", sub="publisher-b"),
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    assert integrity.status_code == 422
    assert integrity.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "does not belong" in integrity.json()["error"]["message"]
