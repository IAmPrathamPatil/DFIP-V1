"""Local E2E bootstrap: demo seed guards and default-client JWT loop.

Does not open hosted databases. PostgreSQL cases require DFIP_TEST_DATABASE_URL
and drop/recreate ``public`` via existing fixtures — never a live URI.
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from dfip_api.app import create_app
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.local_demo_seed import run_cli, seed_company2_identities, seed_demo_identities
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_db.identity import MembershipRow
from dfip_db.local_demo_guard import (
    COMPANY_2_CLIENT_ID,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
    LocalDemoSeedError,
    assert_local_demo_seed_allowed,
    is_hosted_or_unsafe_database_url,
)
from dfip_web.client_report_download import CLIENT_REPORT_DOWNLOAD_NAME
from fastapi.testclient import TestClient

from http_ingest_support import source_row, upload_workbook, workbook_bytes
from postgres_support import postgres_only, requires_postgres
from test_p5_api import JWT_SECRET, make_settings

PUBLISHER_PASSWORD = "local-publisher-pass"
CLIENT_PASSWORD = "local-client-pass"
ITERATIONS = 1000
SECRET = JWT_SECRET


def _guard_kwargs(**overrides):
    body = {
        "database_url": "postgresql://postgres@127.0.0.1:5433/dfip",
        "dfip_env": "development",
        "seed_flag": "1",
        "confirmed": True,
    }
    body.update(overrides)
    return body


def test_empty_database_url_is_refused() -> None:
    assert is_hosted_or_unsafe_database_url("")
    try:
        assert_local_demo_seed_allowed(**_guard_kwargs(database_url=""))
    except LocalDemoSeedError as exc:
        assert "DATABASE_URL" in str(exc)
        return
    raise AssertionError("expected LocalDemoSeedError")


def test_hosted_supabase_url_is_refused() -> None:
    url = "postgresql://postgres.abc:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    assert is_hosted_or_unsafe_database_url(url)
    try:
        assert_local_demo_seed_allowed(**_guard_kwargs(database_url=url))
    except LocalDemoSeedError as exc:
        message = str(exc)
        assert "secret" not in message
        assert "refuses" in message
        return
    raise AssertionError("expected LocalDemoSeedError")


def test_production_env_is_refused() -> None:
    try:
        assert_local_demo_seed_allowed(**_guard_kwargs(dfip_env="production"))
    except LocalDemoSeedError as exc:
        assert "production" in str(exc).lower()
        return
    raise AssertionError("expected LocalDemoSeedError")


def test_missing_seed_flag_and_confirm_are_refused() -> None:
    try:
        assert_local_demo_seed_allowed(**_guard_kwargs(seed_flag=""))
    except LocalDemoSeedError:
        pass
    else:
        raise AssertionError("expected seed flag refusal")
    try:
        assert_local_demo_seed_allowed(**_guard_kwargs(confirmed=False))
    except LocalDemoSeedError as exc:
        assert "confirm-local-only" in str(exc)
        return
    raise AssertionError("expected confirm refusal")


def test_loopback_url_is_allowed() -> None:
    assert not is_hosted_or_unsafe_database_url("postgresql://postgres@127.0.0.1:5433/dfip")
    assert_local_demo_seed_allowed(**_guard_kwargs())


def test_cli_without_dsn_exits_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DFIP_LOCAL_DEMO_SEED", "1")
    monkeypatch.setenv("DFIP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", PUBLISHER_PASSWORD)
    monkeypatch.setenv("DFIP_LOCAL_DEMO_CLIENT_PASSWORD", CLIENT_PASSWORD)
    code = run_cli(["--confirm-local-only"])
    assert code == 2
    err = capsys.readouterr().err
    assert PUBLISHER_PASSWORD not in err
    assert CLIENT_PASSWORD not in err
    assert "DATABASE_URL" in err


def _identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.put_password_user(
        subject=DEMO_PUBLISHER_SUBJECT,
        password=PUBLISHER_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(DEFAULT_CLIENT_ID, "publisher"),),
    )
    store.put_password_user(
        subject=DEMO_CLIENT_SUBJECT,
        password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(DEFAULT_CLIENT_ID, "client"),),
    )
    return store


def _app():
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
        ),
        identity_store=_identity(),
        publication_store=InMemoryPublicationStore(),
    )


def _login(http: TestClient, username: str, password: str):
    return http.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_demo_publisher_and_client_login_succeed() -> None:
    http = TestClient(_app())
    publisher = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    client = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD)
    assert publisher.status_code == 200
    assert client.status_code == 200
    assert publisher.json()["session"]["role"] == "publisher"
    assert publisher.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    assert client.json()["session"]["role"] == "client"
    assert client.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    assert PUBLISHER_PASSWORD not in publisher.text
    assert CLIENT_PASSWORD not in client.text
    assert SECRET not in publisher.text


def test_client_cannot_inspect_or_publish_and_publisher_upload_does_not_publish(
    tmp_path: Path,
) -> None:
    http = TestClient(_app())
    publisher_token = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD).json()[
        "access_token"
    ]
    client_token = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD).json()["access_token"]
    publisher = _bearer(publisher_token)
    client = _bearer(client_token)

    inspect = http.get("/api/v1/facts", headers=client)
    assert inspect.status_code == 403
    publish_denied = http.post(
        "/api/v1/publications",
        headers=client,
        json={
            "client_id": DEFAULT_CLIENT_ID,
            "processing_run_id": "d0000000-0000-4000-8000-000000000001",
        },
    )
    assert publish_denied.status_code == 403

    content = workbook_bytes(tmp_path / "demo.xlsx", [source_row()])
    uploaded = upload_workbook(
        http,
        content,
        "demo.xlsx",
        headers=publisher,
        client_id=DEFAULT_CLIENT_ID,
    )
    assert uploaded.status_code in {200, 201}
    body = uploaded.json()
    assert body["published"] is False
    run_id = body["processing_run"]["processing_run_id"]
    assert body["processing_run"]["qa_verdict"] in {"pass", "warn"}

    before = http.get("/api/v1/publications/current/facts", headers=client)
    assert before.status_code == 200
    assert before.json()["items"] == []

    created = http.post(
        "/api/v1/publications",
        headers=publisher,
        json={"client_id": DEFAULT_CLIENT_ID, "processing_run_id": run_id},
    )
    assert created.status_code == 201

    after = http.get("/api/v1/publications/current/facts", headers=client)
    assert after.status_code == 200
    assert after.json()["pagination"]["total"] >= 1
    assert after.json()["items"][0]["client_id"] == DEFAULT_CLIENT_ID

    working = http.get("/api/v1/facts", headers=publisher)
    assert working.status_code == 200

    report = http.get("/api/v1/publications/current/client-report.xlsx", headers=client)
    assert report.status_code == 200
    disposition = report.headers.get("content-disposition", "")
    assert CLIENT_REPORT_DOWNLOAD_NAME in disposition
    names = ZipFile(BytesIO(report.content)).namelist()
    assert "xl/workbook.xml" in names
    assert PUBLISHER_PASSWORD not in report.content.decode("latin-1", errors="ignore")


@requires_postgres
@postgres_only
def test_postgres_seed_is_idempotent_and_login_works(pg_conn, postgres_url: str) -> None:
    first = seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
    )
    pg_conn.commit()
    second = seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
    )
    pg_conn.commit()
    assert first["publisher_subject"] == DEMO_PUBLISHER_SUBJECT
    assert second["publisher_user_id"] == first["publisher_user_id"]
    assert first["client_id"] == DEFAULT_CLIENT_ID

    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
        )
    )
    with TestClient(app) as http:
        publisher = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
        client = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD)
        assert publisher.status_code == 200, publisher.text
        assert client.status_code == 200, client.text
        assert publisher.json()["session"]["role"] == "publisher"
        assert client.json()["session"]["role"] == "client"
        assert os.environ.get("DFIP_TEST_DATABASE_URL", "")
        assert "supabase" not in os.environ.get("DFIP_TEST_DATABASE_URL", "").lower()


@requires_postgres
@postgres_only
def test_postgres_seed_binds_demo_client_to_active_company_when_default_inactive(
    pg_conn, postgres_url: str
) -> None:
    seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
    )
    seed_company2_identities(
        pg_conn,
        publisher_password="local-publisher2-pass",
        client_password="local-client2-pass",
        iterations=ITERATIONS,
    )
    pg_conn.execute(
        "UPDATE client SET lifecycle_status = 'inactive' WHERE id = %s",
        (DEFAULT_CLIENT_ID,),
    )
    pg_conn.commit()
    seeded = seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
    )
    pg_conn.commit()
    assert seeded["client_id"] == COMPANY_2_CLIENT_ID
    rows = pg_conn.execute(
        """
        SELECT c.id::text AS client_id
        FROM client_membership m
        JOIN app_user u ON u.id = m.user_id
        JOIN client c ON c.id = m.client_id
        WHERE u.subject = %s AND m.role = 'client'
        """,
        (DEMO_CLIENT_SUBJECT,),
    ).fetchall()
    assert [row["client_id"] for row in rows] == [COMPANY_2_CLIENT_ID]

    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
        )
    )
    with TestClient(app) as http:
        client = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD)
        assert client.status_code == 200, client.text
        assert client.json()["session"]["role"] == "client"
        assert client.json()["session"]["client_id"] == COMPANY_2_CLIENT_ID
        inactive = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD)
        # Password still verifies; session is the active company, not default.
        assert inactive.json()["session"]["client_id"] != DEFAULT_CLIENT_ID
