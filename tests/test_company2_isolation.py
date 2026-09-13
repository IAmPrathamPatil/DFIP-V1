"""Company 1 / Company 2 isolation on disposable local identity + publications.

Does not open hosted databases. PostgreSQL cases require DFIP_TEST_DATABASE_URL
and drop/recreate ``public`` via existing fixtures — never a live URI.
Does not add a SPA publisher picker or change login UI.
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
    COMPANY_2_CLIENT_CODE,
    COMPANY_2_CLIENT_ID,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
)
from dfip_web.client_report_download import CLIENT_REPORT_DOWNLOAD_NAME
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.rows import dict_row

from http_ingest_support import source_row, upload_workbook, workbook_bytes
from postgres_support import postgres_only, requires_postgres
from test_p5_api import JWT_SECRET, make_settings

PUBLISHER_PASSWORD = "local-publisher-pass"
CLIENT_PASSWORD = "local-client-pass"
PUBLISHER2_PASSWORD = "local-publisher2-pass"
CLIENT2_PASSWORD = "local-client2-pass"
ITERATIONS = 1000
SECRET = JWT_SECRET
CAMP_1 = "camp-co1"
CAMP_2 = "camp-co2"


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
    store.put_password_user(
        subject=DEMO_PUBLISHER_2_SUBJECT,
        password=PUBLISHER2_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(COMPANY_2_CLIENT_ID, "publisher"),),
    )
    store.put_password_user(
        subject=DEMO_CLIENT_2_SUBJECT,
        password=CLIENT2_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(COMPANY_2_CLIENT_ID, "client"),),
    )
    return store


def _memory_app():
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
        ),
        identity_store=_identity(),
        publication_store=InMemoryPublicationStore(),
    )


def _login(http: TestClient, username: str, password: str, client_id: str | None = None):
    body: dict[str, str] = {"username": username, "password": password}
    if client_id:
        body["client_id"] = client_id
    return http.post("/api/v1/auth/login", json=body)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _token(http: TestClient, username: str, password: str, client_id: str | None = None) -> str:
    response = _login(http, username, password, client_id)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _campaign_ids(payload: dict) -> set[str]:
    return {str(item.get("campaign_id")) for item in payload.get("items") or []}


def _xlsx_xml_text(content: bytes) -> str:
    parts: list[str] = []
    with ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.endswith(".xml"):
                parts.append(archive.read(name).decode("utf-8", errors="ignore"))
    return "\n".join(parts)


def _publish_synthetic(
    http: TestClient, tmp_path: Path, *, publisher: dict, client_id: str, campaign_id: str
):
    content = workbook_bytes(
        tmp_path / f"{campaign_id}.xlsx",
        [source_row(**{"Campaign ID": campaign_id, "Variation ID": f"var-{campaign_id}"})],
    )
    uploaded = upload_workbook(
        http,
        content,
        f"{campaign_id}.xlsx",
        headers=publisher,
        client_id=client_id,
    )
    assert uploaded.status_code in {200, 201}, uploaded.text
    body = uploaded.json()
    assert body["published"] is False
    run_id = body["processing_run"]["processing_run_id"]
    created = http.post(
        "/api/v1/publications",
        headers=publisher,
        json={"client_id": client_id, "processing_run_id": run_id},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_cli_company2_without_passwords_exits_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DFIP_LOCAL_DEMO_SEED", "1")
    monkeypatch.setenv("DFIP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/dfip")
    monkeypatch.setenv("DFIP_LOCAL_DEMO_PUBLISHER_PASSWORD", PUBLISHER_PASSWORD)
    monkeypatch.setenv("DFIP_LOCAL_DEMO_CLIENT_PASSWORD", CLIENT_PASSWORD)
    monkeypatch.delenv("DFIP_LOCAL_DEMO_PUBLISHER2_PASSWORD", raising=False)
    monkeypatch.delenv("DFIP_LOCAL_DEMO_CLIENT2_PASSWORD", raising=False)
    code = run_cli(["--confirm-local-only", "--company-2"])
    assert code == 2
    err = capsys.readouterr().err
    assert "DFIP_LOCAL_DEMO_PUBLISHER2_PASSWORD" in err
    assert PUBLISHER_PASSWORD not in err
    assert CLIENT2_PASSWORD not in err


def test_company1_and_company2_logins_resolve_only_own_client() -> None:
    http = TestClient(_memory_app())
    company1 = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD)
    company2 = _login(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD)
    publisher1 = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    publisher2 = _login(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD)
    assert company1.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    assert company1.json()["session"]["role"] == "client"
    assert company2.json()["session"]["client_id"] == COMPANY_2_CLIENT_ID
    assert company2.json()["session"]["role"] == "client"
    assert publisher1.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    assert publisher2.json()["session"]["client_id"] == COMPANY_2_CLIENT_ID
    stolen = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD, COMPANY_2_CLIENT_ID)
    assert stolen.status_code == 403
    reverse = _login(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD, DEFAULT_CLIENT_ID)
    assert reverse.status_code == 403


def test_memory_two_tenant_publication_isolation(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    pub1 = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD))
    pub2 = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
    client1 = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    client2 = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))

    first = _publish_synthetic(
        http, tmp_path, publisher=pub1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
    )
    company1_run = first["publication"]["processing_run_id"]
    second = _publish_synthetic(
        http, tmp_path, publisher=pub2, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_2
    )
    company2_pub = second["publication"]["publication_id"]

    after_company2 = http.get("/api/v1/publications/current", headers=client1)
    assert after_company2.status_code == 200
    assert after_company2.json()["publication"]["processing_run_id"] == company1_run
    assert after_company2.json()["publication"]["client_id"] == DEFAULT_CLIENT_ID

    facts1 = http.get("/api/v1/publications/current/facts", headers=client1)
    facts2 = http.get("/api/v1/publications/current/facts", headers=client2)
    assert facts1.status_code == 200
    assert facts2.status_code == 200
    assert _campaign_ids(facts1.json()) == {CAMP_1}
    assert _campaign_ids(facts2.json()) == {CAMP_2}

    override = http.get(
        "/api/v1/publications/current/facts",
        headers=client1,
        params={"client_id": COMPANY_2_CLIENT_ID},
    )
    assert override.status_code == 403
    history_override = http.get(
        "/api/v1/publications",
        headers=client1,
        params={"client_id": COMPANY_2_CLIENT_ID},
    )
    assert history_override.status_code == 403
    other = http.get(f"/api/v1/publications/{company2_pub}/facts", headers=client1)
    assert other.status_code == 404

    inspect = http.get("/api/v1/facts", headers=client1)
    history = http.get("/api/v1/facts/history", headers=client2)
    assert inspect.status_code == 403
    assert history.status_code == 403
    publish_denied = http.post(
        "/api/v1/publications",
        headers=client1,
        json={"client_id": DEFAULT_CLIENT_ID, "processing_run_id": company1_run},
    )
    steal_publish = http.post(
        "/api/v1/publications",
        headers=client1,
        json={"client_id": COMPANY_2_CLIENT_ID, "processing_run_id": company1_run},
    )
    assert publish_denied.status_code == 403
    assert steal_publish.status_code == 403

    form_override = upload_workbook(
        http,
        workbook_bytes(tmp_path / "steal.xlsx", [source_row(**{"Campaign ID": "camp-steal"})]),
        "steal.xlsx",
        headers=pub1,
        client_id=COMPANY_2_CLIENT_ID,
        wait=False,
    )
    assert form_override.status_code == 403

    report1 = http.get("/api/v1/publications/current/client-report.xlsx", headers=client1)
    report2 = http.get("/api/v1/publications/current/client-report.xlsx", headers=client2)
    steal_report = http.get(
        "/api/v1/publications/current/client-report.xlsx",
        headers=client1,
        params={"client_id": COMPANY_2_CLIENT_ID},
    )
    other_report = http.get(
        f"/api/v1/publications/{company2_pub}/client-report.xlsx",
        headers=client1,
    )
    assert report1.status_code == 200
    assert report2.status_code == 200
    assert steal_report.status_code == 403
    assert other_report.status_code == 404
    assert CLIENT_REPORT_DOWNLOAD_NAME in report1.headers.get("content-disposition", "")
    text1 = _xlsx_xml_text(report1.content)
    text2 = _xlsx_xml_text(report2.content)
    assert CAMP_1 in text1
    assert CAMP_2 not in text1
    assert CAMP_2 in text2
    assert CAMP_1 not in text2
    assert "xl/workbook.xml" in ZipFile(BytesIO(report1.content)).namelist()


@requires_postgres
@postgres_only
def test_postgres_company2_seed_keeps_company1_single_membership(pg_conn) -> None:
    first = seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
    )
    pg_conn.commit()
    missing = pg_conn.execute(
        "SELECT subject FROM app_user WHERE subject = %s",
        (DEMO_CLIENT_2_SUBJECT,),
    ).fetchone()
    assert missing is None
    second = seed_company2_identities(
        pg_conn,
        publisher_password=PUBLISHER2_PASSWORD,
        client_password=CLIENT2_PASSWORD,
        iterations=ITERATIONS,
    )
    pg_conn.commit()
    assert first["client_id"] == DEFAULT_CLIENT_ID
    assert second["client_id"] == COMPANY_2_CLIENT_ID
    assert second["client_code"] == COMPANY_2_CLIENT_CODE
    publisher_memberships = pg_conn.execute(
        """
        SELECT m.client_id::text AS client_id
        FROM client_membership AS m
        JOIN app_user AS u ON u.id = m.user_id
        WHERE u.subject = %s
        ORDER BY m.client_id
        """,
        (DEMO_PUBLISHER_SUBJECT,),
    ).fetchall()
    assert [row["client_id"] for row in publisher_memberships] == [
        DEFAULT_CLIENT_ID,
        COMPANY_2_CLIENT_ID,
    ]
    client_memberships = pg_conn.execute(
        """
        SELECT m.client_id::text AS client_id
        FROM client_membership AS m
        JOIN app_user AS u ON u.id = m.user_id
        WHERE u.subject = %s
        ORDER BY m.client_id
        """,
        (DEMO_CLIENT_SUBJECT,),
    ).fetchall()
    assert [row["client_id"] for row in client_memberships] == [DEFAULT_CLIENT_ID]


@requires_postgres
@postgres_only
def test_postgres_two_tenant_publication_isolation(
    pg_conn, postgres_url: str, tmp_path: Path
) -> None:
    seed_demo_identities(
        pg_conn,
        publisher_password=PUBLISHER_PASSWORD,
        client_password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
    )
    seed_company2_identities(
        pg_conn,
        publisher_password=PUBLISHER2_PASSWORD,
        client_password=CLIENT2_PASSWORD,
        iterations=ITERATIONS,
    )
    pg_conn.commit()

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
        pub1 = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID))
        pub2 = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
        client1 = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
        client2 = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))

        session1 = http.get("/api/v1/session", headers=client1)
        session2 = http.get("/api/v1/session", headers=client2)
        assert session1.json()["client_id"] == DEFAULT_CLIENT_ID
        assert session2.json()["client_id"] == COMPANY_2_CLIENT_ID

        first = _publish_synthetic(
            http, tmp_path, publisher=pub1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
        )
        company1_pub = first["publication"]["publication_id"]
        company1_run = first["publication"]["processing_run_id"]
        second = _publish_synthetic(
            http, tmp_path, publisher=pub2, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_2
        )
        company2_pub = second["publication"]["publication_id"]
        company2_run = second["publication"]["processing_run_id"]

        current1 = http.get("/api/v1/publications/current", headers=client1)
        current2 = http.get("/api/v1/publications/current", headers=client2)
        assert current1.json()["publication"]["publication_id"] == company1_pub
        assert current1.json()["publication"]["processing_run_id"] == company1_run
        assert current2.json()["publication"]["publication_id"] == company2_pub
        assert current2.json()["publication"]["processing_run_id"] == company2_run

        facts1 = http.get("/api/v1/publications/current/facts", headers=client1)
        facts2 = http.get("/api/v1/publications/current/facts", headers=client2)
        assert _campaign_ids(facts1.json()) == {CAMP_1}
        assert _campaign_ids(facts2.json()) == {CAMP_2}

        history1 = http.get("/api/v1/publications", headers=client1)
        history2 = http.get("/api/v1/publications", headers=client2)
        assert {item["publication_id"] for item in history1.json()["items"]} == {company1_pub}
        assert {item["publication_id"] for item in history2.json()["items"]} == {company2_pub}

        assert http.get("/api/v1/facts", headers=client1).status_code == 403
        assert http.get("/api/v1/facts/history", headers=client2).status_code == 403
        assert (
            http.post(
                "/api/v1/publications",
                headers=client2,
                json={"client_id": COMPANY_2_CLIENT_ID, "processing_run_id": company2_run},
            ).status_code
            == 403
        )
        assert (
            http.get(
                "/api/v1/publications/current/facts",
                headers=client1,
                params={"client_id": COMPANY_2_CLIENT_ID},
            ).status_code
            == 403
        )
        assert (
            http.get(f"/api/v1/publications/{company2_pub}/facts", headers=client1).status_code
            == 404
        )
        report1 = http.get("/api/v1/publications/current/client-report.xlsx", headers=client1)
        report2 = http.get("/api/v1/publications/current/client-report.xlsx", headers=client2)
        assert report1.status_code == 200
        assert report2.status_code == 200
        text1 = _xlsx_xml_text(report1.content)
        text2 = _xlsx_xml_text(report2.content)
        assert CAMP_1 in text1
        assert CAMP_2 not in text1
        assert CAMP_2 in text2
        assert CAMP_1 not in text2
        assert (
            http.get(
                f"/api/v1/publications/{company2_pub}/client-report.xlsx",
                headers=client1,
            ).status_code
            == 404
        )

    with connect(postgres_url, row_factory=dict_row) as conn:
        pointers = conn.execute(
            """
            SELECT
                pc.client_id::text AS client_id,
                p.processing_run_id::text AS run_id
            FROM publication_current AS pc
            JOIN publication AS p ON p.id = pc.publication_id
            ORDER BY pc.client_id
            """
        ).fetchall()
        by_client = {row["client_id"]: row["run_id"] for row in pointers}
        assert by_client[DEFAULT_CLIENT_ID] == company1_run
        assert by_client[COMPANY_2_CLIENT_ID] == company2_run

        conn.execute("BEGIN")
        conn.execute("SET LOCAL ROLE dfip_api")
        conn.execute("SELECT set_config('dfip.role', 'client', true)")
        conn.execute("SELECT set_config('dfip.client_ids', %s, true)", (DEFAULT_CLIENT_ID,))
        conn.execute("SELECT set_config('dfip.platform_admin', 'false', true)")
        current_under_rls = conn.execute(
            "SELECT client_id::text AS client_id FROM publication_current"
        ).fetchall()
        facts_under_rls = conn.execute(
            "SELECT DISTINCT campaign_id FROM publication_fact"
        ).fetchall()
        conn.execute("ROLLBACK")
    assert {row["client_id"] for row in current_under_rls} == {DEFAULT_CLIENT_ID}
    assert {row["campaign_id"] for row in facts_under_rls} == {CAMP_1}
    assert os.environ.get("DFIP_TEST_DATABASE_URL", "")
    assert "supabase" not in os.environ.get("DFIP_TEST_DATABASE_URL", "").lower()
