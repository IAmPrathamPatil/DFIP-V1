"""Company registry actions must use membership-scoped inspector RLS.

In-memory tests do not open PostgreSQL. Postgres tests use DFIP_TEST_DATABASE_URL
and a disposable NOINHERIT LOGIN. They do not touch hosted/live data.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from dfip_db.client_directory import (
    update_client_name_from_pool,
)
from dfip_db.identity import insert_client_password_user_from_pool
from dfip_db.rls import (
    RlsContext,
    bind_rls,
    current_rls,
    expand_inspector_registry_clients,
    require_inspector_rls,
    reset_rls,
)

from postgres_support import CLIENT_A, CLIENT_B, requires_postgres, seed_identity
from test_login_rls import _ensure_app_login_role, _login_app
from test_p5_api import _encode_jwt
from test_p9_authz import _error

PUBLISHER_A = "pub.registry.a"
PUBLISHER_B = "pub.registry.b"
CLIENT_USER = "client.registry.a"
PORTAL_USER = "portal.registry.b"
PORTAL_PASSWORD = "client-portal-ok"
RENAMED = "DFIP registry renamed tenant"


class _MembershipConn:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.guc: str | None = None

    def execute(self, statement: str, params=None):
        if "set_config" in statement:
            self.guc = None if params is None else params[0]
        return self

    def fetchall(self):
        return self.rows


def _bearer(subject: str, *, role: str, client_id: str) -> dict[str, str]:
    token = _encode_jwt(sub=subject, role=role, client_id=client_id)
    return {"Authorization": f"Bearer {token}"}


def test_require_inspector_rls_fails_closed() -> None:
    reset_rls()
    with pytest.raises(PermissionError, match="Inspector RLS context"):
        require_inspector_rls()
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
            require_inspector_rls()
    finally:
        reset_rls()
    assert current_rls() is None


def test_expand_inspector_registry_clients_only_adds_membership_ids() -> None:
    reset_rls()
    ctx = RlsContext(
        user_id="11111111-1111-4111-8111-111111111111",
        role="publisher",
        client_ids=(CLIENT_A,),
        platform_admin=False,
        subject="publisher-user",
    )
    bind_rls(ctx)
    try:
        conn = _MembershipConn([{"client_id": CLIENT_B}])
        authorized = expand_inspector_registry_clients(conn, (CLIENT_B, str(uuid4())))
        assert authorized == (CLIENT_B,)
        assert conn.guc == f"{CLIENT_A},{CLIENT_B}"
        assert current_rls() is ctx
        assert current_rls().client_ids == (CLIENT_A,)
        assert current_rls().platform_admin is False
        denied = _MembershipConn([])
        assert expand_inspector_registry_clients(denied, (CLIENT_B,)) == ()
        assert denied.guc == CLIENT_A
    finally:
        reset_rls()
    assert current_rls() is None


def test_registry_writes_fail_closed_without_inspector() -> None:
    reset_rls()
    with pytest.raises(PermissionError, match="Inspector RLS context"):
        insert_client_password_user_from_pool(
            object(),  # type: ignore[arg-type]
            subject=PORTAL_USER,
            password_hash="unused",
            client_id=CLIENT_B,
        )
    with pytest.raises(PermissionError, match="Inspector RLS context"):
        update_client_name_from_pool(object(), CLIENT_B, RENAMED)  # type: ignore[arg-type]
    assert current_rls() is None


def test_insert_client_user_from_pool_rejects_client_role_context() -> None:
    reset_rls()
    bind_rls(
        RlsContext(
            user_id="owner-1",
            role="client",
            client_ids=(CLIENT_A,),
            platform_admin=False,
            subject=CLIENT_USER,
        )
    )
    try:
        with pytest.raises(PermissionError, match="Inspector RLS context"):
            insert_client_password_user_from_pool(
                object(),  # type: ignore[arg-type]
                subject=PORTAL_USER,
                password_hash="unused",
                client_id=CLIENT_A,
            )
        assert current_rls() is not None
        assert current_rls().role == "client"
    finally:
        reset_rls()
    assert current_rls() is None


@pytest.mark.postgres
@requires_postgres
def test_postgres_registry_actions_use_membership_scoped_inspector_rls(
    tmp_path: Path, pg_conn, postgres_url
) -> None:
    reset_rls()
    api_login_url = _ensure_app_login_role(pg_conn, postgres_url)
    assert pg_conn.execute(
        "SELECT has_table_privilege(%s, 'client', 'INSERT') AS allowed",
        ("dfip_test_app_login",),
    ).fetchone()["allowed"] is False
    assert pg_conn.execute(
        "SELECT has_table_privilege(%s, 'client', 'UPDATE') AS allowed",
        ("dfip_test_app_login",),
    ).fetchone()["allowed"] is False
    assert pg_conn.execute(
        "SELECT has_table_privilege(%s, 'app_user', 'INSERT') AS allowed",
        ("dfip_test_app_login",),
    ).fetchone()["allowed"] is False
    assert pg_conn.execute(
        "SELECT has_table_privilege('dfip_api', 'app_user', 'INSERT') AS allowed"
    ).fetchone()["allowed"] is True
    assert pg_conn.execute(
        "SELECT has_table_privilege('dfip_api', 'client', 'UPDATE') AS allowed"
    ).fetchone()["allowed"] is True
    assert pg_conn.execute(
        "SELECT has_table_privilege('dfip_worker', 'app_user', 'INSERT') AS allowed"
    ).fetchone()["allowed"] is False
    assert pg_conn.execute(
        "SELECT has_table_privilege('dfip_worker', 'client', 'UPDATE') AS allowed"
    ).fetchone()["allowed"] is False
    policies = {
        row["polname"]
        for row in pg_conn.execute(
            """
            SELECT polname FROM pg_policy
            WHERE polrelid = 'client'::regclass
            """
        ).fetchall()
    }
    assert "client_select" in policies
    assert "client_inspector_insert" in policies
    assert "client_inspector_update" in policies
    assert pg_conn.execute(
        """
        SELECT prosecdef
        FROM pg_proc
        WHERE proname = 'dfip_delete_company'
          AND pronamespace = 'public'::regnamespace
        """
    ).fetchone()["prosecdef"] is True

    publisher_a = seed_identity(pg_conn, subject=PUBLISHER_A, role="publisher", client_id=CLIENT_A)
    seed_identity(pg_conn, subject=PUBLISHER_B, role="publisher", client_id=CLIENT_B)
    seed_identity(pg_conn, subject=CLIENT_USER, role="client", client_id=CLIENT_A)
    pg_conn.execute(
        """
        INSERT INTO client_membership (user_id, client_id, role)
        VALUES (%s, %s, 'publisher')
        """,
        (publisher_a, CLIENT_B),
    )
    disposable_id = str(uuid4())
    pg_conn.execute(
        "INSERT INTO client (id, code, name) VALUES (%s, %s, %s)",
        (disposable_id, disposable_id, "DFIP disposable registry tenant"),
    )
    pg_conn.execute(
        """
        INSERT INTO client_membership (user_id, client_id, role)
        VALUES (%s, %s, 'publisher')
        """,
        (publisher_a, disposable_id),
    )
    pg_conn.commit()

    http = _login_app(api_login_url, tmp_path / "registry-archive")
    try:
        selected_a = _bearer(PUBLISHER_A, role="publisher", client_id=CLIENT_A)
        other = _bearer(PUBLISHER_B, role="publisher", client_id=CLIENT_B)
        client_headers = _bearer(CLIENT_USER, role="client", client_id=CLIENT_A)

        denied_client = http.post(
            f"/api/v1/clients/{CLIENT_B}/users",
            headers=client_headers,
            json={
                "username": PORTAL_USER,
                "password": PORTAL_PASSWORD,
                "confirm_password": PORTAL_PASSWORD,
                "role": "client",
            },
        )
        assert denied_client.status_code == 403, denied_client.text
        assert _error(denied_client)["code"] == "AUTHORIZATION_FAILED"
        assert current_rls() is None

        stolen = http.post(
            f"/api/v1/clients/{CLIENT_A}/users",
            headers=other,
            json={
                "username": "stolen.portal",
                "password": PORTAL_PASSWORD,
                "confirm_password": PORTAL_PASSWORD,
                "role": "client",
            },
        )
        assert stolen.status_code == 403, stolen.text

        created = http.post(
            f"/api/v1/clients/{CLIENT_B}/users",
            headers=selected_a,
            json={
                "username": PORTAL_USER,
                "password": PORTAL_PASSWORD,
                "confirm_password": PORTAL_PASSWORD,
                "role": "client",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["username"] == PORTAL_USER
        assert body["client_id"] == CLIENT_B
        assert body["role"] == "client"
        assert "password" not in body
        assert current_rls() is None

        persisted = pg_conn.execute(
            """
            SELECT u.subject, m.role, m.client_id::text AS client_id
            FROM app_user AS u
            JOIN client_membership AS m ON m.user_id = u.id
            WHERE u.subject = %s
            """,
            (PORTAL_USER,),
        ).fetchone()
        pg_conn.commit()
        assert persisted is not None
        assert persisted["subject"] == PORTAL_USER
        assert persisted["role"] == "client"
        assert persisted["client_id"] == CLIENT_B
        assert persisted["client_id"] != CLIENT_A

        renamed = http.post(
            f"/api/v1/clients/{CLIENT_B}/rename",
            headers=selected_a,
            json={"name": RENAMED},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["client_id"] == CLIENT_B
        assert renamed.json()["name"] == RENAMED
        other_rename = http.post(
            f"/api/v1/clients/{CLIENT_A}/rename",
            headers=other,
            json={"name": "stolen-name"},
        )
        assert other_rename.status_code == 403, other_rename.text
        client_rename = http.post(
            f"/api/v1/clients/{CLIENT_A}/rename",
            headers=client_headers,
            json={"name": "client-rename"},
        )
        assert client_rename.status_code == 403, client_rename.text

        deactivated = http.post(
            f"/api/v1/clients/{CLIENT_B}/deactivate",
            headers=selected_a,
        )
        assert deactivated.status_code == 200, deactivated.text
        assert deactivated.json()["lifecycle_status"] == "inactive"
        other_deactivate = http.post(
            f"/api/v1/clients/{CLIENT_A}/deactivate",
            headers=other,
        )
        assert other_deactivate.status_code == 403, other_deactivate.text
        client_deactivate = http.post(
            f"/api/v1/clients/{CLIENT_A}/deactivate",
            headers=client_headers,
        )
        assert client_deactivate.status_code == 403, client_deactivate.text
        restored = http.post(
            f"/api/v1/clients/{CLIENT_B}/reactivate",
            headers=selected_a,
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["lifecycle_status"] == "active"
        assert restored.json()["deactivated_at"] is None

        missing = str(uuid4())
        ghost = http.delete(f"/api/v1/clients/{missing}", headers=other)
        assert ghost.status_code == 403, ghost.text
        assert _error(ghost)["code"] == "AUTHORIZATION_FAILED"

        steal_delete = http.delete(f"/api/v1/clients/{disposable_id}", headers=other)
        assert steal_delete.status_code == 403, steal_delete.text
        assert steal_delete.json().get("already_absent") is not True

        still_active = http.delete(f"/api/v1/clients/{disposable_id}", headers=selected_a)
        assert still_active.status_code == 409, still_active.text

        deactivated_d = http.post(
            f"/api/v1/clients/{disposable_id}/deactivate",
            headers=selected_a,
        )
        assert deactivated_d.status_code == 200, deactivated_d.text
        deleted = http.delete(f"/api/v1/clients/{disposable_id}", headers=selected_a)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted"] is True
        assert deleted.json()["already_absent"] is False
        gone = pg_conn.execute(
            "SELECT 1 FROM client WHERE id = %s",
            (disposable_id,),
        ).fetchone()
        pg_conn.commit()
        assert gone is None
        assert current_rls() is None
    finally:
        http.close()
        reset_rls()
        assert current_rls() is None
