"""RUN 004: one publisher identity selects Company 1 or Company 2.

Does not open hosted databases. PostgreSQL cases require DFIP_TEST_DATABASE_URL
and drop/recreate ``public`` via existing fixtures — never a live URI.
Does not create Company 3. Does not change Excel, Power BI, or catalog version IDs.
"""

from __future__ import annotations

from pathlib import Path

import jwt
from dfip_api.app import create_app
from dfip_api.auth import Principal
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.local_demo_seed import seed_company2_identities, seed_demo_identities
from dfip_api.membership import requires_client_selection
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_db.identity import MembershipRow
from dfip_db.local_demo_guard import (
    COMPANY_2_CLIENT_ID,
    DEFAULT_CLIENT_ID,
    DEMO_CLIENT_2_SUBJECT,
    DEMO_CLIENT_SUBJECT,
    DEMO_PUBLISHER_2_SUBJECT,
    DEMO_PUBLISHER_SUBJECT,
)
from fastapi.testclient import TestClient

from http_ingest_support import source_row, upload_workbook, workbook_bytes
from postgres_support import postgres_only, requires_postgres
from test_p5_api import JWT_SECRET, _encode_jwt, make_settings
from test_p9_authz import _error

PUBLISHER_PASSWORD = "local-publisher-pass"
CLIENT_PASSWORD = "local-client-pass"
PUBLISHER2_PASSWORD = "local-publisher2-pass"
CLIENT2_PASSWORD = "local-client2-pass"
ITERATIONS = 1000
UNAUTHORIZED_CLIENT_ID = "a0000000-0000-4000-8000-000000000099"
CAMP_1 = "camp-sel-1"
CAMP_2 = "camp-sel-2"
ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"


def _identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.put_password_user(
        subject=DEMO_PUBLISHER_SUBJECT,
        password=PUBLISHER_PASSWORD,
        iterations=ITERATIONS,
        memberships=(
            MembershipRow(DEFAULT_CLIENT_ID, "publisher", code="default", name="Company 1"),
            MembershipRow(COMPANY_2_CLIENT_ID, "publisher", code="company-2", name="Company 2"),
        ),
    )
    store.put_password_user(
        subject=DEMO_CLIENT_SUBJECT,
        password=CLIENT_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(DEFAULT_CLIENT_ID, "client", code="default"),),
    )
    store.put_password_user(
        subject=DEMO_PUBLISHER_2_SUBJECT,
        password=PUBLISHER2_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(COMPANY_2_CLIENT_ID, "publisher", code="company-2"),),
    )
    store.put_password_user(
        subject=DEMO_CLIENT_2_SUBJECT,
        password=CLIENT2_PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(COMPANY_2_CLIENT_ID, "client", code="company-2"),),
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


def _select(http: TestClient, token: str, client_id: str):
    return http.post(
        "/api/v1/auth/select-client",
        headers=_bearer(token),
        json={"client_id": client_id},
    )


def _claims(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


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
    body = uploaded.json()
    assert body["published"] is False
    run_id = body["processing_run"]["processing_run_id"]
    created = http.post(
        "/api/v1/publications",
        headers=publisher,
        json={"client_id": client_id, "processing_run_id": run_id},
    )
    assert created.status_code == 201, created.text
    return created.json(), body


def test_requires_client_selection_fail_closed_for_unbound_inspector() -> None:
    unbound = Principal(
        subject=DEMO_PUBLISHER_SUBJECT,
        auth_mode="jwt",
        role="publisher",
        client_id=None,
        membership_client_ids=(DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID),
    )
    bound = Principal(
        subject=DEMO_PUBLISHER_SUBJECT,
        auth_mode="jwt",
        role="publisher",
        client_id=DEFAULT_CLIENT_ID,
        membership_client_ids=(DEFAULT_CLIENT_ID,),
    )
    claim_based = Principal(
        subject="user-1",
        auth_mode="jwt",
        role="publisher",
        client_id=None,
        membership_client_ids=None,
    )
    assert requires_client_selection(unbound) is True
    assert requires_client_selection(bound) is False
    assert requires_client_selection(claim_based) is False


def test_universal_publisher_login_is_unbound_until_select() -> None:
    http = TestClient(_memory_app())
    response = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    token = response.json()["access_token"]
    assert session["subject"] == DEMO_PUBLISHER_SUBJECT
    assert session["role"] == "publisher"
    assert session["client_id"] is None
    codes = {item["code"] for item in session["clients"]}
    assert codes == {"default", "company-2"}
    assert "client_id" not in _claims(token)
    denied = http.get("/api/v1/source-files", headers=_bearer(token))
    assert denied.status_code == 403
    assert _error(denied)["code"] == "AUTHORIZATION_FAILED"
    skipped = http.get(
        "/api/v1/facts",
        headers=_bearer(token),
        params={"client_id": DEFAULT_CLIENT_ID},
    )
    assert skipped.status_code == 403
    selected = _select(http, token, DEFAULT_CLIENT_ID)
    assert selected.status_code == 200, selected.text
    bound = selected.json()["session"]
    assert bound["client_id"] == DEFAULT_CLIENT_ID
    assert _claims(selected.json()["access_token"])["client_id"] == DEFAULT_CLIENT_ID


def test_publisher_selects_authorized_company_1_and_company_2() -> None:
    http = TestClient(_memory_app())
    token = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD).json()["access_token"]
    first = _select(http, token, DEFAULT_CLIENT_ID)
    second = _select(http, first.json()["access_token"], COMPANY_2_CLIENT_ID)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["session"]["client_id"] == DEFAULT_CLIENT_ID
    assert second.json()["session"]["client_id"] == COMPANY_2_CLIENT_ID
    assert _claims(second.json()["access_token"])["client_id"] == COMPANY_2_CLIENT_ID
    stale = http.get("/api/v1/session", headers=_bearer(first.json()["access_token"]))
    assert stale.status_code == 200
    assert stale.json()["client_id"] == DEFAULT_CLIENT_ID


