"""RUN 004B-1: generic company registry and display-name rename.

Does not open hosted databases. PostgreSQL cases require DFIP_TEST_DATABASE_URL
and drop/recreate ``public`` via existing fixtures — never a live URI.
Does not create Company 3. Does not add a company. Does not change Excel.
"""

from __future__ import annotations

from pathlib import Path

from dfip_api.app import create_app
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.local_demo_seed import seed_company2_identities, seed_demo_identities
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_db.identity import MembershipRow
from dfip_db.local_demo_guard import (
    COMPANY_2_CLIENT_ID,
    COMPANY_2_CLIENT_NAME,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
)
from fastapi.testclient import TestClient

from http_ingest_support import source_row, upload_workbook, workbook_bytes
from postgres_support import postgres_only, requires_postgres
from test_p5_api import JWT_SECRET, make_settings
from test_p9_authz import _error

PUBLISHER_PASSWORD = "local-publisher-pass"
CLIENT_PASSWORD = "local-client-pass"
PUBLISHER2_PASSWORD = "local-publisher2-pass"
CLIENT2_PASSWORD = "local-client2-pass"
READER_PASSWORD = "local-reader-pass"
SOLO_PASSWORD = "local-solo-pass"
ITERATIONS = 1000
UNAUTHORIZED_CLIENT_ID = "a0000000-0000-4000-8000-000000000099"
RENAMED = "Eureka Forbes India"
CAMP_1 = "camp-reg-1"
CAMP_2 = "camp-reg-2"
SOLO_PUBLISHER = "solo-publisher"
READER_SUBJECT = "demo-reader"
ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"


def _identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.put_password_user(
        subject=DEMO_PUBLISHER_SUBJECT,
        password=PUBLISHER_PASSWORD,
        iterations=ITERATIONS,
        memberships=(
            MembershipRow(DEFAULT_CLIENT_ID, "publisher", code="default", name="Alpha Co"),
            MembershipRow(COMPANY_2_CLIENT_ID, "publisher", code="company-2", name="Beta Co"),
        ),
    )
    store.put_password_user(
        subject=DEMO_CLIENT_SUBJECT,
        password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(DEFAULT_CLIENT_ID, "client", code="default", name="Alpha Co"),),
    )
    store.put_password_user(
        subject=DEMO_PUBLISHER_2_SUBJECT,
        password=PUBLISHER2_PASSWORD,
        iterations=ITERATIONS,
        memberships=(
            MembershipRow(COMPANY_2_CLIENT_ID, "publisher", code="company-2", name="Beta Co"),
        ),
    )
    store.put_password_user(
        subject=DEMO_CLIENT_2_SUBJECT,
        password=CLIENT2_PASSWORD,
        iterations=ITERATIONS,
        memberships=(
            MembershipRow(COMPANY_2_CLIENT_ID, "client", code="company-2", name="Beta Co"),
        ),
    )
    store.put_password_user(
        subject=SOLO_PUBLISHER,
        password=SOLO_PASSWORD,
        iterations=ITERATIONS,
        memberships=(
            MembershipRow(DEFAULT_CLIENT_ID, "publisher", code="default", name="Alpha Co"),
        ),
    )
    store.put_password_user(
        subject=READER_SUBJECT,
        password=READER_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(DEFAULT_CLIENT_ID, "reader", code="default", name="Alpha Co"),),
    )
    return store


