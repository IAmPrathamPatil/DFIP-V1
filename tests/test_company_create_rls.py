"""Create Company must SET ROLE dfip_api and fail closed without inspector RLS.

In-memory tests do not open PostgreSQL. Postgres tests use DFIP_TEST_DATABASE_URL
and a disposable NOINHERIT LOGIN. They do not touch hosted/live data.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from dfip_db.client_directory import insert_client_for_inspector_from_pool
from dfip_db.rls import RlsContext, bind_rls, current_rls, reset_rls
from psycopg import connect
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import dict_row

from postgres_support import CLIENT_A, CLIENT_B, requires_postgres, seed_identity
from test_login_rls import _ensure_app_login_role, _login_app
from test_p5_api import _encode_jwt
from test_p9_authz import _error

PUBLISHER_A = "pub.create.a"
PUBLISHER_B = "pub.create.b"
CLIENT_USER = "client.create.a"
CREATED_NAME = "DFIP create-company RLS tenant"


def test_create_company_from_pool_fails_closed_without_rls() -> None:
    reset_rls()
    assert current_rls() is None
    with pytest.raises(PermissionError, match="Inspector RLS context"):
        insert_client_for_inspector_from_pool(
            object(),  # type: ignore[arg-type]
            name=CREATED_NAME,
            owner_user_id="owner-1",
            owner_role="publisher",
        )
    assert current_rls() is None


def test_create_company_from_pool_rejects_client_role_context() -> None:
    reset_rls()
    bind_rls(
        RlsContext(
            user_id="owner-1",
            role="client",
            client_ids=(CLIENT_A,),
            platform_admin=False,
            subject="client-user",
        )
    )
    try:
        with pytest.raises(PermissionError, match="Inspector RLS context"):
            insert_client_for_inspector_from_pool(
                object(),  # type: ignore[arg-type]
                name=CREATED_NAME,
                owner_user_id="owner-1",
                owner_role="client",
            )
        assert current_rls() is not None
        assert current_rls().role == "client"
    finally:
        reset_rls()
    assert current_rls() is None


def test_create_company_from_pool_rejects_owner_mismatch() -> None:
    reset_rls()
    bind_rls(
        RlsContext(
            user_id="owner-1",
            role="publisher",
            client_ids=(CLIENT_A,),
            platform_admin=False,
            subject="publisher-user",
        )
    )
    try:
        with pytest.raises(PermissionError, match="Inspector RLS context"):
            insert_client_for_inspector_from_pool(
                object(),  # type: ignore[arg-type]
                name=CREATED_NAME,
                owner_user_id="someone-else",
                owner_role="publisher",
            )
        with pytest.raises(PermissionError, match="Inspector RLS context"):
            insert_client_for_inspector_from_pool(
                object(),  # type: ignore[arg-type]
                name=CREATED_NAME,
                owner_user_id="owner-1",
                owner_role="admin",
            )
        assert current_rls() is not None
        assert current_rls().user_id == "owner-1"
    finally:
        reset_rls()
    assert current_rls() is None


def _bearer(subject: str, *, role: str, client_id: str) -> dict[str, str]:
    token = _encode_jwt(sub=subject, role=role, client_id=client_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.postgres
@requires_postgres
def test_postgres_create_company_from_pool_fails_closed(pg_pool) -> None:
    reset_rls()
    with pytest.raises(PermissionError, match="Inspector RLS context"):
        insert_client_for_inspector_from_pool(
            pg_pool,
            name=CREATED_NAME,
            owner_user_id=str(uuid4()),
            owner_role="publisher",
        )
    assert current_rls() is None


@pytest.mark.postgres
@requires_postgres
def test_postgres_create_company_uses_inspector_rls_without_table_grants(
    tmp_path: Path, pg_conn, postgres_url
) -> None:
    reset_rls()
    api_login_url = _ensure_app_login_role(pg_conn, postgres_url)
    assert pg_conn.execute(
        "SELECT has_table_privilege(%s, 'client', 'INSERT') AS allowed",
        ("dfip_test_app_login",),
    ).fetchone()["allowed"] is False
    assert pg_conn.execute(
        "SELECT has_table_privilege(%s, 'client', 'SELECT') AS allowed",
        ("dfip_test_app_login",),
    ).fetchone()["allowed"] is False
    assert pg_conn.execute(
        "SELECT has_table_privilege('dfip_api', 'client', 'INSERT') AS allowed"
    ).fetchone()["allowed"] is True
    assert pg_conn.execute(
        "SELECT has_table_privilege('dfip_worker', 'client', 'INSERT') AS allowed"
    ).fetchone()["allowed"] is False

    publisher_a = seed_identity(pg_conn, subject=PUBLISHER_A, role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject=PUBLISHER_B, role="publisher", client_id=CLIENT_B)
    seed_identity(pg_conn, subject=CLIENT_USER, role="client", client_id=CLIENT_A)

    denied_id = str(uuid4())
    with connect(api_login_url, row_factory=dict_row) as raw:
        raw.execute("BEGIN")
        raw.execute("SET LOCAL ROLE dfip_api")
        raw.execute("SELECT set_config('dfip.role', 'client', true)")
        raw.execute("SELECT set_config('dfip.platform_admin', 'false', true)")
        raw.execute("SELECT set_config('dfip.client_ids', %s, true)", (CLIENT_A,))
        raw.execute("SELECT set_config('dfip.user_id', %s, true)", (CLIENT_USER,))
        with pytest.raises(InsufficientPrivilege):
            raw.execute(
                "INSERT INTO client (id, code, name) VALUES (%s, %s, %s)",
                (denied_id, denied_id, "denied-client-role"),
            )
        raw.execute("ROLLBACK")

    http = _login_app(api_login_url, tmp_path / "create-company-archive")
    try:
        client_denied = http.post(
            "/api/v1/clients",
            headers=_bearer(CLIENT_USER, role="client", client_id=CLIENT_A),
            json={"name": CREATED_NAME},
        )
        assert client_denied.status_code == 403, client_denied.text
        assert _error(client_denied)["code"] == "AUTHORIZATION_FAILED"
        assert current_rls() is None

        created = http.post(
            "/api/v1/clients",
            headers=_bearer(PUBLISHER_A, role="publisher", client_id=CLIENT_A),
            json={"name": CREATED_NAME},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        new_id = body["client_id"]
        assert new_id not in {CLIENT_A, CLIENT_B, denied_id}
        assert body["code"] == new_id
        assert body["name"] == CREATED_NAME
        assert current_rls() is None

        membership = pg_conn.execute(
            """
            SELECT user_id::text AS user_id, role
            FROM client_membership
            WHERE client_id = %s
            """,
            (new_id,),
        ).fetchall()
        pg_conn.commit()
        assert len(membership) == 1
        assert membership[0]["user_id"] == publisher_a
        assert membership[0]["role"] == "publisher"

        listed_a = http.get(
            "/api/v1/clients",
            headers=_bearer(PUBLISHER_A, role="publisher", client_id=CLIENT_A),
        )
        assert listed_a.status_code == 200, listed_a.text
        ids_a = {item["client_id"] for item in listed_a.json()["items"]}
        assert new_id in ids_a

        listed_b = http.get(
            "/api/v1/clients",
            headers=_bearer(PUBLISHER_B, role="publisher", client_id=CLIENT_B),
        )
        assert listed_b.status_code == 200, listed_b.text
        ids_b = {item["client_id"] for item in listed_b.json()["items"]}
        assert new_id not in ids_b
        assert CLIENT_B in ids_b
        assert current_rls() is None
    finally:
        http.close()
        reset_rls()
        assert current_rls() is None
