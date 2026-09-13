"""RUN 004B-2: generic Add Company / tenant creation.

Does not open hosted databases. PostgreSQL cases require DFIP_TEST_DATABASE_URL
and drop/recreate ``public`` via existing fixtures — never a live URI.
Does not hard-code a third company id. Does not onboard catalogs or Excel.
"""

from __future__ import annotations

from uuid import UUID

from dfip_api.app import create_app
from dfip_api.local_demo_seed import seed_company2_identities, seed_demo_identities
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

from postgres_support import postgres_only, requires_postgres
from test_company_registry import (
    CAMP_1,
    CAMP_2,
    CLIENT2_PASSWORD,
    CLIENT_PASSWORD,
    ITERATIONS,
    PUBLISHER2_PASSWORD,
    PUBLISHER_PASSWORD,
    READER_PASSWORD,
    READER_SUBJECT,
    SOLO_PASSWORD,
    SOLO_PUBLISHER,
    WEB_STATIC,
    _bearer,
    _ids,
    _memory_app,
    _names,
    _publish_synthetic,
    _select,
    _token,
)
from test_p5_api import JWT_SECRET, make_settings
from test_p9_authz import _error

CREATED = "DFIP-004B2 synthetic tenant"
CREATED_DUP = "Alpha Co"
CREATED_RENAME = "DFIP-004B2 synthetic tenant renamed"