def _memory_app():
    return create_app(
        settings=make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
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


def _select(http: TestClient, token: str, client_id: str):
    return http.post(
        "/api/v1/auth/select-client",
        headers=_bearer(token),
        json={"client_id": client_id},
    )


def _ids(payload: dict) -> set[str]:
    return {str(item.get("client_id")) for item in payload.get("items") or []}


def _names(payload: dict) -> dict[str, str]:
    return {str(item["client_id"]): str(item["name"]) for item in payload.get("items") or []}


def _campaign_ids(payload: dict) -> set[str]:
    return {str(item.get("campaign_id")) for item in payload.get("items") or []}


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
    run_id = uploaded.json()["processing_run"]["processing_run_id"]
    created = http.post(
        "/api/v1/publications",
        headers=publisher,
        json={"client_id": client_id, "processing_run_id": run_id},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_publisher_lists_authorized_companies() -> None:
    http = TestClient(_memory_app())
    headers = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD))
    response = http.get("/api/v1/clients", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert _ids(body) == {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
    names = _names(body)
    assert names[DEFAULT_CLIENT_ID] == "Alpha Co"
    assert names[COMPANY_2_CLIENT_ID] == "Beta Co"
    for item in body["items"]:
        assert item["client_id"] in {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
        assert "code" in item
        assert item["name"]


def test_client_and_reader_cannot_list_or_rename_registry() -> None:
    http = TestClient(_memory_app())
    client = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    reader = _bearer(_token(http, READER_SUBJECT, READER_PASSWORD))
    for headers in (client, reader):
        listed = http.get("/api/v1/clients", headers=headers)
        renamed = http.post(
            f"/api/v1/clients/{DEFAULT_CLIENT_ID}/rename",
            headers=headers,
            json={"name": RENAMED},
        )
        assert listed.status_code == 403
        assert renamed.status_code == 403
        assert _error(listed)["code"] == "AUTHORIZATION_FAILED"
        assert _error(renamed)["code"] == "AUTHORIZATION_FAILED"


def test_publisher_renames_display_name_without_changing_client_id(tmp_path: Path) -> None:
    http = TestClient(_memory_app())
    unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    published = _publish_synthetic(
        http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
    )
    publication_id = published["publication"]["publication_id"]
    before = http.get("/api/v1/publications/current", headers=company1).json()

    renamed = http.post(
        f"/api/v1/clients/{DEFAULT_CLIENT_ID}/rename",
        headers=company1,
        json={"name": RENAMED},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["client_id"] == DEFAULT_CLIENT_ID
    assert renamed.json()["code"] == "default"
    assert renamed.json()["name"] == RENAMED

    registry = http.get("/api/v1/clients", headers=company1).json()
    names = _names(registry)
    assert names[DEFAULT_CLIENT_ID] == RENAMED
    assert names[COMPANY_2_CLIENT_ID] == "Beta Co"
    assert _ids(registry) == {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}

    session = http.get("/api/v1/session", headers=company1).json()
    assert session["client_id"] == DEFAULT_CLIENT_ID
    session_names = {item["client_id"]: item["name"] for item in session["clients"]}
    assert session_names[DEFAULT_CLIENT_ID] == RENAMED

    after = http.get("/api/v1/publications/current", headers=company1).json()
    assert after["publication"]["client_id"] == DEFAULT_CLIENT_ID
    assert after["publication"]["publication_id"] == publication_id
    assert after["publication"]["publication_id"] == before["publication"]["publication_id"]
    facts = http.get("/api/v1/publications/current/facts", headers=company1)
    assert _campaign_ids(facts.json()) == {CAMP_1}

    switched = _select(http, company1["Authorization"].split(" ", 1)[1], COMPANY_2_CLIENT_ID)
    assert switched.status_code == 200
    assert switched.json()["session"]["client_id"] == COMPANY_2_CLIENT_ID
    back = _select(http, switched.json()["access_token"], DEFAULT_CLIENT_ID)
    assert back.status_code == 200
    assert back.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    current = http.get(
        "/api/v1/publications/current", headers=_bearer(back.json()["access_token"])
    )
    assert current.json()["publication"]["publication_id"] == publication_id

    client = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    client_facts = http.get("/api/v1/publications/current/facts", headers=client)
    assert client_facts.status_code == 200
    assert _campaign_ids(client_facts.json()) == {CAMP_1}


def test_cannot_rename_unauthorized_or_foreign_company() -> None:
    http = TestClient(_memory_app())
    solo = _bearer(_token(http, SOLO_PUBLISHER, SOLO_PASSWORD))
    foreign = http.post(
        f"/api/v1/clients/{COMPANY_2_CLIENT_ID}/rename",
        headers=solo,
        json={"name": RENAMED},
    )
    missing = http.post(
        f"/api/v1/clients/{UNAUTHORIZED_CLIENT_ID}/rename",
        headers=solo,
        json={"name": RENAMED},
    )
    assert foreign.status_code == 403
    assert missing.status_code == 403
    listed = http.get("/api/v1/clients", headers=solo).json()
    assert _ids(listed) == {DEFAULT_CLIENT_ID}
    assert _names(listed)[DEFAULT_CLIENT_ID] == "Alpha Co"


def test_spa_company_registry_is_publisher_only() -> None:
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    components = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    assert "admin-companies" in app_js
    assert "companiesView" in views
    assert "data-company-registry" in views
    assert "data-company-rename-form" in views
    assert "/admin/companies" in components
    assert "listClients" in client_js
    assert "/clients/" in client_js
    assert "renameClient" in client_js
    assert "createClient" in client_js
    assert "createClientUser" in client_js
    assert "Add Company" in views
    assert "data-company-create-form" in views
    assert "data-company-onboarding" in views
    assert "data-company-client-form" in views


@requires_postgres
@postgres_only
def test_postgres_rename_keeps_publication_and_other_tenant(
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
            dfip_auth_secret=JWT_SECRET,
            database_url=postgres_url,
            dfip_password_pbkdf2_iterations=ITERATIONS,
            dfip_env="test",
        )
    )
    with TestClient(app) as http:
        unbound = _token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
        listed = http.get("/api/v1/clients", headers=_bearer(unbound))
        assert listed.status_code == 200
        assert _ids(listed.json()) == {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
        original_two = _names(listed.json())[COMPANY_2_CLIENT_ID]
        assert original_two == COMPANY_2_CLIENT_NAME

        company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
        first = _publish_synthetic(
            http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
        )
        token1 = company1["Authorization"].split(" ", 1)[1]
        company2 = _bearer(_select(http, token1, COMPANY_2_CLIENT_ID).json()["access_token"])
        second = _publish_synthetic(
            http, tmp_path, publisher=company2, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_2
        )

        renamed = http.post(
            f"/api/v1/clients/{DEFAULT_CLIENT_ID}/rename",
            headers=company2,
            json={"name": RENAMED},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["client_id"] == DEFAULT_CLIENT_ID
        code_row = pg_conn.execute(
            "SELECT id::text AS client_id, code, name FROM client WHERE id = %s",
            (DEFAULT_CLIENT_ID,),
        ).fetchone()
        assert code_row["client_id"] == DEFAULT_CLIENT_ID
        assert code_row["code"] == "default"
        assert code_row["name"] == RENAMED
        other = pg_conn.execute(
            "SELECT name FROM client WHERE id = %s",
            (COMPANY_2_CLIENT_ID,),
        ).fetchone()
        assert other["name"] == COMPANY_2_CLIENT_NAME

        registry = http.get("/api/v1/clients", headers=company2).json()
        assert _names(registry)[DEFAULT_CLIENT_ID] == RENAMED
        assert _names(registry)[COMPANY_2_CLIENT_ID] == COMPANY_2_CLIENT_NAME

        bound1 = _bearer(
            _select(
                http, company2["Authorization"].split(" ", 1)[1], DEFAULT_CLIENT_ID
            ).json()["access_token"]
        )
        current1 = http.get("/api/v1/publications/current", headers=bound1)
        assert current1.json()["publication"]["client_id"] == DEFAULT_CLIENT_ID
        assert current1.json()["publication"]["publication_id"] == first["publication"][
            "publication_id"
        ]
        facts1 = http.get("/api/v1/publications/current/facts", headers=bound1)
        assert _campaign_ids(facts1.json()) == {CAMP_1}

        bound2 = _bearer(
            _select(http, bound1["Authorization"].split(" ", 1)[1], COMPANY_2_CLIENT_ID).json()[
                "access_token"
            ]
        )
        current2 = http.get("/api/v1/publications/current", headers=bound2)
        assert current2.json()["publication"]["client_id"] == COMPANY_2_CLIENT_ID
        assert current2.json()["publication"]["publication_id"] == second["publication"][
            "publication_id"
        ]

        client1 = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
        client2 = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
        assert http.get("/api/v1/clients", headers=client1).status_code == 403
        assert _campaign_ids(
            http.get("/api/v1/publications/current/facts", headers=client1).json()
        ) == {CAMP_1}
        assert _campaign_ids(
            http.get("/api/v1/publications/current/facts", headers=client2).json()
        ) == {CAMP_2}
