"""Excel workbook grant: post-expiry /auth/refresh without lengthening login TTL.

Does not open Excel Desktop. Does not print tokens. Does not rewrite
excel/Client_Report.xlsx.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import jwt
from dfip_api.auth import (
    Principal,
    decode_jwt_claims,
    issue_access_token,
    principal_from_access_claims,
)
from dfip_api.excel_grant import EXCEL_TOKEN_TYP, issue_excel_workbook_token
from dfip_api.membership import enrich_principal
from dfip_db.identity import MembershipRow
from fastapi.testclient import TestClient

from test_p5_api import CLIENT_ID, JWT_SECRET, _encode_jwt, make_settings
from test_p7_client_auth import (
    ALICE,
    BOB,
    ITERATIONS,
    PASSWORD,
    _auth_app,
    _bearer,
    _login,
)
from test_p8_client_report import _session_jwt_stamped, _settings_value
from test_security_hardening import CLIENT_B

PUBLISHER = "pub.inspector"
PUBLISHER_SOLO = "pub.solo"


def _excel_app(*, access_ttl: int = 1, grant_ttl: int = 3600) -> tuple[TestClient, object]:
    ingest, facts, publications, identity, http = _auth_app_with_excel_ttl(
        access_ttl=access_ttl, grant_ttl=grant_ttl
    )
    del ingest, facts, publications
    return http, identity


def _auth_app_with_excel_ttl(*, access_ttl: int = 1, grant_ttl: int = 3600):
    from dfip_api.app import create_app
    from dfip_api.publication_store import InMemoryPublicationStore

    from test_p5_api import seed_stores
    from test_p7_client_auth import _identity_store
    from test_p7_publication import _publish
    from test_security_hardening import RUN_B_ID as RUN_OTHER
    from test_security_hardening import _seed_other_client

    ingest, facts = seed_stores()
    _seed_other_client(ingest, facts)
    ingest.processing_runs[RUN_OTHER].qa_verdict = "pass"
    publications = InMemoryPublicationStore()
    identity = _identity_store()
    identity.put_password_user(
        subject=PUBLISHER,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(
            MembershipRow(CLIENT_ID, "publisher"),
            MembershipRow(CLIENT_B, "publisher"),
        ),
    )
    identity.put_password_user(
        subject=PUBLISHER_SOLO,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_ID, "publisher"),),
    )
    settings = make_settings(
        dfip_auth_mode="jwt",
        dfip_auth_secret=JWT_SECRET,
        dfip_password_pbkdf2_iterations=1000,
        dfip_excel_access_ttl_seconds=access_ttl,
        dfip_excel_grant_ttl_seconds=grant_ttl,
        dfip_auth_token_ttl_seconds=3600,
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
    created_b = _publish(http, headers=publisher, client_id=CLIENT_B, processing_run_id=RUN_OTHER)
    assert created_a.status_code == 201, created_a.text
    assert created_b.status_code == 201, created_b.text
    return ingest, facts, publications, identity, http


def _claims(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})


def _assert_client_excel_stamp(
    token: object, *, client_id: str, session: str | None = None
) -> dict:
    """Decode stamp claims without printing the token."""
    assert _session_jwt_stamped(token)
    assert isinstance(token, str)
    if session is not None:
        assert token != session
    claims = _claims(token)
    assert claims["typ"] == EXCEL_TOKEN_TYP
    assert claims["role"] == "client"
    assert claims["client_id"] == client_id
    assert claims["jti"]
    return claims


def _download_excel_stamp(http: TestClient, username: str) -> tuple[str, str]:
    login = _login(http, username, PASSWORD)
    assert login.status_code == 200
    session = login.json()["access_token"]
    downloaded = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(session),
    )
    assert downloaded.status_code == 200, downloaded.text[:300]
    stamped = _settings_value(downloaded.content, "BearerToken")
    assert _session_jwt_stamped(stamped)
    assert isinstance(stamped, str)
    return stamped, session


def _excel_token_from_login(http: TestClient, username: str) -> str:
    login = _login(http, username, PASSWORD)
    assert login.status_code == 200
    session = login.json()["access_token"]
    settings = http.app.state.settings
    principal = principal_from_access_claims(
        decode_jwt_claims(settings, session, verify_exp=True)
    )
    principal = enrich_principal(http.app.state.identity_store, principal, settings)
    stamped = issue_excel_workbook_token(
        settings, principal, http.app.state.excel_grant_store
    )
    assert stamped is not None
    return stamped


def _wait_until_expired(token: str) -> tuple[int, int]:
    exp = int(_claims(token)["exp"])
    now = int(datetime.now(tz=UTC).timestamp())
    if exp >= now:
        time.sleep(exp - now + 1)
    after = int(datetime.now(tz=UTC).timestamp())
    assert after > exp
    return exp, after


def test_excel_stamp_is_not_the_login_token_and_carries_grant_claims() -> None:
    http, _identity = _excel_app()
    stamped, session = _download_excel_stamp(http, ALICE)
    _assert_client_excel_stamp(stamped, client_id=CLIENT_ID, session=session)
    login_claims = _claims(session)
    assert login_claims.get("typ") != EXCEL_TOKEN_TYP
    login = _login(http, ALICE, PASSWORD)
    assert login.status_code == 200
    assert login.json()["expires_in"] >= 60


def test_expired_excel_jwt_can_refresh_then_read_own_history_only() -> None:
    http, _identity = _excel_app()
    stamped, _session = _download_excel_stamp(http, ALICE)
    exp, after = _wait_until_expired(stamped)
    del exp
    facts = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(stamped),
        params={"limit": 1},
    )
    assert facts.status_code == 401
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    access_claims = _claims(access)
    assert int(access_claims["exp"]) > after
    assert access_claims.get("typ") == EXCEL_TOKEN_TYP
    assert access_claims["jti"] == _claims(stamped)["jti"]
    assert access_claims["role"] == "client"
    assert access_claims["client_id"] == CLIENT_ID
    own = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(access),
        params={"limit": 1},
    )
    assert own.status_code == 200
    stolen = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(access),
        params={"limit": 1, "client_id": CLIENT_B},
    )
    assert stolen.status_code == 403
    ops = http.get("/api/v1/ops/ready", headers=_bearer(access))
    assert ops.status_code == 403
    publish = http.post(
        "/api/v1/publications",
        headers=_bearer(access),
        json={"client_id": CLIENT_ID, "processing_run_id": "d0000000-0000-4000-8000-000000000001"},
    )
    assert publish.status_code == 403


def test_expired_login_jwt_without_excel_grant_cannot_refresh() -> None:
    http, _identity, _pub_b = _auth_app()
    settings = http.app.state.settings
    token, _ttl = issue_access_token(
        settings,
        Principal(
            subject=ALICE,
            auth_mode="jwt",
            role="client",
            client_id=CLIENT_ID,
            token_version=1,
        ),
        ttl_seconds=1,
    )
    claims = _claims(token)
    assert claims.get("typ") != EXCEL_TOKEN_TYP
    time.sleep(2)
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(token))
    assert refreshed.status_code == 401


def test_logout_rejects_session_jwt_but_keeps_excel_grant() -> None:
    http, identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    stamped, session = _download_excel_stamp(http, ALICE)
    assert int(_claims(stamped)["exp"]) > int(datetime.now(tz=UTC).timestamp())
    before = identity.get_by_subject(ALICE)
    assert before is not None
    signed_out = http.post("/api/v1/auth/logout", headers=_bearer(session))
    assert signed_out.status_code == 204
    after = identity.get_by_subject(ALICE)
    assert after is not None
    assert after.token_version == before.token_version + 1
    session_facts = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(session),
        params={"limit": 1},
    )
    assert session_facts.status_code == 401
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    assert _claims(access)["jti"] == _claims(stamped)["jti"]
    facts = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(stamped),
        params={"limit": 1},
    )
    assert facts.status_code == 200


def test_logout_does_not_revoke_expired_excel_grant() -> None:
    http, _identity = _excel_app()
    stamped, session = _download_excel_stamp(http, ALICE)
    _wait_until_expired(stamped)
    signed_out = http.post("/api/v1/auth/logout", headers=_bearer(session))
    assert signed_out.status_code == 204
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    assert _claims(access)["typ"] == EXCEL_TOKEN_TYP
    assert _claims(access)["jti"] == _claims(stamped)["jti"]


def test_publisher_refreshable_download_mints_client_excel_grant() -> None:
    http, _identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    login = _login(http, PUBLISHER, PASSWORD, CLIENT_ID)
    assert login.status_code == 200
    session = login.json()["access_token"]
    assert _claims(session)["role"] == "publisher"
    downloaded = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(session),
    )
    assert downloaded.status_code == 200, downloaded.text[:300]
    stamped = _settings_value(downloaded.content, "BearerToken")
    claims = _assert_client_excel_stamp(stamped, client_id=CLIENT_ID, session=session)
    assert claims.get("sub") == PUBLISHER
    static = http.get(
        "/api/v1/publications/current/client-report.xlsx",
        headers=_bearer(session),
    )
    assert static.status_code == 200
    assert _settings_value(static.content, "BearerToken") in {None, ""}
    assert isinstance(stamped, str)
    working = http.get("/api/v1/facts", headers=_bearer(stamped), params={"limit": 1})
    assert working.status_code == 403
    uploads = http.get("/api/v1/source-files", headers=_bearer(stamped))
    assert uploads.status_code == 403
    ops = http.get("/api/v1/ops/ready", headers=_bearer(stamped))
    assert ops.status_code == 403
    companies = http.get("/api/v1/clients", headers=_bearer(stamped))
    assert companies.status_code == 403
    publish = http.post(
        "/api/v1/publications",
        headers=_bearer(stamped),
        json={"client_id": CLIENT_ID, "processing_run_id": "d0000000-0000-4000-8000-000000000001"},
    )
    assert publish.status_code == 403
    own = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(stamped),
        params={"limit": 1},
    )
    assert own.status_code == 200
    stolen = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(stamped),
        params={"limit": 1, "client_id": CLIENT_B},
    )
    assert stolen.status_code == 403


def test_publisher_excel_grant_renews_after_expiry_and_stays_client() -> None:
    http, _identity = _excel_app()
    session = _login(http, PUBLISHER, PASSWORD, CLIENT_ID).json()["access_token"]
    downloaded = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(session),
    )
    assert downloaded.status_code == 200, downloaded.text[:300]
    stamped = _settings_value(downloaded.content, "BearerToken")
    assert isinstance(stamped, str)
    _assert_client_excel_stamp(stamped, client_id=CLIENT_ID, session=session)
    _wait_until_expired(stamped)
    expired_history = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(stamped),
        params={"limit": 1},
    )
    assert expired_history.status_code == 401
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    access_claims = _claims(access)
    assert access_claims.get("typ") == EXCEL_TOKEN_TYP
    assert access_claims["jti"] == _claims(stamped)["jti"]
    assert access_claims["role"] == "client"
    assert access_claims["client_id"] == CLIENT_ID
    own = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(access),
        params={"limit": 1},
    )
    assert own.status_code == 200
    working = http.get("/api/v1/facts", headers=_bearer(access), params={"limit": 1})
    assert working.status_code == 403
    stolen = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(access),
        params={"limit": 1, "client_id": CLIENT_B},
    )
    assert stolen.status_code == 403


def test_publisher_cannot_mint_excel_grant_for_unauthorized_company() -> None:
    http, _identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    session = _login(http, PUBLISHER_SOLO, PASSWORD, CLIENT_ID).json()["access_token"]
    denied = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(session),
        params={"client_id": CLIENT_B},
    )
    assert denied.status_code == 403
    assert not denied.content.startswith(b"PK")
    bound_other = _login(http, PUBLISHER, PASSWORD, CLIENT_ID).json()["access_token"]
    cross = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(bound_other),
        params={"client_id": CLIENT_B},
    )
    assert cross.status_code == 403
    selected = _login(http, PUBLISHER, PASSWORD, CLIENT_B).json()["access_token"]
    allowed = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(selected),
    )
    assert allowed.status_code == 200
    _assert_client_excel_stamp(
        _settings_value(allowed.content, "BearerToken"),
        client_id=CLIENT_B,
        session=selected,
    )


def test_claim_only_publisher_jwt_is_not_embedded_in_refreshable_workbook() -> None:
    http, _identity, _pub_b = _auth_app()
    publisher = {"Authorization": f"Bearer {_encode_jwt(role='publisher', client_id=CLIENT_ID)}"}
    downloaded = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=publisher,
    )
    assert downloaded.status_code == 403
    assert not downloaded.content.startswith(b"PK")


def test_publisher_logout_does_not_revoke_minted_excel_grant() -> None:
    http, identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    session = _login(http, PUBLISHER, PASSWORD, CLIENT_ID).json()["access_token"]
    downloaded = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(session),
    )
    assert downloaded.status_code == 200
    stamped = _settings_value(downloaded.content, "BearerToken")
    assert isinstance(stamped, str)
    before = identity.get_by_subject(PUBLISHER)
    assert before is not None
    signed_out = http.post("/api/v1/auth/logout", headers=_bearer(session))
    assert signed_out.status_code == 204
    after = identity.get_by_subject(PUBLISHER)
    assert after is not None
    assert after.token_version == before.token_version + 1
    session_facts = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(session),
        params={"limit": 1},
    )
    assert session_facts.status_code == 401
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    access_claims = _claims(access)
    assert access_claims.get("typ") == EXCEL_TOKEN_TYP
    assert access_claims["role"] == "client"
    assert access_claims["client_id"] == CLIENT_ID
    assert access_claims["jti"] == _claims(stamped)["jti"]
    facts = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(stamped),
        params={"limit": 1},
    )
    assert facts.status_code == 200


def test_bob_excel_token_cannot_read_alice_history() -> None:
    http, _identity = _excel_app()
    current = http.get(
        "/api/v1/publications/current",
        headers=_bearer(_login(http, BOB, PASSWORD).json()["access_token"]),
    )
    assert current.status_code == 200
    assert current.json()["publication"]["client_id"] == CLIENT_B
    bob = _excel_token_from_login(http, BOB)
    _wait_until_expired(bob)
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(bob))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    assert _claims(access)["client_id"] == CLIENT_B
    other = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(access),
        params={"limit": 1, "client_id": CLIENT_ID},
    )
    assert other.status_code == 403
    own = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(access),
        params={"limit": 1},
    )
    assert own.status_code == 200


def test_excel_grant_mint_persists_active_row() -> None:
    http, identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    stamped, _session = _download_excel_stamp(http, ALICE)
    claims = _assert_client_excel_stamp(stamped, client_id=CLIENT_ID)
    grant = http.app.state.excel_grant_store.get_active(claims["jti"])
    assert grant is not None
    assert grant.client_id == CLIENT_ID
    assert grant.revoked_at is None
    record = identity.get_by_subject(ALICE)
    assert record is not None
    assert grant.user_id == record.user_id


def test_excel_grant_ignores_website_token_version() -> None:
    http, identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    stamped, _session = _download_excel_stamp(http, ALICE)
    record = identity.get_by_subject(ALICE)
    assert record is not None
    bumped = identity.increment_token_version(record.user_id)
    assert bumped == record.token_version + 1
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    assert _claims(access)["jti"] == _claims(stamped)["jti"]
    assert _claims(access)["typ"] == EXCEL_TOKEN_TYP
    facts = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(stamped),
        params={"limit": 1},
    )
    assert facts.status_code == 200


def test_missing_excel_grant_row_is_restored_on_refresh() -> None:
    http, _identity = _excel_app()
    stamped, _session = _download_excel_stamp(http, ALICE)
    claims = _claims(stamped)
    jti = claims["jti"]
    store = http.app.state.excel_grant_store
    assert store.get_active(jti) is not None
    dropped = store._by_jti.pop(jti)
    assert dropped.revoked_at is None
    assert store.get(jti) is None
    _wait_until_expired(stamped)
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    access_claims = _claims(access)
    assert access_claims["jti"] == jti
    assert access_claims["typ"] == EXCEL_TOKEN_TYP
    assert access_claims["role"] == "client"
    assert access_claims["client_id"] == CLIENT_ID
    restored = store.get_active(jti)
    assert restored is not None
    assert restored.jti == jti
    assert restored.client_id == CLIENT_ID
    assert restored.user_id == dropped.user_id
    own = http.get(
        "/api/v1/publications/history/facts",
        headers=_bearer(access),
        params={"limit": 1},
    )
    assert own.status_code == 200


def test_missing_excel_grant_row_past_original_ttl_cannot_refresh() -> None:
    http, _identity = _excel_app(access_ttl=1, grant_ttl=1)
    stamped, _session = _download_excel_stamp(http, ALICE)
    jti = _claims(stamped)["jti"]
    store = http.app.state.excel_grant_store
    store._by_jti.pop(jti)
    time.sleep(2)
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 401
    assert store.get(jti) is None


def test_explicitly_revoked_excel_grant_cannot_refresh() -> None:
    http, identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    stamped, _session = _download_excel_stamp(http, ALICE)
    record = identity.get_by_subject(ALICE)
    assert record is not None
    revoked = http.app.state.excel_grant_store.revoke_for_user(record.user_id)
    assert revoked == 1
    jti = _claims(stamped)["jti"]
    row = http.app.state.excel_grant_store.get(jti)
    assert row is not None
    assert row.revoked_at is not None
    refreshed = http.post("/api/v1/auth/refresh", headers=_bearer(stamped))
    assert refreshed.status_code == 401
    still = http.app.state.excel_grant_store.get(jti)
    assert still is not None
    assert still.revoked_at is not None


def test_excel_grant_persist_failure_fails_download() -> None:
    http, _identity = _excel_app(access_ttl=3600, grant_ttl=3600)

    class HollowGrantStore:
        def register(self, **_kwargs):
            return None

        def get(self, _jti: str):
            return None

        def get_active(self, _jti: str):
            return None

        def touch(self, _jti: str) -> None:
            return None

        def revoke_for_user(self, _user_id: str) -> int:
            return 0

    http.app.state.excel_grant_store = HollowGrantStore()
    session = _login(http, ALICE, PASSWORD).json()["access_token"]
    downloaded = http.get(
        "/api/v1/publications/current/refreshable-client-report.xlsx",
        headers=_bearer(session),
    )
    assert downloaded.status_code == 503
    assert not downloaded.content.startswith(b"PK")


def _prefer(token: str) -> dict[str, str]:
    return {"Prefer": f"dfip-bearer={token}"}


def test_prefer_dfip_bearer_renews_expired_excel_jwt_and_reads_history() -> None:
    http, _identity = _excel_app()
    stamped, _session = _download_excel_stamp(http, ALICE)
    _wait_until_expired(stamped)
    facts = http.get(
        "/api/v1/publications/history/facts",
        headers=_prefer(stamped),
        params={"limit": 1},
    )
    assert facts.status_code == 401
    csv_expired = http.get(
        "/api/v1/publications/history/facts.csv",
        headers=_prefer(stamped),
    )
    assert csv_expired.status_code == 401
    refreshed = http.post("/api/v1/auth/refresh", headers=_prefer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    access_claims = _claims(access)
    assert access_claims.get("typ") == EXCEL_TOKEN_TYP
    assert access_claims["jti"] == _claims(stamped)["jti"]
    assert access_claims["client_id"] == CLIENT_ID
    own = http.get(
        "/api/v1/publications/history/facts.csv",
        headers=_prefer(access),
    )
    assert own.status_code == 200
    assert "campaign_id" in own.text.splitlines()[0]


def test_prefer_dfip_bearer_wins_over_windows_negotiate_authorization() -> None:
    http, _identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    stamped, _session = _download_excel_stamp(http, ALICE)
    mixed = {
        "Authorization": "Negotiate YIIB",
        "Prefer": f"dfip-bearer={stamped}",
    }
    facts = http.get(
        "/api/v1/publications/history/facts",
        headers=mixed,
        params={"limit": 1},
    )
    assert facts.status_code == 200
    stolen = http.get(
        "/api/v1/publications/history/facts",
        headers=mixed,
        params={"limit": 1, "client_id": CLIENT_B},
    )
    assert stolen.status_code == 403


def test_prefer_dfip_bearer_survives_publisher_logout() -> None:
    http, identity = _excel_app(access_ttl=3600, grant_ttl=3600)
    stamped, session = _download_excel_stamp(http, ALICE)
    signed_out = http.post("/api/v1/auth/logout", headers=_bearer(session))
    assert signed_out.status_code == 204
    after = identity.get_by_subject(ALICE)
    assert after is not None
    refreshed = http.post("/api/v1/auth/refresh", headers=_prefer(stamped))
    assert refreshed.status_code == 200
    access = refreshed.json()["access_token"]
    assert _claims(access)["jti"] == _claims(stamped)["jti"]
    facts = http.get(
        "/api/v1/publications/history/facts.csv",
        headers=_prefer(access),
    )
    assert facts.status_code == 200


def test_http_credentials_from_headers_parse_prefer_and_bearer() -> None:
    from dfip_api.auth import http_credentials_from_headers

    prefer = http_credentials_from_headers(None, "return=representation, dfip-bearer=abc.def.ghi")
    assert prefer is not None
    assert prefer.scheme == "Bearer"
    assert prefer.credentials == "abc.def.ghi"
    quoted = http_credentials_from_headers(
        "Negotiate AAAA",
        'return=representation, dfip-bearer="abc.def.ghi"',
    )
    assert quoted is not None
    assert quoted.credentials == "abc.def.ghi"
    bearer = http_credentials_from_headers("Bearer website-token", None)
    assert bearer is not None
    assert bearer.credentials == "website-token"
    assert http_credentials_from_headers("Negotiate AAAA", None) is None
    assert http_credentials_from_headers(None, "return=minimal") is None