def test_unauthorized_company_selection_is_denied() -> None:
    http = TestClient(_memory_app())
    token = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD).json()["access_token"]
    denied = _select(http, token, UNAUTHORIZED_CLIENT_ID)
    assert denied.status_code == 403
    assert _error(denied)["code"] == "AUTHORIZATION_FAILED"
    login_denied = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD, UNAUTHORIZED_CLIENT_ID)
    assert login_denied.status_code == 403


def test_client_cannot_select_inspect_or_publish() -> None:
    http = TestClient(_memory_app())
    client_token = _login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD).json()["access_token"]
    headers = _bearer(client_token)
    select = _select(http, client_token, DEFAULT_CLIENT_ID)
    inspect = http.get("/api/v1/source-files", headers=headers)
    publish = http.post(
        "/api/v1/publications",
        headers=headers,
        json={
            "client_id": DEFAULT_CLIENT_ID,
            "processing_run_id": "d0000000-0000-4000-8000-000000000001",
        },
    )
    assert select.status_code == 403
    assert inspect.status_code == 403
    assert publish.status_code == 403
    assert {
        _error(select)["code"],
        _error(inspect)["code"],
        _error(publish)["code"],
    } == {"AUTHORIZATION_FAILED"}
    session = http.get("/api/v1/session", headers=headers)
    assert session.status_code == 200
    assert session.json()["role"] == "client"
    assert session.json()["client_id"] == DEFAULT_CLIENT_ID


def test_switch_upload_and_publish_use_selected_company_without_leakage(
    tmp_path: Path,
) -> None:
    http = TestClient(_memory_app())
    unbound = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD).json()["access_token"]
    company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
    first, upload1 = _publish_synthetic(
        http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
    )
    assert upload1["client_id"] == DEFAULT_CLIENT_ID
    assert first["publication"]["client_id"] == DEFAULT_CLIENT_ID

    switched = _select(http, company1["Authorization"].split(" ", 1)[1], COMPANY_2_CLIENT_ID)
    company2 = _bearer(switched.json()["access_token"])
    current_as_c2 = http.get("/api/v1/publications/current", headers=company2)
    assert current_as_c2.status_code == 200
    assert current_as_c2.json()["publication"] is None
    working = http.get("/api/v1/facts", headers=company2)
    assert working.status_code == 200
    assert CAMP_1 not in _campaign_ids(working.json())
    override = http.get(
        "/api/v1/facts",
        headers=company2,
        params={"client_id": DEFAULT_CLIENT_ID},
    )
    assert override.status_code == 403
    form_override = upload_workbook(
        http,
        workbook_bytes(
            tmp_path / "steal.xlsx",
            [source_row(**{"Campaign ID": "camp-steal", "Variation ID": "var-steal"})],
        ),
        "steal.xlsx",
        headers=company2,
        wait=False,
        client_id=DEFAULT_CLIENT_ID,
    )
    assert form_override.status_code == 403

    second, upload2 = _publish_synthetic(
        http, tmp_path, publisher=company2, client_id=COMPANY_2_CLIENT_ID, campaign_id=CAMP_2
    )
    assert upload2["client_id"] == COMPANY_2_CLIENT_ID
    assert second["publication"]["client_id"] == COMPANY_2_CLIENT_ID

    client1 = _bearer(_login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD).json()["access_token"])
    client2 = _bearer(_login(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD).json()["access_token"])
    facts1 = http.get("/api/v1/publications/current/facts", headers=client1)
    facts2 = http.get("/api/v1/publications/current/facts", headers=client2)
    assert _campaign_ids(facts1.json()) == {CAMP_1}
    assert _campaign_ids(facts2.json()) == {CAMP_2}
    steal = http.get(
        "/api/v1/publications/current/facts",
        headers=client1,
        params={"client_id": COMPANY_2_CLIENT_ID},
    )
    assert steal.status_code == 403

    back = _select(http, switched.json()["access_token"], DEFAULT_CLIENT_ID)
    company1_again = _bearer(back.json()["access_token"])
    current_again = http.get("/api/v1/publications/current", headers=company1_again)
    assert (
        current_again.json()["publication"]["publication_id"]
        == first["publication"]["publication_id"]
    )
    working1 = http.get("/api/v1/facts", headers=company1_again)
    assert CAMP_1 in _campaign_ids(working1.json())
    assert CAMP_2 not in _campaign_ids(working1.json())


