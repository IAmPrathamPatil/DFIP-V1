"""P7 client authentication: password sign-in, membership scope, tenant isolation.

Uses the in-memory identity adapter. Does not open PostgreSQL, does not print
tokens, and does not change August or publication_current live state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from dfip_api.app import create_app
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_db.identity import MembershipRow
from dfip_web.api_client import DfipApiClient
from fastapi.testclient import TestClient

from test_p5_api import (
    AUTH,
    CLIENT_ID,
    DEV_TOKEN,
    FILE_A,
    JWT_SECRET,
    RUN_A,
    _encode_jwt,
    make_settings,
    seed_stores,
)
from test_p7_publication import FORBIDDEN_TEMPLATE_TOKENS, _publish
from test_p9_authz import _error
from test_security_hardening import (
    CLIENT_B,
    _seed_other_client,
)
from test_security_hardening import (
    FILE_B as FILE_OTHER,
)
from test_security_hardening import (
    RUN_B_ID as RUN_OTHER,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "apps" / "web" / "static"
PASSWORD = "correct-horse-battery"
ITERATIONS = 1000
ALICE = "alice.client"
BOB = "bob.client"
NONE = "none.member"
MULTI = "multi.member"
FORBIDDEN_AUTH = "Invalid authentication credentials."


def _identity_store() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.put_password_user(
        subject=ALICE,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_ID, "client"),),
    )
    store.put_password_user(
        subject=BOB,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_B, "client"),),
    )
    store.put_password_user(
        subject=NONE,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(),
    )
    store.put_password_user(
        subject=MULTI,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(
            MembershipRow(CLIENT_ID, "client"),
            MembershipRow(CLIENT_B, "client"),
        ),
    )
    return store


def _auth_app(*, dual_dev_token: bool = False):
    ingest, facts = seed_stores()
    _seed_other_client(ingest, facts)
    ingest.processing_runs[RUN_OTHER].qa_verdict = "pass"
    publications = InMemoryPublicationStore()
    identity = _identity_store()
    if dual_dev_token:
        settings = make_settings(
            dfip_auth_mode="dev_token",
            dfip_dev_auth_token=DEV_TOKEN,
            dfip_dev_auth_role="publisher",
            dfip_auth_secret=JWT_SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
        )
    else:
        settings = make_settings(
            dfip_auth_mode="jwt",
            dfip_auth_secret=JWT_SECRET,
            dfip_password_pbkdf2_iterations=ITERATIONS,
        )
    app = create_app(
        settings=settings,
        ingest_store=ingest,
        fact_store=facts,
        publication_store=publications,
        identity_store=identity,
    )
    http = TestClient(app)
    publisher = {"Authorization": f"Bearer {_encode_jwt(role='publisher')}"}
    created_a = _publish(http, headers=publisher, client_id=CLIENT_ID)
    created_b = _publish(
        http,
        headers=publisher,
        client_id=CLIENT_B,
        processing_run_id=RUN_OTHER,
    )
    assert created_a.status_code == 201, created_a.text
    assert created_b.status_code == 201, created_b.text
    pub_b = created_b.json()["publication"]["publication_id"]
    return http, identity, pub_b


def _login(http: TestClient, username: str, password: str, client_id: str | None = None):
    body: dict[str, str] = {"username": username, "password": password}
    if client_id:
        body["client_id"] = client_id
    return http.post("/api/v1/auth/login", json=body)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_succeeds_for_valid_client_user() -> None:
    http, _identity, _pub_b = _auth_app()
    response = _login(http, ALICE, PASSWORD)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] >= 1
    assert body["session"]["subject"] == ALICE
    assert body["session"]["auth_mode"] == "jwt"
    assert body["session"]["role"] == "client"
    assert body["session"]["client_id"] == CLIENT_ID
    assert PASSWORD not in response.text
    assert JWT_SECRET not in response.text
    assert "password_hash" not in response.text
    claims = jwt.decode(body["access_token"], JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == ALICE
    assert claims["client_id"] == CLIENT_ID
    assert claims["role"] == "client"
    assert claims["ver"] == 1
    assert "exp" in claims


def test_invalid_credentials_are_rejected() -> None:
    http, _identity, _pub_b = _auth_app()
    missing = _login(http, "nobody.here", PASSWORD)
    wrong = _login(http, ALICE, "not-the-password")
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert _error(missing)["code"] == "AUTHENTICATION_FAILED"
    assert _error(wrong)["message"] == FORBIDDEN_AUTH
    assert _error(missing)["message"] == _error(wrong)["message"]
    assert "nobody.here" not in missing.text
    assert PASSWORD not in wrong.text


def test_authenticated_user_resolves_app_user_and_membership() -> None:
    http, identity, _pub_b = _auth_app()
    token = _login(http, ALICE, PASSWORD).json()["access_token"]
    session = http.get("/api/v1/session", headers=_bearer(token))
    assert session.status_code == 200
    body = session.json()
    assert body["subject"] == ALICE
    assert body["client_id"] == CLIENT_ID
    record = identity.get_by_subject(ALICE)
    assert record is not None
    assert body["subject"] == record.subject
    assert record.memberships[0].client_id == CLIENT_ID


def test_user_without_membership_cannot_access_client_data() -> None:
    http, _identity, _pub_b = _auth_app()
    login = _login(http, NONE, PASSWORD)
    assert login.status_code == 403
    assert _error(login)["code"] == "AUTHORIZATION_FAILED"
    minted = _encode_jwt(sub=NONE, role="client", client_id=CLIENT_ID, ver=1)
    denied = http.get("/api/v1/publications/current", headers=_bearer(minted))
    assert denied.status_code == 403
    facts = http.get("/api/v1/publications/current/facts", headers=_bearer(minted))
    assert facts.status_code == 403


def test_client_a_cannot_read_client_b_publication_or_facts() -> None:
    http, _identity, pub_b = _auth_app()
    token = _login(http, ALICE, PASSWORD).json()["access_token"]
    headers = _bearer(token)
    own = http.get("/api/v1/publications/current", headers=headers)
    assert own.status_code == 200
    assert own.json()["publication"]["client_id"] == CLIENT_ID
    facts = http.get("/api/v1/publications/current/facts", headers=headers)
    assert facts.status_code == 200
    assert facts.json()["pagination"]["total"] == 3
    assert "camp-other" not in facts.text
    other = http.get(
        "/api/v1/publications/current",
        headers=headers,
        params={"client_id": CLIENT_B},
    )
    assert other.status_code == 403
    other_facts = http.get(
        "/api/v1/publications/current/facts",
        headers=headers,
        params={"client_id": CLIENT_B},
    )
    assert other_facts.status_code == 403
    historical = http.get(f"/api/v1/publications/{pub_b}/facts", headers=headers)
    assert historical.status_code == 404
    assert "camp-other" not in historical.text


def test_client_a_cannot_read_client_b_source_file_metadata() -> None:
    http, _identity, _pub_b = _auth_app()
    token = _login(http, ALICE, PASSWORD).json()["access_token"]
    headers = _bearer(token)
    listed = http.get("/api/v1/source-files", headers=headers)
    assert listed.status_code == 403
    one = http.get(f"/api/v1/source-files/{FILE_OTHER}", headers=headers)
    assert one.status_code == 403
    own_file = http.get(f"/api/v1/source-files/{FILE_A}", headers=headers)
    assert own_file.status_code == 403
    assert "other-client.xlsx" not in listed.text
    assert "other-client.xlsx" not in one.text


def test_forged_client_id_does_not_change_authorization_scope() -> None:
    http, _identity, _pub_b = _auth_app()
    token = _login(http, ALICE, PASSWORD).json()["access_token"]
    headers = _bearer(token)
    listed = http.get(
        "/api/v1/publications",
        headers=headers,
        params={"client_id": CLIENT_B},
    )
    assert listed.status_code == 403
    created = http.post(
        "/api/v1/publications",
        headers=headers,
        json={"client_id": CLIENT_B, "processing_run_id": RUN_A},
    )
    assert created.status_code == 403


def test_logout_invalidates_issued_access_token() -> None:
    http, identity, _pub_b = _auth_app()
    login = _login(http, ALICE, PASSWORD)
    token = login.json()["access_token"]
    before = identity.get_by_subject(ALICE)
    assert before is not None
    signed_out = http.post("/api/v1/auth/logout", headers=_bearer(token))
    assert signed_out.status_code == 204
    after = identity.get_by_subject(ALICE)
    assert after is not None
    assert after.token_version == before.token_version + 1
    later = http.get("/api/v1/session", headers=_bearer(token))
    assert later.status_code == 401
    assert _error(later)["code"] == "AUTHENTICATION_FAILED"


def test_expired_session_is_rejected() -> None:
    http, identity, _pub_b = _auth_app()
    record = identity.get_by_subject(ALICE)
    assert record is not None
    expired = jwt.encode(
        {
            "sub": ALICE,
            "role": "client",
            "client_id": CLIENT_ID,
            "ver": record.token_version,
            "exp": datetime.now(tz=UTC) - timedelta(seconds=30),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    if isinstance(expired, bytes):
        expired = expired.decode("ascii")
    response = http.get("/api/v1/session", headers=_bearer(expired))
    assert response.status_code == 401
    assert _error(response)["code"] == "AUTHENTICATION_FAILED"


def test_developer_token_remains_compatible_in_development() -> None:
    http, _identity, _pub_b = _auth_app(dual_dev_token=True)
    session = http.get("/api/v1/session", headers=AUTH)
    assert session.status_code == 200
    assert session.json()["auth_mode"] == "dev_token"
    assert session.json()["role"] == "publisher"
    login = _login(http, ALICE, PASSWORD)
    assert login.status_code == 200
    jwt_session = http.get(
        "/api/v1/session",
        headers=_bearer(login.json()["access_token"]),
    )
    assert jwt_session.status_code == 200
    assert jwt_session.json()["subject"] == ALICE
    assert jwt_session.json()["auth_mode"] == "jwt"


def test_multiple_memberships_require_explicit_client() -> None:
    http, _identity, _pub_b = _auth_app()
    ambiguous = _login(http, MULTI, PASSWORD)
    assert ambiguous.status_code == 403
    bound = _login(http, MULTI, PASSWORD, client_id=CLIENT_ID)
    assert bound.status_code == 200
    assert bound.json()["session"]["client_id"] == CLIENT_ID
    headers = _bearer(bound.json()["access_token"])
    denied = http.get(
        "/api/v1/publications/current/facts",
        headers=headers,
        params={"client_id": CLIENT_B},
    )
    assert denied.status_code == 403


def test_member_cannot_select_unrelated_client_on_login() -> None:
    http, _identity, _pub_b = _auth_app()
    alice_as_b = _login(http, ALICE, PASSWORD, client_id=CLIENT_B)
    bob_as_a = _login(http, BOB, PASSWORD, client_id=CLIENT_ID)
    assert alice_as_b.status_code == 403
    assert bob_as_a.status_code == 403
    assert _error(alice_as_b)["code"] == "AUTHORIZATION_FAILED"
    assert _error(bob_as_a)["code"] == "AUTHORIZATION_FAILED"


def test_refresh_reissues_membership_bound_token() -> None:
    http, identity, _pub_b = _auth_app()
    first = _login(http, ALICE, PASSWORD)
    token = first.json()["access_token"]
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(token))
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["token_type"] == "bearer"
    assert body["session"]["subject"] == ALICE
    assert body["session"]["client_id"] == CLIENT_ID
    next_token = body["access_token"]
    original = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    rotated = jwt.decode(next_token, JWT_SECRET, algorithms=["HS256"])
    assert rotated["sub"] == ALICE
    assert rotated["client_id"] == CLIENT_ID
    assert rotated["ver"] == original["ver"]
    record = identity.get_by_subject(ALICE)
    assert record is not None
    assert rotated["ver"] == record.token_version
    session = http.get("/api/v1/session", headers=_bearer(next_token))
    assert session.status_code == 200
    assert session.json()["client_id"] == CLIENT_ID
    signed_out = http.post("/api/v1/auth/logout", headers=_bearer(next_token))
    assert signed_out.status_code == 204
    assert http.get("/api/v1/session", headers=_bearer(token)).status_code == 401
    assert http.get("/api/v1/session", headers=_bearer(next_token)).status_code == 401


def test_login_issued_token_rejects_bad_signature_and_malformed() -> None:
    http, _identity, _pub_b = _auth_app()
    token = _login(http, ALICE, PASSWORD).json()["access_token"]
    parts = token.split(".")
    assert len(parts) == 3
    forged = f"{parts[0]}.{parts[1]}.AA-forged-signature"
    bad_sig = http.get("/api/v1/session", headers=_bearer(forged))
    malformed = http.get("/api/v1/session", headers=_bearer("not-a-jwt"))
    assert bad_sig.status_code == 401
    assert malformed.status_code == 401
    assert JWT_SECRET not in bad_sig.text


def test_client_can_read_own_published_csv_not_other_client() -> None:
    http, _identity, _pub_b = _auth_app()
    headers = _bearer(_login(http, ALICE, PASSWORD).json()["access_token"])
    own = http.get("/api/v1/publications/current/facts.csv", headers=headers)
    assert own.status_code == 200
    assert "camp-1" in own.text
    assert "camp-other" not in own.text
    cross = http.get(
        "/api/v1/publications/current/facts.csv",
        headers=headers,
        params={"client_id": CLIENT_B},
    )
    assert cross.status_code == 403


def test_python_client_login_logout_and_refresh() -> None:
    http, _identity, _pub_b = _auth_app()
    with DfipApiClient("http://testserver", http_client=http) as client:
        payload = client.login(ALICE, PASSWORD)
        assert payload["session"]["client_id"] == CLIENT_ID
        session = client.session()
        assert session["subject"] == ALICE
        refreshed = client.refresh_session()
        assert refreshed["session"]["subject"] == ALICE
        client.logout()
        try:
            client.session()
            raise AssertionError("logout must reject the previous access token")
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 401


def test_frontend_uses_password_sign_in_not_developer_token() -> None:
    views = (WEB_STATIC / "js" / "views.js").read_text(encoding="utf-8")
    app_js = (WEB_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    client_js = (WEB_STATIC / "js" / "api-client.js").read_text(encoding="utf-8")
    auth_js = (WEB_STATIC / "js" / "auth.js").read_text(encoding="utf-8")
    assert 'name="username"' in views
    assert 'name="password"' in views
    assert 'name="token"' not in views
    assert "Bearer token" not in views
    assert "DFIP_DEV_AUTH_TOKEN" not in views
    assert "api.login" in app_js
    assert "api.logout" in app_js
    assert "api.refresh" in app_js
    assert "/auth/login" in client_js
    assert "/auth/logout" in client_js
    assert "sessionStorage" in auth_js
    assert DEV_TOKEN not in views
    assert JWT_SECRET not in client_js
    assert DEV_TOKEN not in app_js


def test_no_auth_secrets_in_excel_or_frontend_artifacts() -> None:
    secret_tokens = (
        "DATABASE_URL",
        "DFIP_AUTH_SECRET",
        "SUPABASE_SERVICE_ROLE_KEY",
        "postgresql://",
        "DFIP_DEV_AUTH_TOKEN",
        DEV_TOKEN,
        JWT_SECRET,
        PASSWORD,
    )
    for path in WEB_STATIC.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for token in secret_tokens:
            assert str(token).lower() not in text, f"{path} contains {token}"
    excel_targets = [
        ROOT / "excel" / "PublishedFacts.m",
        ROOT / "excel" / "Client_Report.xlsx",
    ]
    excel_forbidden = (*FORBIDDEN_TEMPLATE_TOKENS, DEV_TOKEN, JWT_SECRET, PASSWORD)
    for path in excel_targets:
        if not path.is_file():
            continue
        if path.suffix.lower() == ".xlsx":
            text = path.read_bytes().decode("latin-1", errors="ignore")
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        for token in excel_forbidden:
            assert str(token).lower() not in lowered, f"{path} contains {token}"