def test_publisher_creates_company_with_new_immutable_id() -> None:
    http = TestClient(_memory_app())
    headers = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD))
    created = http.post("/api/v1/clients", headers=headers, json={"name": CREATED})
    assert created.status_code == 201, created.text
    body = created.json()
    new_id = body["client_id"]
    UUID(new_id)
    assert new_id not in {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
    assert body["code"] == new_id
    assert body["name"] == CREATED

    listed = http.get("/api/v1/clients", headers=headers)
    assert listed.status_code == 200
    names = _names(listed.json())
    assert names[DEFAULT_CLIENT_ID] == "Alpha Co"
    assert names[COMPANY_2_CLIENT_ID] == "Beta Co"
    assert names[new_id] == CREATED
    assert _ids(listed.json()) == {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID, new_id}

    selected = _select(http, headers["Authorization"].split(" ", 1)[1], new_id)
    assert selected.status_code == 200, selected.text
    session = selected.json()["session"]
    assert session["client_id"] == new_id
    session_ids = {item["client_id"] for item in session["clients"]}
    assert new_id in session_ids

    renamed = http.post(
        f"/api/v1/clients/{new_id}/rename",
        headers=_bearer(selected.json()["access_token"]),
        json={"name": CREATED_RENAME},
    )
    assert renamed.status_code == 200
    assert renamed.json()["client_id"] == new_id
    assert renamed.json()["code"] == new_id
    assert renamed.json()["name"] == CREATED_RENAME


def test_client_and_reader_cannot_create_company() -> None:
    http = TestClient(_memory_app())
    client = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
    reader = _bearer(_token(http, READER_SUBJECT, READER_PASSWORD))
    for headers in (client, reader):
        created = http.post("/api/v1/clients", headers=headers, json={"name": CREATED})
        assert created.status_code == 403
        assert _error(created)["code"] == "AUTHORIZATION_FAILED"


def test_create_validation_and_duplicate_display_name() -> None:
    http = TestClient(_memory_app())
    headers = _bearer(_token(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD))
    empty = http.post("/api/v1/clients", headers=headers, json={"name": "   "})
    assert empty.status_code == 422
    missing = http.post("/api/v1/clients", headers=headers, json={"name": ""})
    assert missing.status_code == 422
    duplicate = http.post("/api/v1/clients", headers=headers, json={"name": CREATED_DUP})
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["client_id"] != DEFAULT_CLIENT_ID
    assert duplicate.json()["name"] == CREATED_DUP
    listed = http.get("/api/v1/clients", headers=headers).json()
    assert _names(listed)[DEFAULT_CLIENT_ID] == "Alpha Co"


def test_created_company_is_not_visible_to_other_publisher() -> None:
    http = TestClient(_memory_app())
    creator = _bearer(_token(http, SOLO_PUBLISHER, SOLO_PASSWORD))
    created = http.post("/api/v1/clients", headers=creator, json={"name": CREATED})
    assert created.status_code == 201
    new_id = created.json()["client_id"]
    other = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
    listed = http.get("/api/v1/clients", headers=other)
    assert new_id not in _ids(listed.json())
    selected = _select(http, other["Authorization"].split(" ", 1)[1], new_id)
    assert selected.status_code == 403
    renamed = http.post(
        f"/api/v1/clients/{new_id}/rename",
        headers=other,
        json={"name": CREATED_RENAME},
    )
    assert renamed.status_code == 403


def test_spa_add_company_is_publisher_only() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-company-create-form" in views
    assert "Add Company" in views
    assert "data-company-onboarding" in views
    assert "Processing-ready" in views
    assert "createClient" in client_js
    assert "createClientUser" in client_js
    assert "data-company-client-form" in views
    assert "companyCreateForm" in app_js
    create_block = app_js.split("companyCreateForm", 1)[1].split("companyClientForm", 1)[0]
    assert 'String(new FormData(form).get("name") || "").trim()' in create_block
    assert "Company name is required." in create_block
    assert "if (!name)" in create_block
    assert create_block.index("if (!name)") < create_block.index("createClient(name)")
    assert "maxlength=\"200\"" in views


@requires_postgres
@postgres_only
def test_postgres_create_keeps_existing_tenants_and_publications(
    pg_conn, postgres_url: str, tmp_path
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
        company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
        first = _publish_synthetic(
            http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
        )
        token1 = company1["Authorization"].split(" ", 1)[1]
        company2 = _bearer(_select(http, token1, COMPANY_2_CLIENT_ID).json()["access_token"])
        second = _publish_synthetic(
            http, tmp_path, publisher=company2, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_2
        )
        pointers_before = pg_conn.execute(
            """
            SELECT client_id::text AS client_id, publication_id::text AS publication_id
            FROM publication_current
            ORDER BY client_id
            """
        ).fetchall()
        facts_before = pg_conn.execute(
            "SELECT client_id::text AS client_id, campaign_id FROM publication_fact ORDER BY 1, 2"
        ).fetchall()
        name_before = {
            row["client_id"]: row["name"]
            for row in pg_conn.execute(
                "SELECT id::text AS client_id, name FROM client WHERE id IN (%s, %s)",
                (DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID),
            ).fetchall()
        }

        created = http.post(
            "/api/v1/clients",
            headers=company2,
            json={"name": CREATED},
        )
        assert created.status_code == 201, created.text
        new_id = created.json()["client_id"]
        UUID(new_id)
        assert new_id not in {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
        assert created.json()["code"] == new_id

        row = pg_conn.execute(
            "SELECT id::text AS client_id, code, name FROM client WHERE id = %s",
            (new_id,),
        ).fetchone()
        assert row["client_id"] == new_id
        assert row["code"] == new_id
        assert row["name"] == CREATED
        names_after = {
            item["client_id"]: item["name"]
            for item in pg_conn.execute(
                "SELECT id::text AS client_id, name FROM client WHERE id IN (%s, %s)",
                (DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID),
            ).fetchall()
        }
        assert names_after[DEFAULT_CLIENT_ID] == name_before[DEFAULT_CLIENT_ID]
        assert names_after[COMPANY_2_CLIENT_ID] == COMPANY_2_CLIENT_NAME
        pointers_after = pg_conn.execute(
            """
            SELECT client_id::text AS client_id, publication_id::text AS publication_id
            FROM publication_current
            ORDER BY client_id
            """
        ).fetchall()
        facts_after = pg_conn.execute(
            "SELECT client_id::text AS client_id, campaign_id FROM publication_fact ORDER BY 1, 2"
        ).fetchall()
        assert pointers_after == pointers_before
        assert facts_after == facts_before
        assert first["publication"]["publication_id"] in {
            item["publication_id"] for item in pointers_after
        }
        assert second["publication"]["publication_id"] in {
            item["publication_id"] for item in pointers_after
        }

        registry = http.get("/api/v1/clients", headers=company2).json()
        assert new_id in _ids(registry)
        assert _names(registry)[DEFAULT_CLIENT_ID] == name_before[DEFAULT_CLIENT_ID]
        assert _names(registry)[COMPANY_2_CLIENT_ID] == COMPANY_2_CLIENT_NAME

        selected = _select(http, company2["Authorization"].split(" ", 1)[1], new_id)
        assert selected.status_code == 200, selected.text
        assert selected.json()["session"]["client_id"] == new_id

        other = _bearer(_token(http, DEMO_PUBLISHER_2_SUBJECT, PUBLISHER2_PASSWORD))
        assert new_id not in _ids(http.get("/api/v1/clients", headers=other).json())
        client1 = _bearer(_token(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD))
        client2 = _bearer(_token(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD))
        assert (
            http.post("/api/v1/clients", headers=client1, json={"name": CREATED}).status_code
            == 403
        )
        assert (
            http.post("/api/v1/clients", headers=client2, json={"name": CREATED}).status_code
            == 403
        )
        assert http.get("/api/v1/clients", headers=client1).status_code == 403
