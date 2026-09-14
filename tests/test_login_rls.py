"""Login identity reads must SET ROLE dfip_api for production API LOGIN.

In-memory tests do not open PostgreSQL. Postgres tests use DFIP_TEST_DATABASE_URL
and a disposable NOINHERIT LOGIN. They do not touch hosted/live data.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import pytest
from dfip_api.app import create_app
from dfip_api.password import hash_password
from dfip_db.rls import (
    IDENTITY_LOOKUP_USER_ID,
    RlsContext,
    bind_rls,
    current_rls,
    identity_lookup_bind,
    identity_lookup_rls,
    reset_rls,
)
from fastapi.testclient import TestClient
from psycopg import connect, sql
from psycopg.errors import InsufficientPrivilege
from psycopg.rows import dict_row

from postgres_support import CLIENT_A, CLIENT_B, requires_postgres, seed_identity
from test_p5_api import JWT_SECRET, make_settings
from test_p9_authz import _error

APP_LOGIN_ROLE = "dfip_test_app_login"
APP_LOGIN_PASSWORD = "dfip-test-app-login-only"
LOGIN_USER = "alice.login"
LOGIN_PASSWORD = "correct-horse-battery"
OTHER_USER = "bob.other"


def test_identity_lookup_rls_is_not_platform_admin() -> None:
    context = identity_lookup_rls()
    assert context.platform_admin is False
    assert context.client_ids == ()
    assert context.role == "client"
    assert context.user_id == IDENTITY_LOOKUP_USER_ID
    assert context.subject == IDENTITY_LOOKUP_USER_ID


def test_identity_lookup_bind_resets() -> None:
    reset_rls()
    assert current_rls() is None
    with identity_lookup_bind(client_ids=(CLIENT_A,)) as bound:
        assert bound.platform_admin is False
        assert bound.client_ids == (CLIENT_A,)
        assert current_rls() is bound
    assert current_rls() is None


def test_identity_lookup_bind_clears_on_exception() -> None:
    reset_rls()
    with pytest.raises(RuntimeError, match="boom"):
        with identity_lookup_bind():
            assert current_rls() is not None
            raise RuntimeError("boom")
    assert current_rls() is None


def test_identity_lookup_bind_preserves_http_context() -> None:
    reset_rls()
    http = RlsContext(
        user_id="http-user",
        role="publisher",
        client_ids=(CLIENT_A,),
        platform_admin=False,
        subject="http-user",
    )
    bind_rls(http)
    try:
        with identity_lookup_bind(
            client_ids=(CLIENT_B,),
            platform_admin=True,
            role="admin",
        ) as bound:
            assert bound is http
            assert bound.platform_admin is False
            assert bound.client_ids == (CLIENT_A,)
        assert current_rls() is http
    finally:
        reset_rls()
    assert current_rls() is None


def _dsn_for_login(postgres_url: str, user: str, password: str) -> str:
    parsed = urlparse(postgres_url)
    if parsed.scheme.lower() not in {"postgres", "postgresql"}:
        raise ValueError("unsupported test DSN")
    host = parsed.hostname or "127.0.0.1"
    port = f":{parsed.port}" if parsed.port else ""
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
    return urlunparse(parsed._replace(netloc=f"{auth}@{host}{port}"))


def _ensure_app_login_role(pg_conn, postgres_url: str) -> str:
    pg_conn.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE usename = %s AND pid <> pg_backend_pid()
        """,
        (APP_LOGIN_ROLE,),
    )
    pg_conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(APP_LOGIN_ROLE)))
    pg_conn.execute(
        sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION"
        ).format(sql.Identifier(APP_LOGIN_ROLE), sql.Literal(APP_LOGIN_PASSWORD))
    )
    pg_conn.execute(sql.SQL("GRANT dfip_api TO {}").format(sql.Identifier(APP_LOGIN_ROLE)))
    pg_conn.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(pg_conn.info.dbname),
            sql.Identifier(APP_LOGIN_ROLE),
        )
    )
    pg_conn.commit()
    return _dsn_for_login(postgres_url, APP_LOGIN_ROLE, APP_LOGIN_PASSWORD)


def _seed_password_user(pg_conn, *, subject: str, client_id: str) -> None:
    user_id = seed_identity(pg_conn, subject=subject, role="client", client_id=client_id)
    pg_conn.execute(
        "UPDATE app_user SET password_hash = %s WHERE id = %s",
        (hash_password(LOGIN_PASSWORD, iterations=1000), user_id),
    )
    pg_conn.commit()


def _login_app(api_login_url: str, archive: Path) -> TestClient:
    return TestClient(
        create_app(
            settings=make_settings(
                dfip_auth_mode="jwt",
                dfip_auth_secret=JWT_SECRET,
                database_url=api_login_url,
                dfip_env="test",
                dfip_storage_endpoint=str(archive),
            )
        )
    )


@pytest.mark.postgres
@requires_postgres
def test_postgres_login_uses_set_role_without_table_grants(
    tmp_path: Path, pg_conn, postgres_url
) -> None:
    reset_rls()
    api_login_url = _ensure_app_login_role(pg_conn, postgres_url)
    assert pg_conn.execute(
        "SELECT has_table_privilege(%s, 'client', 'SELECT') AS allowed",
        (APP_LOGIN_ROLE,),
    ).fetchone()["allowed"] is False
    assert pg_conn.execute(
        "SELECT has_table_privilege(%s, 'app_user', 'SELECT') AS allowed",
        (APP_LOGIN_ROLE,),
    ).fetchone()["allowed"] is False
    with connect(api_login_url, row_factory=dict_row) as raw:
        with pytest.raises(InsufficientPrivilege):
            raw.execute("SELECT 1 FROM client LIMIT 1")

    _seed_password_user(pg_conn, subject=LOGIN_USER, client_id=CLIENT_A)
    _seed_password_user(pg_conn, subject=OTHER_USER, client_id=CLIENT_B)

    http = _login_app(api_login_url, tmp_path / "login-archive")
    try:
        missing = http.post(
            "/api/v1/auth/login",
            json={"username": "nobody.here", "password": LOGIN_PASSWORD},
        )
        wrong = http.post(
            "/api/v1/auth/login",
            json={"username": LOGIN_USER, "password": "not-the-password"},
        )
        assert missing.status_code == 401, missing.text
        assert wrong.status_code == 401, wrong.text
        assert _error(missing)["code"] == "AUTHENTICATION_FAILED"
        assert _error(wrong)["code"] == "AUTHENTICATION_FAILED"
        assert current_rls() is None

        ok = http.post(
            "/api/v1/auth/login",
            json={"username": LOGIN_USER, "password": LOGIN_PASSWORD},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] >= 1
        assert body["session"]["subject"] == LOGIN_USER
        assert body["session"]["auth_mode"] == "jwt"
        assert body["session"]["role"] == "client"
        assert body["session"]["client_id"] == CLIENT_A
        assert [row["client_id"] for row in body["session"]["clients"]] == [CLIENT_A]
        assert body["session"]["clients"][0]["code"] == "default"
        assert "password_hash" not in ok.text
        assert LOGIN_PASSWORD not in ok.text
        assert current_rls() is None

        token = body["access_token"]
        session = http.get(
            "/api/v1/session",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert session.status_code == 200, session.text
        payload = session.json()
        assert payload["subject"] == LOGIN_USER
        assert payload["client_id"] == CLIENT_A
        assert [row["client_id"] for row in payload["clients"]] == [CLIENT_A]
        assert CLIENT_B not in session.text
        assert current_rls() is None
    finally:
        http.close()
        reset_rls()
        assert current_rls() is None
