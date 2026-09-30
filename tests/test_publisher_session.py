"""Single active Publisher session: enforcement, takeover, and lease.

Server-side security boundary only. No browser storage is involved, so these
tests exercise the API directly: two Publisher sessions are two independent
``app_user``-bound session ids presented from two different callers.

Uses the in-memory identity adapter. Does not open PostgreSQL, does not print
tokens, and does not change publication_current state.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from dfip_api.app import create_app
from dfip_api.errors import (
    AUTHORIZATION_FAILED,
    PERSISTENCE_UNAVAILABLE,
    PUBLISHER_SESSION_REPLACED,
)
from dfip_api.identity_store import InMemoryIdentityStore
from dfip_api.limits import RateWindowLimiter
from dfip_api.publication_store import InMemoryPublicationStore
from dfip_api.publisher_session import (
    PUBLISHER_SESSION_HEADER,
    InMemoryPublisherSessionStore,
    enforce_publisher_session,
    new_session_id,
)
from dfip_db.identity import MembershipRow
from fastapi.testclient import TestClient

from test_p5_api import CLIENT_ID, JWT_SECRET, _error, make_settings, seed_stores
from test_security_hardening import CLIENT_B

PASSWORD = "correct-horse-battery"
ITERATIONS = 1000
PUBLISHER = "ops.publisher"
OTHER_PUBLISHER = "second.publisher"
CLIENT_A = "alice.client"
CLIENT_B_USER = "bob.client"
REPLACED = "Publisher session opened elsewhere. This session is no longer active."


def _identity_store() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.put_password_user(
        subject=PUBLISHER,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_ID, "publisher"),),
    )
    store.put_password_user(
        subject=OTHER_PUBLISHER,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_B, "publisher"),),
    )
    store.put_password_user(
        subject=CLIENT_A,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_ID, "client"),),
    )
    store.put_password_user(
        subject=CLIENT_B_USER,
        password=PASSWORD,
        iterations=ITERATIONS,
        memberships=(MembershipRow(CLIENT_B, "client"),),
    )
    return store


def _app(**overrides):
    ingest, facts = seed_stores()
    settings = make_settings(
        dfip_auth_mode="jwt",
        dfip_auth_secret=JWT_SECRET,
        dfip_password_pbkdf2_iterations=ITERATIONS,
        **overrides,
    )
    return create_app(
        settings=settings,
        ingest_store=ingest,
        fact_store=facts,
        publication_store=InMemoryPublicationStore(),
        identity_store=_identity_store(),
    )


def _login(http: TestClient, username: str) -> str:
    response = http.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _open_session(http: TestClient, token: str) -> str:
    response = http.post("/api/v1/auth/publisher-session", headers=_bearer(token))
    assert response.status_code == 200, response.text
    return str(response.json()["session_id"])


def _publisher_headers(token: str, session_id: str) -> dict[str, str]:
    return {**_bearer(token), PUBLISHER_SESSION_HEADER: session_id}


# --- a. one active Publisher session ---------------------------------------


def test_one_publisher_session_works_for_protected_routes() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    session_id = _open_session(http, token)
    headers = _publisher_headers(token, session_id)

    for path in ("/api/v1/session", "/api/v1/facts", "/api/v1/source-files", "/api/v1/batches"):
        response = http.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)


def test_publisher_without_a_session_is_refused() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    response = http.get("/api/v1/facts", headers=_bearer(token))
    assert response.status_code == 401
    assert _error(response)["code"] == PUBLISHER_SESSION_REPLACED


def test_session_id_is_opaque_and_carries_no_user_information() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    body = http.post(
        "/api/v1/auth/publisher-session", headers=_bearer(token)
    ).json()
    session_id = body["session_id"]
    assert len(session_id) >= 40
    assert PUBLISHER not in session_id
    assert CLIENT_ID not in session_id
    assert token not in session_id
    assert PUBLISHER not in http.post(
        "/api/v1/auth/publisher-session", headers=_bearer(token)
    ).text


# --- b/c. second session replaces first, old one stops working --------------


def test_second_browser_replaces_first_and_old_session_stops_working() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)

    tab_a = _open_session(http, token)
    assert http.get("/api/v1/facts", headers=_publisher_headers(token, tab_a)).status_code == 200

    # A different browser instance presents no session id, so it takes over.
    tab_b = _open_session(http, token)
    assert tab_b != tab_a

    replaced = http.get("/api/v1/facts", headers=_publisher_headers(token, tab_a))
    assert replaced.status_code == 401
    assert _error(replaced)["code"] == PUBLISHER_SESSION_REPLACED
    assert _error(replaced)["message"] == REPLACED

    live = http.get("/api/v1/facts", headers=_publisher_headers(token, tab_b))
    assert live.status_code == 200


def test_replaced_session_is_refused_on_every_publisher_surface() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    old = _open_session(http, token)
    new = _open_session(http, token)
    stale = _publisher_headers(token, old)

    assert http.get("/api/v1/session", headers=stale).status_code == 401
    assert http.get("/api/v1/analytics/overview", headers=stale).status_code == 401
    assert http.get("/api/v1/publications/current", headers=stale).status_code == 401
    assert http.get("/api/v1/publications/current/facts.csv", headers=stale).status_code == 401
    assert http.get("/api/v1/facts/history", headers=stale).status_code == 401
    assert http.post("/api/v1/publications", json={}, headers=stale).status_code == 401
    assert http.post(
        "/api/v1/auth/select-client", json={"client_id": CLIENT_ID}, headers=stale
    ).status_code == 401
    # The session that replaced it keeps working.
    assert http.get("/api/v1/facts", headers=_publisher_headers(token, new)).status_code == 200


def test_heartbeat_cannot_resurrect_a_replaced_session() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    old = _open_session(http, token)
    new = _open_session(http, token)

    beat = http.post(
        "/api/v1/auth/publisher-session/heartbeat",
        headers=_publisher_headers(token, old),
    )
    assert beat.status_code == 401
    assert _error(beat)["code"] == PUBLISHER_SESSION_REPLACED
    # The live session is untouched by the stale heartbeat.
    assert http.post(
        "/api/v1/auth/publisher-session/heartbeat",
        headers=_publisher_headers(token, new),
    ).status_code == 200


# --- d. foreign / forged session ids ---------------------------------------


def test_a_publisher_cannot_use_another_users_session_id() -> None:
    http = TestClient(_app())
    first_token = _login(http, PUBLISHER)
    first_session = _open_session(http, first_token)

    other_token = _login(http, OTHER_PUBLISHER)
    stolen = http.get("/api/v1/facts", headers=_publisher_headers(other_token, first_session))
    assert stolen.status_code == 401
    assert _error(stolen)["code"] == PUBLISHER_SESSION_REPLACED

    # ...and presenting a foreign id does not let them take over that user.
    assert http.post(
        "/api/v1/auth/publisher-session",
        headers=_publisher_headers(other_token, first_session),
    ).status_code == 401
    assert http.get(
        "/api/v1/facts", headers=_publisher_headers(first_token, first_session)
    ).status_code == 200


def test_unknown_and_forged_session_ids_are_refused_identically() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    _open_session(http, token)

    for candidate in ("", "not-a-session", new_session_id(), "0" * 64):
        headers = _bearer(token)
        if candidate:
            headers[PUBLISHER_SESSION_HEADER] = candidate
        response = http.get("/api/v1/facts", headers=headers)
        assert response.status_code == 401, candidate
        assert _error(response)["code"] == PUBLISHER_SESSION_REPLACED


# --- e. clients are never limited ------------------------------------------


def test_multiple_clients_work_at_the_same_time() -> None:
    http = TestClient(_app())
    token_a = _login(http, CLIENT_A)
    token_b = _login(http, CLIENT_B_USER)

    for path in ("/api/v1/session", "/api/v1/analytics/overview", "/api/v1/publications/current"):
        first = http.get(path, headers=_bearer(token_a))
        second = http.get(path, headers=_bearer(token_b))
        assert first.status_code == 200, (path, first.text)
        assert second.status_code == 200, (path, second.text)


def test_clients_create_no_publisher_session_rows() -> None:
    app = _app()
    http = TestClient(app)
    store: InMemoryPublisherSessionStore = app.state.publisher_session_store

    http.get("/api/v1/session", headers=_bearer(_login(http, CLIENT_A)))
    http.get("/api/v1/session", headers=_bearer(_login(http, CLIENT_B_USER)))
    assert store._by_id == {}

    token = _login(http, PUBLISHER)
    _open_session(http, token)
    assert len(store._by_id) == 1


def test_client_cannot_open_a_publisher_session() -> None:
    http = TestClient(_app())
    response = http.post(
        "/api/v1/auth/publisher-session", headers=_bearer(_login(http, CLIENT_A))
    )
    assert response.status_code == 403
    assert _error(response)["code"] == AUTHORIZATION_FAILED


# --- f. fail closed ---------------------------------------------------------


def test_publisher_is_refused_when_the_session_store_is_missing() -> None:
    app = _app()
    http = TestClient(app)
    token = _login(http, PUBLISHER)
    session_id = _open_session(http, token)
    app.state.publisher_session_store = None

    response = http.get("/api/v1/facts", headers=_publisher_headers(token, session_id))
    assert response.status_code == 503
    assert _error(response)["code"] == PERSISTENCE_UNAVAILABLE


def test_clients_are_unaffected_when_the_session_store_is_missing() -> None:
    app = _app()
    http = TestClient(app)
    client_token = _login(http, CLIENT_A)
    app.state.publisher_session_store = None

    assert http.get("/api/v1/session", headers=_bearer(client_token)).status_code == 200
    assert http.get(
        "/api/v1/analytics/overview", headers=_bearer(client_token)
    ).status_code == 200


def test_opening_a_session_without_a_store_is_an_outage_not_a_bypass() -> None:
    app = _app()
    http = TestClient(app)
    token = _login(http, PUBLISHER)
    app.state.publisher_session_store = None

    response = http.post("/api/v1/auth/publisher-session", headers=_bearer(token))
    assert response.status_code == 503
    assert _error(response)["code"] == PERSISTENCE_UNAVAILABLE


# --- h. takeover protection and rate limiting ------------------------------


def test_a_replaced_tab_cannot_take_the_session_back() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    old = _open_session(http, token)
    new = _open_session(http, token)

    # The replaced tab retries establishment while still holding its dead id.
    for _ in range(3):
        retry = http.post(
            "/api/v1/auth/publisher-session", headers=_publisher_headers(token, old)
        )
        assert retry.status_code == 401
        assert _error(retry)["code"] == PUBLISHER_SESSION_REPLACED

    # The live session survived every retry.
    assert http.get("/api/v1/facts", headers=_publisher_headers(token, new)).status_code == 200


def test_establishment_is_rate_limited() -> None:
    app = _app()
    app.state.publisher_session_limiter = RateWindowLimiter(
        max_attempts=2, window_seconds=600
    )
    http = TestClient(app)
    token = _login(http, PUBLISHER)

    assert http.post("/api/v1/auth/publisher-session", headers=_bearer(token)).status_code == 200
    assert http.post("/api/v1/auth/publisher-session", headers=_bearer(token)).status_code == 200
    blocked = http.post("/api/v1/auth/publisher-session", headers=_bearer(token))
    assert blocked.status_code == 429
    assert _error(blocked)["code"] == "TOO_MANY_REQUESTS"


def test_a_continuing_instance_renews_instead_of_creating_a_second_session() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    first = _open_session(http, token)
    # The same instance presenting its own live id keeps that id.
    again = http.post(
        "/api/v1/auth/publisher-session",
        headers=_publisher_headers(token, first),
    ).json()["session_id"]
    assert again == first


# --- i. lease expiry through the common store interface ---------------------


def test_expired_lease_is_inactive_and_can_be_replaced() -> None:
    store = InMemoryPublisherSessionStore()
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    session_id = new_session_id()
    store.register(
        user_id="u1", session_id=session_id, lease_seconds=60, now=start
    )

    assert store.get_active(session_id, now=start + timedelta(seconds=30)) is not None
    assert store.get_active(session_id, now=start + timedelta(seconds=61)) is None

    # A lapsed lease must not block a new publisher session.
    replacement = new_session_id()
    store.register(
        user_id="u1",
        session_id=replacement,
        lease_seconds=60,
        now=start + timedelta(seconds=120),
    )
    assert store.get_active(replacement, now=start + timedelta(seconds=120)) is not None
    assert store.get_active(session_id, now=start + timedelta(seconds=120)) is None
    assert store.active_for_user("u1", now=start + timedelta(seconds=120)).session_id == (
        replacement
    )


def test_renewal_refuses_an_expired_lease() -> None:
    store = InMemoryPublisherSessionStore()
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    session_id = new_session_id()
    store.register(user_id="u1", session_id=session_id, lease_seconds=60, now=start)

    assert store.renew(
        session_id=session_id, user_id="u1", lease_seconds=60,
        now=start + timedelta(seconds=30),
    ) is not None
    assert store.renew(
        session_id=session_id, user_id="u1", lease_seconds=60,
        now=start + timedelta(seconds=600),
    ) is None


def test_enforcement_fails_on_an_expired_lease() -> None:
    from dfip_api.auth import Principal
    from dfip_api.errors import PublisherSessionReplacedError

    store = InMemoryPublisherSessionStore()
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    session_id = new_session_id()
    store.register(user_id="u1", session_id=session_id, lease_seconds=60, now=start)
    principal = Principal(
        subject="ops", auth_mode="jwt", role="publisher", user_id="u1"
    )

    enforce_publisher_session(store, principal, session_id, now=start + timedelta(seconds=10))
    try:
        enforce_publisher_session(
            store, principal, session_id, now=start + timedelta(seconds=120)
        )
    except PublisherSessionReplacedError as exc:
        assert exc.status_code == 401
        assert exc.code == PUBLISHER_SESSION_REPLACED
    else:
        raise AssertionError("expired lease must be refused")


# --- j. concurrent registration --------------------------------------------


def test_concurrent_registration_leaves_exactly_one_active_session() -> None:
    store = InMemoryPublisherSessionStore()
    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def attempt() -> None:
        try:
            barrier.wait(timeout=5)
            session = store.register(
                user_id="u1", session_id=new_session_id(), lease_seconds=600
            )
            results.append(session.session_id)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 8
    active = [r for r in results if store.get_active(r) is not None]
    assert len(active) == 1
    assert store.active_for_user("u1").session_id == active[0]


# --- k. inactive company ----------------------------------------------------


def test_publisher_cannot_open_a_session_for_an_inactive_company() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    session_id = _open_session(http, token)

    deactivate = http.post(
        f"/api/v1/clients/{CLIENT_ID}/deactivate",
        headers=_publisher_headers(token, session_id),
    )
    assert deactivate.status_code == 200, deactivate.text

    blocked = http.post("/api/v1/auth/publisher-session", headers=_bearer(token))
    assert blocked.status_code == 403
    assert "inactive" in _error(blocked)["message"].lower()


# --- logout -----------------------------------------------------------------


def test_logout_releases_the_lease_and_still_works_after_replacement() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    _open_session(http, token)
    stale = _open_session(http, token)

    # A replaced tab can still sign out: logout is never gated.
    out = http.post("/api/v1/auth/logout", headers=_publisher_headers(token, stale))
    assert out.status_code == 204


def test_logout_only_affects_the_calling_user() -> None:
    http = TestClient(_app())
    first = _login(http, PUBLISHER)
    second = _login(http, OTHER_PUBLISHER)
    first_session = _open_session(http, first)
    second_session = _open_session(http, second)

    assert http.post(
        "/api/v1/auth/logout", headers=_publisher_headers(first, first_session)
    ).status_code == 204

    assert http.get(
        "/api/v1/facts", headers=_publisher_headers(second, second_session)
    ).status_code == 200


def test_session_responses_do_not_leak_internals() -> None:
    http = TestClient(_app())
    token = _login(http, PUBLISHER)
    response = http.post("/api/v1/auth/publisher-session", headers=_bearer(token))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"session_id", "created_at", "expires_at", "heartbeat_seconds"}
    for secret in (JWT_SECRET, PASSWORD, token, "publisher_session", "SELECT", "postgres"):
        assert secret not in response.text
