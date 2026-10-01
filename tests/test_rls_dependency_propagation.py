"""Regression: the request RLS context must survive FastAPI dependency resolution.

``get_principal`` is the only place the tenant RLS ContextVar is bound for an
HTTP request, and every tenant store reads it back with
``transaction(pool, current_rls())``.  When the dependency is a **sync**
generator, FastAPI enters it through ``contextmanager_in_threadpool``, i.e. on a
*copied* context.  ``bind_rls`` then updates that copy, the request task never
sees it, ``current_rls()`` is ``None`` inside the endpoint, ``SET LOCAL ROLE
dfip_api`` is skipped, and every tenant query runs as the production LOGIN role
``dfip_app`` -- which intentionally holds no table grants.  The visible symptom
is ``InsufficientPrivilege: permission denied for table ...`` surfacing as HTTP
500 on the analytics, facts, publications, processing-run, source-file and
catalog routes.

The dependency must therefore stay an **async** generator.

These tests cover both halves of that invariant:

* a fast structural/behavioural test that needs no database, and
* a PostgreSQL test that connects as a real non-superuser NOINHERIT login role
  granted only ``dfip_api`` -- the production topology.  The existing
  PostgreSQL suite connects as a superuser, which bypasses ``SET ROLE``
  entirely and therefore cannot observe this class of bug.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import jwt
import pytest
from dfip_api.app import create_app
from dfip_api.deps import PrincipalDep, get_principal, get_principal_unchecked
from dfip_db.rls import current_rls
from fastapi.testclient import TestClient
from psycopg import sql

from postgres_support import (
    CLIENT_A,
    admin_connect,
    requires_postgres,
    seed_identity,
    seed_working_set,
    truncate_tenant,
)
from publisher_session_support import with_publisher_session
from test_p5_api import JWT_SECRET, make_settings

APP_LOGIN_ROLE = "dfip_app_rls_regression"
APP_LOGIN_PASSWORD = "rls-regression-login-pw"


def _token(*, role: str, sub: str, client_id: str | None = None) -> str:
    payload: dict[str, object] = {
        "sub": sub,
        "exp": datetime.now(tz=UTC) + timedelta(minutes=5),
        "role": role,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    return str(jwt.encode(payload, JWT_SECRET, algorithm="HS256"))


def _bearer(*, role: str, sub: str, client_id: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(role=role, sub=sub, client_id=client_id)}"}


def _settings(database_url: str | None = None):
    values: dict[str, object] = {
        "dfip_auth_mode": "jwt",
        "dfip_auth_secret": JWT_SECRET,
        "dfip_env": "test",
    }
    if database_url is not None:
        values["database_url"] = database_url
    return make_settings(**values)


# ---------------------------------------------------------------------------
# 1. Structural guard: the dependencies must remain async generators.
# ---------------------------------------------------------------------------


def test_principal_dependencies_are_async_generators() -> None:
    assert inspect.isasyncgenfunction(get_principal)
    assert inspect.isasyncgenfunction(get_principal_unchecked)


# ---------------------------------------------------------------------------
# 2. Behavioural guard: the bound context reaches the endpoint.
#    Runs without PostgreSQL; the pool is only a truthiness sentinel here.
# ---------------------------------------------------------------------------


def test_endpoint_sees_bound_rls_context() -> None:
    app = create_app(settings=_settings())
    # get_principal only checks that a pool exists to decide db_mode; the value
    # is never used for this request.
    app.state.db_pool = object()

    @app.get("/_probe/rls")
    def probe(principal: PrincipalDep) -> dict[str, object]:
        ctx = current_rls()
        return {
            "bound": ctx is not None,
            "role": None if ctx is None else ctx.role,
            "client_ids": [] if ctx is None else sorted(ctx.client_ids),
        }

    with TestClient(app) as http:
        response = http.get(
            "/_probe/rls",
            headers=_bearer(role="publisher", sub="rls-probe", client_id=CLIENT_A),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bound"] is True, (
        "current_rls() was None inside the endpoint: the RLS ContextVar bound by "
        "get_principal did not propagate through FastAPI dependency resolution."
    )
    assert body["role"] == "publisher"
    assert body["client_ids"] == [CLIENT_A]


# ---------------------------------------------------------------------------
# 3. Production-topology guard: a non-superuser NOINHERIT login role that is a
#    member of dfip_api must be able to read tenant tables through the API.
# ---------------------------------------------------------------------------


def _app_login_dsn(base_dsn: str) -> str:
    parts = urlsplit(base_dsn)
    netloc = f"{APP_LOGIN_ROLE}:{APP_LOGIN_PASSWORD}@{parts.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@pytest.fixture
def app_login_dsn(postgres_url: str) -> Iterator[str]:
    """A LOGIN role with NO table grants of its own, mirroring production dfip_app."""
    with admin_connect(postgres_url) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_LOGIN_ROLE,)
        ).fetchone()
        if exists is not None:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(APP_LOGIN_ROLE)))
            conn.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(
                    sql.Identifier(APP_LOGIN_ROLE)
                )
            )
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(APP_LOGIN_ROLE)))
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                sql.Identifier(APP_LOGIN_ROLE), sql.Literal(APP_LOGIN_PASSWORD)
            )
        )
        conn.execute(sql.SQL("GRANT dfip_api TO {}").format(sql.Identifier(APP_LOGIN_ROLE)))
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(APP_LOGIN_ROLE))
        )
        conn.commit()
    try:
        yield _app_login_dsn(postgres_url)
    finally:
        # Leave no cluster-level or tenant-level residue behind.
        with admin_connect(postgres_url) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(APP_LOGIN_ROLE)))
            conn.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(
                    sql.Identifier(APP_LOGIN_ROLE)
                )
            )
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(APP_LOGIN_ROLE)))
            conn.commit()


def test_login_role_holds_no_table_grants(app_login_dsn: str, postgres_url: str) -> None:
    """Guards the premise: the regression must come from the role switch, not grants."""
    with admin_connect(postgres_url) as conn:
        for table in ("publication_history_grain", "publication_current", "fact_campaign_day"):
            row = conn.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT') AS allowed",
                (APP_LOGIN_ROLE, table),
            ).fetchone()
            assert row["allowed"] is False, f"{APP_LOGIN_ROLE} must not hold SELECT on {table}"
        assert (
            conn.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname = %s", (APP_LOGIN_ROLE,)
            ).fetchone()["rolsuper"]
            is False
        )


@pytest.mark.postgres
@requires_postgres
def test_tenant_routes_work_for_non_superuser_login_role(
    pg_conn, postgres_url: str, app_login_dsn: str
) -> None:
    """Every affected route family, through a production-shaped connection."""
    with admin_connect(postgres_url) as conn:
        seed_working_set(conn)
        seed_identity(conn, subject="publisher-1", role="publisher", client_id=CLIENT_A)

    token = _token(role="publisher", sub="publisher-1", client_id=CLIENT_A)
    with TestClient(create_app(settings=_settings(app_login_dsn))) as http:
        headers = with_publisher_session({"Authorization": f"Bearer {token}"}, http, token)

        checked: list[tuple[str, str]] = [
            ("analytics overview", "/api/v1/analytics/overview"),
            ("analytics trends", "/api/v1/analytics/trends"),
            ("analytics insights", "/api/v1/analytics/insights"),
            ("analytics anomalies", "/api/v1/analytics/anomalies"),
            ("analytics explorer", "/api/v1/analytics/explorer"),
            ("facts", "/api/v1/facts"),
            ("publications", "/api/v1/publications"),
            ("publications current", "/api/v1/publications/current"),
            ("processing runs", "/api/v1/processing-runs"),
            ("source files", "/api/v1/source-files"),
            ("catalogs logic", "/api/v1/catalogs/logic"),
            ("catalogs labels", "/api/v1/catalogs/labels"),
        ]
        for label, url in checked:
            response = http.get(url, params={"client_id": CLIENT_A}, headers=headers)
            assert response.status_code == 200, (
                f"{label} ({url}) returned {response.status_code}: {response.text}"
            )
            assert "permission denied" not in response.text, label

    # Do not leak seeded tenant rows into the rest of the suite.
    truncate_tenant(pg_conn)
