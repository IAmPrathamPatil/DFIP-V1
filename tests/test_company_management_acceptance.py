"""RUN 004B-4: end-to-end company-management acceptance.

Disposable PostgreSQL only for postgres-marked cases. Never a live URI.
Operator-chosen usernames/passwords are test fixtures, not product generators.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from dfip_api.app import create_app
from dfip_api.client_directory import InMemoryClientDirectory
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.local_demo_seed import seed_company2_identities, seed_demo_identities
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_db.local_demo_guard import (
    COMPANY_2_CLIENT_ID,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
)
from dfip_web.client_report_download import CLIENT_REPORT_DOWNLOAD_NAME
from fastapi.testclient import TestClient

from http_ingest_support import source_row, upload_workbook, workbook_bytes
from postgres_support import postgres_only, requires_postgres
from test_company_registry import (
    CLIENT2_PASSWORD,
    CLIENT_PASSWORD,
    ITERATIONS,
    PUBLISHER2_PASSWORD,
    PUBLISHER_PASSWORD,
    WEB_STATIC,
    _bearer,
    _memory_app,
    _publish_synthetic,
    _select,
    _token,
)
from test_p5_api import JWT_SECRET, make_settings
from test_p9_authz import _error

CREATED = "DFIP-004B4 synthetic tenant"
RENAMED = "DFIP-004B4 synthetic tenant renamed"
CAMP_1 = "camp-004b4-co1"
CAMP_2 = "camp-004b4-co2"
CAMP_NEW = "camp-004b4-new"
ALPHA_USER = "alpha-portal-user"
ALPHA_PASS = "alpha-portal-pass"
GAMMA_USER = "gamma-portal-user"
GAMMA_PASS = "gamma-portal-pass"
OPERATOR_USER = "ops.publisher"
OPERATOR_PASS = "ops-publisher-pass"


def _users_url(client_id: str) -> str:
    return f"/api/v1/clients/{client_id}/users"


def _user_body(
    username: str, password: str, *, role: str = "client", confirm: str | None = None
) -> dict[str, str]:
    return {
        "username": username,
        "password": password,
        "confirm_password": password if confirm is None else confirm,
        "role": role,
    }


def _setup_app():
    directory = InMemoryClientDirectory()
    directory.ensure(DEFAULT_CLIENT_ID, code="default", name="Alpha Co")
    directory.ensure(COMPANY_2_CLIENT_ID, code="company-2", name="Beta Co")
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
        ),
        identity_store=InMemoryIdentityStore(),
        client_directory=directory,
        publication_store=InMemoryPublicationStore(),
    )


def _assert_no_secret(response, password: str) -> None:
    assert password not in response.text
    body = response.json()
    assert "password" not in body
    assert "password_hash" not in body


def test_operator_publisher_setup_uses_chosen_credentials() -> None:
    http = TestClient(_setup_app())
    status = http.get("/api/v1/auth/setup-status")
    assert status.status_code == 200, status.text
    assert status.json() == {"publisher_setup_required": True}
    mismatch = http.post(
        "/api/v1/auth/setup-publisher",
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PASS,
            "confirm_password": "different-pass",
        },
    )
    assert mismatch.status_code == 422
    assert OPERATOR_PASS not in mismatch.text
    assert "different-pass" not in mismatch.text
    created = http.post(
        "/api/v1/auth/setup-publisher",
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PASS,
            "confirm_password": OPERATOR_PASS,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == OPERATOR_USER
    assert body["role"] == "publisher"
    _assert_no_secret(created, OPERATOR_PASS)
    assert http.get("/api/v1/auth/setup-status").json() == {"publisher_setup_required": False}
    duplicate = http.post(
        "/api/v1/auth/setup-publisher",
        json={
            "username": "second.publisher",
            "password": OPERATOR_PASS,
            "confirm_password": OPERATOR_PASS,
        },
    )
    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == "CONFLICT"
    login = http.post(
        "/api/v1/auth/login",
        json={"username": OPERATOR_USER, "password": OPERATOR_PASS},
    )
    assert login.status_code == 200, login.text
    session = login.json()["session"]
    assert session["subject"] == OPERATOR_USER
    assert session["role"] == "publisher"
    assert session["client_id"] is None
    ids = {item["client_id"] for item in session["clients"]}
    assert ids == {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
    token = login.json()["access_token"]
    company1 = _select(http, token, DEFAULT_CLIENT_ID)
    assert company1.status_code == 200
    assert company1.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    company2 = _select(http, company1.json()["access_token"], COMPANY_2_CLIENT_ID)
    assert company2.status_code == 200
    assert company2.json()["session"]["client_id"] == COMPANY_2_CLIENT_ID
    _assert_no_secret(login, OPERATOR_PASS)


def test_seeded_environment_does_not_offer_product_publisher_setup() -> None:
    http = TestClient(_memory_app())
    assert http.get("/api/v1/auth/setup-status").json() == {"publisher_setup_required": False}
    refused = http.post(
        "/api/v1/auth/setup-publisher",
        json={
            "username": OPERATOR_USER,
            "password": OPERATOR_PASS,
            "confirm_password": OPERATOR_PASS,
        },
    )
    assert refused.status_code == 409


def test_client_password_confirmation_must_match() -> None:
    http = TestClient(_memory_app())
    publisher = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID))
    mismatch = http.post(
        _users_url(DEFAULT_CLIENT_ID),
        headers=publisher,
        json=_user_body(ALPHA_USER, ALPHA_PASS, confirm="other-pass"),
    )
    assert mismatch.status_code == 422
    assert ALPHA_PASS not in mismatch.text
    assert "other-pass" not in mismatch.text


def test_publisher_provisions_operator_chosen_client_login() -> None:
    http = TestClient(_memory_app())
    publisher = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID))
    created = http.post(
        _users_url(DEFAULT_CLIENT_ID),
        headers=publisher,
        json=_user_body(ALPHA_USER, ALPHA_PASS),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == ALPHA_USER
    assert body["client_id"] == DEFAULT_CLIENT_ID
    assert body["role"] == "client"
    _assert_no_secret(created, ALPHA_PASS)

    session = http.post("/api/v1/auth/login", json={"username": ALPHA_USER, "password": ALPHA_PASS})
    assert session.status_code == 200, session.text
    assert session.json()["session"]["role"] == "client"
    assert session.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    assert session.json()["session"]["subject"] == ALPHA_USER
    assert "needsCompanySelection" not in session.text

    duplicate = http.post(
        _users_url(DEFAULT_CLIENT_ID),
        headers=publisher,
        json=_user_body(ALPHA_USER, ALPHA_PASS),
    )
    assert duplicate.status_code == 422
    assert _error(duplicate)["code"] == "VALIDATION_ERROR"
    publisher_role = http.post(
        _users_url(DEFAULT_CLIENT_ID),
        headers=publisher,
        json=_user_body("not-a-publisher", ALPHA_PASS, role="publisher"),
    )
    assert publisher_role.status_code == 422


def test_client_login_cannot_manage_companies_or_inspect() -> None:
    http = TestClient(_memory_app())
    publisher = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID))
    http.post(
        _users_url(DEFAULT_CLIENT_ID),
        headers=publisher,
        json=_user_body(ALPHA_USER, ALPHA_PASS),
    )
    client = _bearer(_token(http, ALPHA_USER, ALPHA_PASS))
    assert http.get("/api/v1/clients", headers=client).status_code == 403
    assert http.post("/api/v1/clients", headers=client, json={"name": CREATED}).status_code == 403
    assert (
        http.post(
            f"/api/v1/clients/{DEFAULT_CLIENT_ID}/rename",
            headers=client,
            json={"name": "Nope"},
        ).status_code
        == 403
    )
    assert (
        http.post(
            _users_url(DEFAULT_CLIENT_ID),
            headers=client,
            json=_user_body("other-client", ALPHA_PASS),
        ).status_code
        == 403
    )
    assert (
        http.post(
            "/api/v1/auth/select-client",
            headers=client,
            json={"client_id": DEFAULT_CLIENT_ID},
        ).status_code
        == 403
    )
    assert http.get("/api/v1/facts", headers=client).status_code == 403
    assert (
        http.post(
            "/api/v1/publications",
            headers=client,
            json={"client_id": DEFAULT_CLIENT_ID, "processing_run_id": DEFAULT_CLIENT_ID},
        ).status_code
        == 403
    )
    steal = http.post(
        "/api/v1/auth/login",
        json={"username": ALPHA_USER, "password": ALPHA_PASS, "client_id": COMPANY_2_CLIENT_ID},
    )
    assert steal.status_code == 403


def test_provision_denied_for_unauthorized_company_and_other_publisher() -> None:
    http = TestClient(_memory_app())
    other = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
    denied = http.post(
        _users_url(DEFAULT_CLIENT_ID),
        headers=other,
        json=_user_body(ALPHA_USER, ALPHA_PASS),
    )
    assert denied.status_code == 403
    client = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    assert (
        http.post(
            _users_url(DEFAULT_CLIENT_ID),
            headers=client,
            json=_user_body(ALPHA_USER, ALPHA_PASS),
        ).status_code
        == 403
    )


def test_existing_company_clients_stay_isolated_after_provision(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    pub1 = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, DEFAULT_CLIENT_ID))
    pub2 = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
    _publish_synthetic(
        http, tmp_path, publisher=pub1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
    )
    _publish_synthetic(
        http, tmp_path, publisher=pub2, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_2
    )
    http.post(
        _users_url(DEFAULT_CLIENT_ID),
        headers=pub1,
        json=_user_body(ALPHA_USER, ALPHA_PASS),
    )
    seeded = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    provisioned = _bearer(_token(http, ALPHA_USER, ALPHA_PASS))
    company2 = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
    for headers in (seeded, provisioned):
        facts = http.get("/api/v1/publications/current/facts", headers=headers)
        assert facts.status_code == 200
        assert {item["campaign_id"] for item in facts.json()["items"]} == {CAMP_1}
        assert (
            http.get(
                "/api/v1/publications/current/facts",
                headers=headers,
                params={"client_id": COMPANY_2_CLIENT_ID},
            ).status_code
            == 403
        )
    facts2 = http.get("/api/v1/publications/current/facts", headers=company2)
    assert {item["campaign_id"] for item in facts2.json()["items"]} == {CAMP_2}


def test_spa_exposes_operator_client_provisioning() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    assert "data-company-client-form" in views
    assert "Create Client Account" in views
    assert "minlength=\"12\"" in views
    assert "at least 12 characters" in views.lower()
    assert "data-publisher-setup-form" in views
    assert "Publisher username" in views
    assert "Publisher password" in views
    assert "Client username" in views
    assert "Client password" in views
    assert "demo-publisher" not in views
    for marker in (
        "data-publisher-setup-username",
        "data-publisher-setup-password",
        "data-publisher-setup-confirm",
        "data-company-client-username",
        "data-company-client-password",
        "data-company-client-confirm",
    ):
        assert marker in views
        prefix = views.split(marker, 1)[0].rsplit("<input", 1)[-1]
        assert "value=" not in prefix
    assert "createClientUser" in client_js
    assert "setupPublisher" in client_js
    assert "setupStatus" in client_js
    assert "companyClientForm" in app_js
    assert "publisherSetupForm" in app_js
    assert "password hash" in views.lower() or "password hash" in app_js.lower()


def _xlsx_has(content: bytes, needle: str) -> bool:
    parts: list[str] = []
    with ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.endswith(".xml"):
                parts.append(archive.read(name).decode("utf-8", errors="ignore"))
    return needle in "\n".join(parts)


@requires_postgres
@postgres_only
def test_postgres_new_company_client_ready_e2e(pg_conn, postgres_url: str, tmp_path: Path) -> None:
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
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
        )
    )
    with TestClient(app) as http:
        unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
        company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
        first = _publish_synthetic(
            http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
        )
        company2 = _bearer(
            _select(http, company1["Authorization"].split(" ", 1)[1], COMPANY_2_CLIENT_ID).json()[
                "access_token"
            ]
        )
        second = _publish_synthetic(
            http, tmp_path, publisher=company2, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_2
        )
        renamed = http.post(
            f"/api/v1/clients/{COMPANY_2_CLIENT_ID}/rename",
            headers=company2,
            json={"name": "Beta Co renamed"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["client_id"] == COMPANY_2_CLIENT_ID
        created = http.post("/api/v1/clients", headers=company2, json={"name": CREATED})
        assert created.status_code == 201, created.text
        new_id = created.json()["client_id"]
        assert new_id not in {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
        assert created.json()["code"] == new_id
        selected = _select(http, company2["Authorization"].split(" ", 1)[1], new_id)
        assert selected.status_code == 200, selected.text
        assert selected.json()["session"]["client_id"] == new_id
        operator = _bearer(selected.json()["access_token"])
        provisioned = http.post(
            _users_url(new_id),
            headers=operator,
            json=_user_body(GAMMA_USER, GAMMA_PASS),
        )
        assert provisioned.status_code == 201, provisioned.text
        _assert_no_secret(provisioned, GAMMA_PASS)
        uploaded = upload_workbook(
            http,
            workbook_bytes(tmp_path / "new.xlsx", [source_row(**{"Campaign ID": CAMP_NEW})]),
            "new.xlsx",
            headers=operator,
            client_id=new_id,
        )
        assert uploaded.status_code in {200, 201}, uploaded.text
        body = uploaded.json()
        assert body["published"] is False
        run = body["processing_run"]
        assert run["qa_verdict"] in {"pass", "warn"}
        assert run["campaign_label_version_id"] is None
        assert run["rate_card_version_id"] is None
        published = http.post(
            "/api/v1/publications",
            headers=operator,
            json={"client_id": new_id, "processing_run_id": run["processing_run_id"]},
        )
        assert published.status_code == 201, published.text
        gamma = _bearer(_token(http, GAMMA_USER, GAMMA_PASS))
        assert gamma
        facts = http.get("/api/v1/publications/current/facts", headers=gamma)
        assert facts.status_code == 200, facts.text
        assert {item["campaign_id"] for item in facts.json()["items"]} == {CAMP_NEW}
        report = http.get("/api/v1/publications/current/client-report.xlsx", headers=gamma)
        assert report.status_code == 200
        assert CLIENT_REPORT_DOWNLOAD_NAME in report.headers.get("content-disposition", "")
        assert _xlsx_has(report.content, CAMP_NEW)
        assert CAMP_1 not in {item["campaign_id"] for item in facts.json()["items"]}
        alpha = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
        beta = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
        assert {
            item["campaign_id"]
            for item in http.get("/api/v1/publications/current/facts", headers=alpha).json()[
                "items"
            ]
        } == {CAMP_1}
        assert {
            item["campaign_id"]
            for item in http.get("/api/v1/publications/current/facts", headers=beta).json()["items"]
        } == {CAMP_2}
        assert (
            http.get(
                "/api/v1/publications/current/facts",
                headers=gamma,
                params={"client_id": DEFAULT_CLIENT_ID},
            ).status_code
            == 403
        )
        assert http.get("/api/v1/clients", headers=gamma).status_code == 403
        assert (
            http.post(
                "/api/v1/auth/select-client", headers=gamma, json={"client_id": new_id}
            ).status_code
            == 403
        )
        pointers = {
            row["client_id"]: row["publication_id"]
            for row in pg_conn.execute(
                """
                SELECT client_id::text AS client_id, publication_id::text AS publication_id
                FROM publication_current
                ORDER BY client_id
                """
            ).fetchall()
        }
        assert pointers[DEFAULT_CLIENT_ID] == first["publication"]["publication_id"]
        assert pointers[COMPANY_2_CLIENT_ID] == second["publication"]["publication_id"]
        assert new_id in pointers
        membership = pg_conn.execute(
            """
            SELECT m.role, m.client_id::text AS client_id, u.password_hash
            FROM client_membership AS m
            JOIN app_user AS u ON u.id = m.user_id
            WHERE u.subject = %s
            """,
            (GAMMA_USER,),
        ).fetchone()
        assert membership["role"] == "client"
        assert membership["client_id"] == new_id
        assert membership["password_hash"]
        assert GAMMA_PASS not in membership["password_hash"]
        renamed_row = pg_conn.execute(
            "SELECT id::text AS client_id, code, name FROM client WHERE id = %s",
            (COMPANY_2_CLIENT_ID,),
        ).fetchone()
        assert renamed_row["client_id"] == COMPANY_2_CLIENT_ID
        assert renamed_row["name"] == "Beta Co renamed"


@requires_postgres
@postgres_only
def test_postgres_operator_publisher_setup_one_account(pg_conn, postgres_url: str) -> None:
    app = create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
        )
    )
    with TestClient(app) as http:
        status = http.get("/api/v1/auth/setup-status")
        assert status.status_code == 200, status.text
        assert status.json()["publisher_setup_required"] is True
        created = http.post(
            "/api/v1/auth/setup-publisher",
            json={
                "username": OPERATOR_USER,
                "password": OPERATOR_PASS,
                "confirm_password": OPERATOR_PASS,
            },
        )
        assert created.status_code == 201, created.text
        _assert_no_secret(created, OPERATOR_PASS)
        login = http.post(
            "/api/v1/auth/login",
            json={"username": OPERATOR_USER, "password": OPERATOR_PASS},
        )
        assert login.status_code == 200, login.text
        _assert_no_secret(login, OPERATOR_PASS)
        token = login.json()["access_token"]
        assert _select(http, token, DEFAULT_CLIENT_ID).status_code == 200
        assert _select(http, token, COMPANY_2_CLIENT_ID).status_code == 200
        refused = http.post(
            "/api/v1/auth/setup-publisher",
            json={
                "username": "another.ops",
                "password": OPERATOR_PASS,
                "confirm_password": OPERATOR_PASS,
            },
        )
        assert refused.status_code == 409
        assert _error(refused)["code"] == "CONFLICT"
    inspectors = pg_conn.execute(
        """
        SELECT u.subject, u.password_hash, COUNT(m.client_id) AS companies
        FROM app_user AS u
        JOIN client_membership AS m ON m.user_id = u.id
        WHERE m.role IN ('publisher', 'admin')
        GROUP BY u.subject, u.password_hash
        """
    ).fetchall()
    assert len(inspectors) == 1
    assert inspectors[0]["subject"] == OPERATOR_USER
    assert str(inspectors[0]["password_hash"]).startswith("pbkdf2_sha256$")
    assert OPERATOR_PASS not in str(inspectors[0]["password_hash"])
    assert int(inspectors[0]["companies"]) >= 2