def test_claim_based_publisher_jwt_does_not_require_company_selection() -> None:
    app = create_app(
        settings=make_settings(dfip_auth_mode="jwt", dfip_auth_secret=JWT_SECRET, dfip_env="test")
    )
    http = TestClient(app)
    token = _encode_jwt(role="publisher")
    response = http.get("/api/v1/source-files", headers=_bearer(token))
    assert response.status_code == 200


def test_spa_selector_is_publisher_only_and_uses_select_client() -> None:
    roles = (WEB_STATIC / "js" / "roles.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    components = (WEB_STATIC / "js" / "components.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    assert "needsCompanySelection" in roles
    assert "inspectorClients" in roles
    assert "selectClient" in client_js
    assert "/auth/select-client" in client_js
    assert "companySelectView" in views
    assert "data-company-select-panel" in views
    assert "data-company-select-form" in components
    assert "data-active-company" in components
    assert "canAccessAdmin(session.role)" in components
    assert "needsCompanySelection(session)" in app_js
    assert "bindSelectedCompany" in app_js
    assert "clearPendingUpload()" in app_js
    assert 'access: "client"' in app_js


@requires_postgres
@postgres_only
def test_postgres_universal_publisher_selects_both_companies_without_leakage(
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
        login = _login(http, DEMO_PUBLISHER_SUBJECT, PUBLISHER_PASSWORD)
        assert login.status_code == 200, login.text
        session = login.json()["session"]
        assert session["client_id"] is None
        client_ids = {item["client_id"] for item in session["clients"]}
        assert client_ids == {DEFAULT_CLIENT_ID, COMPANY_2_CLIENT_ID}
        unbound = login.json()["access_token"]
        assert http.get("/api/v1/source-files", headers=_bearer(unbound)).status_code == 403
        assert _select(http, unbound, UNAUTHORIZED_CLIENT_ID).status_code == 403

        company1 = _bearer(_select(http, unbound, DEFAULT_CLIENT_ID).json()["access_token"])
        first, _upload1 = _publish_synthetic(
            http, tmp_path, publisher=company1, client_id=DEFAULT_CLIENT_ID, campaign_id=CAMP_1
        )
        token1 = company1["Authorization"].split(" ", 1)[1]
        company2 = _bearer(_select(http, token1, COMPANY_2_CLIENT_ID).json()["access_token"])
        current_as_c2 = http.get("/api/v1/publications/current", headers=company2)
        assert current_as_c2.status_code == 200
        assert current_as_c2.json()["publication"] is None
        working_c2 = http.get("/api/v1/facts", headers=company2)
        assert CAMP_1 not in _campaign_ids(working_c2.json())
        second, _upload2 = _publish_synthetic(
            http,
            tmp_path,
            publisher=company2,
            client_id=COMPANY_2_CLIENT_ID,
            campaign_id=CAMP_2,
        )
        assert second["publication"]["client_id"] == COMPANY_2_CLIENT_ID
        assert first["publication"]["client_id"] == DEFAULT_CLIENT_ID

        client1 = _bearer(_login(http, DEMO_CLIENT_SUBJECT, CLIENT_PASSWORD).json()["access_token"])
        client2 = _bearer(
            _login(http, DEMO_CLIENT_2_SUBJECT, CLIENT2_PASSWORD).json()["access_token"]
        )
        assert (
            _select(
                http, client1["Authorization"].split(" ", 1)[1], COMPANY_2_CLIENT_ID
            ).status_code
            == 403
        )
        assert http.get("/api/v1/source-files", headers=client1).status_code == 403
        facts1 = http.get("/api/v1/publications/current/facts", headers=client1)
        facts2 = http.get("/api/v1/publications/current/facts", headers=client2)
        assert _campaign_ids(facts1.json()) == {CAMP_1}
        assert _campaign_ids(facts2.json()) == {CAMP_2}
        steal = http.get(
            "/api/v1/publications/current/facts",
            headers=client1,
            params={"client_id": COMPANY_2_CLIENT_ID},
        )
        assert steal.status_code == 403
        bound_override = http.get(
            "/api/v1/facts",
            headers=company2,
            params={"client_id": DEFAULT_CLIENT_ID},
        )
        assert bound_override.status_code == 403
