"""PostgreSQL API regression: 401/403/422, working set vs published, membership."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
from publisher_session_support import with_publisher_session
from test_p5_api import JWT_SECRET, make_settings

pytestmark = [postgres_only, requires_postgres]

# Local CLIENT_B working set. ``postgres_support.seed_working_set`` only seeds
# CLIENT_A, and this file must not change shared seed helpers for one contract
# test. ``pg_conn`` re-inserts the CLIENT_B company row before every test.
FILE_B = "b0000000-0000-4000-8000-000000000002"
BATCH_B = "c0000000-0000-4000-8000-000000000002"
RUN_B = "d0000000-0000-4000-8000-000000000002"


def _seed_client_b_failed_run(conn) -> None:
    """A CLIENT_B processing run in a non-succeeded state."""
    ts = datetime(2025, 8, 1, 1, tzinfo=UTC)
    conn.execute(
        """
        INSERT INTO source_file (
            id, client_id, sha256, original_filename, byte_size, source_kind, uploaded_at
        )
        VALUES (%s, %s, %s, 'beta.xlsx', 10, 'native_export', %s)
        """,
        (FILE_B, CLIENT_B, "b" * 64, ts),
    )
    conn.execute(
        """
        INSERT INTO batch (
            id, source_file_id, client_id, status, row_count_declared, row_count_staged,
            row_count_rejected, observed_day_min, observed_day_max, created_at, completed_at,
            worksheet_name, header_row, source_start_column, empty_row_count
        )
        VALUES (
            %s, %s, %s, 'failed', 3, 0, 3, %s, %s, %s, %s,
            'Web-Engage Raw', 1, 'K', 0
        )
        """,
        (BATCH_B, FILE_B, CLIENT_B, date(2025, 8, 1), date(2025, 8, 3), ts, ts),
    )
    conn.execute(
        """
        INSERT INTO processing_run (
            id, batch_id, client_id, engine_version, started_at, finished_at, status
        )
        VALUES (%s, %s, %s, '0.4.0', %s, %s, 'failed')
        """,
        (RUN_B, BATCH_B, CLIENT_B, ts, ts),
    )
    conn.commit()


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


def _pub(http: TestClient, headers: dict[str, str]) -> dict[str, str]:
    """Attach this caller's active Publisher session to publisher headers."""
    token = headers["Authorization"].split(" ", 1)[1]
    return with_publisher_session(headers, http, token)


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
    publisher = _pub(client, _jwt(role="publisher", sub="publisher-1", client_id=CLIENT_A))
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
    headers = _pub(client, _jwt(role="publisher", sub="publisher-a", client_id=CLIENT_A))
    cross = client.get("/api/v1/facts", headers=headers, params={"client_id": CLIENT_B})
    assert cross.status_code in {200, 403}
    if cross.status_code == 200:
        assert cross.json()["pagination"]["total"] == 0
    page = client.get("/api/v1/facts", headers=headers, params={"limit": 201})
    assert page.status_code == 422
    assert page.json()["error"]["code"] == "INVALID_PAGINATION"
    jwt_cross = client.get(
        "/api/v1/publications/current/facts",
        headers=_pub(client, _jwt(role="publisher", sub="publisher-a", client_id=CLIENT_A)),
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
    headers = _pub(first, _jwt(role="publisher", sub="publisher-1", client_id=CLIENT_A))
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
    headers = _pub(first, _jwt(role="publisher", sub="publisher-1", client_id=CLIENT_A))
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


def test_jwt_mismatch_is_403_and_cross_tenant_run_is_404(pg_conn, pg_stores, postgres_url) -> None:
    """A conflicting JWT client_id is 403; another tenant's run is 404.

    Two different contracts, deliberately in one test. The 403 is the
    caller's own token contradicting their own request body, so it is
    decided before any resource is read.

    The 404 is the tenant boundary. ``get_principal`` binds the request RLS
    context, so ``processing_run`` for CLIENT_A is filtered out of the
    lookup for a publisher whose membership is CLIENT_B, and
    ``PublicationService.create`` never reaches its
    "Processing run does not belong to this client." branch. 404 rather
    than 422 is required: 422 would be distinguishable from 404 for a run
    that does not exist at all, which is an existence oracle for other
    tenants' processing_run ids. The 422 branch is still exercised for a
    principal authorized for both tenants by
    ``test_platform_admin_cross_tenant_run_is_422``.
    """
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-a", role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject="publisher-b", role="publisher", client_id=CLIENT_B)
    facts.upsert(sample_fact())
    client = _app(postgres_url)
    jwt_mismatch = client.post(
        "/api/v1/publications",
        headers=_pub(client, _jwt(role="publisher", sub="publisher-a", client_id=CLIENT_A)),
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    assert jwt_mismatch.status_code == 403
    cross_tenant = client.post(
        "/api/v1/publications",
        headers=_pub(client, _jwt(role="publisher", sub="publisher-b")),
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "NOT_FOUND"


def test_platform_admin_cross_tenant_run_is_422(pg_conn, pg_stores, postgres_url) -> None:
    """The documented run-mismatch 422 stays reachable, for platform admins.

    ``platform_admin`` comes from ``app_user.is_platform_admin``, never from a
    JWT claim, and the RLS policies honor ``dfip_is_platform_admin()``. Such a
    caller is authorized for every tenant by design, so naming the mismatch
    discloses nothing they could not already read. This is the only principal
    for which the "does not belong to this client" branch is reachable under
    real tenant filtering.
    """
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-pa", role="publisher", platform_admin=True)
    facts.upsert(sample_fact())
    client = _app(postgres_url)
    integrity = client.post(
        "/api/v1/publications",
        headers=_pub(client, _jwt(role="publisher", sub="publisher-pa")),
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    assert integrity.status_code == 422
    assert integrity.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "does not belong to this client" in integrity.json()["error"]["message"]


def test_cross_tenant_run_is_indistinguishable_from_absent_run(
    pg_conn, pg_stores, postgres_url
) -> None:
    """A run owned by another tenant must answer exactly like a missing run.

    Guards the 404 contract against a future "helpful" 422 that would confirm
    whether a guessed processing_run id exists in some other tenant.
    """
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    seed_identity(pg_conn, subject="publisher-probe", role="publisher", client_id=CLIENT_B)
    facts.upsert(sample_fact())
    client = _app(postgres_url)
    headers = _pub(client, _jwt(role="publisher", sub="publisher-probe"))

    other_tenant = client.post(
        "/api/v1/publications",
        headers=headers,
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    never_existed = client.post(
        "/api/v1/publications",
        headers=headers,
        json={
            "client_id": CLIENT_B,
            "processing_run_id": "d0000000-0000-4000-8000-000000009999",
        },
    )

    assert other_tenant.status_code == never_existed.status_code == 404
    assert other_tenant.json() == never_existed.json()
    assert other_tenant.json()["error"]["code"] == "NOT_FOUND"


def test_same_tenant_non_succeeded_run_is_422(pg_conn, pg_stores, postgres_url) -> None:
    """A visible run in the wrong state is still a 422, not a 404.

    Confirms the 422 validation path is not dead for ordinary tenant-scoped
    publishers: the run belongs to the caller's own tenant, so RLS does not
    hide it, and only its status is rejected.
    """
    _ingest, facts, _pubs, _read = pg_stores
    seed_working_set(pg_conn)
    _seed_client_b_failed_run(pg_conn)
    seed_identity(pg_conn, subject="publisher-b-run", role="publisher", client_id=CLIENT_B)
    client = _app(postgres_url)
    wrong_state = client.post(
        "/api/v1/publications",
        headers=_pub(client, _jwt(role="publisher", sub="publisher-b-run")),
        json={"client_id": CLIENT_B, "processing_run_id": RUN_B},
    )
    assert wrong_state.status_code == 422
    assert wrong_state.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "succeeded" in wrong_state.json()["error"]["message"]
